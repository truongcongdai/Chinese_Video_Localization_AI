"""Owner-scoped reup automation and short-preview HTTP adapters."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import threading
from typing import Any, Awaitable, Callable, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from universal_video_ai.channel_agent.production_publishing import YouTubeResumablePublisher
from universal_video_ai.channel_agent.youtube import GoogleOAuthTokenService, YouTubeReadOnlyError, YouTubeReadOnlyService
from universal_video_ai.publishing.llm import PublishingLLMClient, PublishingLLMConfig
from universal_video_ai.publishing.models import PublishingPackConfig
from universal_video_ai.publishing.service import PublishingPackService
from universal_video_ai.remix.shorts import ShortPlan, build_short_plan, remap_segments, render_short
from universal_video_ai.render.prepublish import inspect_for_publish, prepublish_report_to_dict
from universal_video_ai.reup.discovery import SourceDiscovery, discovery_issue
from universal_video_ai.reup.source_sessions import SOURCE_HOME, SourceSessions
from universal_video_ai.reup.service import ReupRepository
from universal_video_ai.reup.strategy import ReupStrategyService
from universal_video_ai.segment import TranscriptSegment
from universal_video_ai.web.auth import get_current_user_id
from universal_video_ai.web.store import Store

__all__ = ["ReupProfileBody", "ShortRequest", "ReupRuntime", "install_reup_routes"]
_logger = logging.getLogger(__name__)


class ReupProfileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    topic: str = Field(default="", max_length=300)
    strategy_mode: Literal["auto", "manual"] = "manual"
    strategy_direction_id: str = Field(default="", max_length=100)
    market_region: str = Field(default="VN", pattern=r"^[A-Z]{2}$")
    channel_id: str = Field(default="", max_length=100)
    keywords: list[str] = Field(default_factory=list, max_length=15)
    exclude_keywords: list[str] = Field(default_factory=list, max_length=30)
    platforms: list[Literal["youtube", "bilibili", "douyin", "tiktok"]] = Field(default_factory=list, max_length=4)
    source_urls: list[str] = Field(default_factory=list, max_length=20)
    target_language: str = Field(default="vi", max_length=12)
    min_seconds: int = Field(default=0, ge=0, le=7200)
    max_seconds: int = Field(default=3600, ge=1, le=28800)
    daily_limit: int = Field(default=1, ge=1, le=10)
    scan_interval_minutes: int = Field(default=60, ge=15, le=1440)
    automation: Literal["manual", "assisted", "auto"] = "assisted"
    publish_mode: Literal["private", "immediate", "schedule"] = "private"
    timezone: str = "Asia/Bangkok"
    publish_time: str = "18:00"
    publish_spacing_minutes: int = Field(default=120, ge=15, le=1440)
    rights_confirmed: bool = False
    speaker_mode: Literal["single", "manual", "auto"] = "single"
    speaker_voices: dict[str, str] = Field(default_factory=dict, max_length=16)
    make_short: bool = False
    short_seconds: int = Field(default=60, ge=10, le=180)
    llm_model: str = Field(default="", max_length=200)
    metadata_provider: Literal["none", "ollama"] = "none"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (KeyError, ValueError) as exc:
            raise ValueError("Unknown timezone.") from exc
        return value

    @field_validator("publish_time")
    @classmethod
    def valid_time(cls, value: str) -> str:
        try:
            datetime.strptime(value, "%H:%M")
        except ValueError as exc:
            raise ValueError("Publish time must be HH:MM.") from exc
        return value

    @field_validator("source_urls")
    @classmethod
    def valid_sources(cls, values: list[str]) -> list[str]:
        from urllib.parse import urlparse
        allowed = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "bilibili.com",
                   "www.bilibili.com", "space.bilibili.com", "douyin.com", "www.douyin.com",
                   "tiktok.com", "www.tiktok.com", "m.tiktok.com"}
        for value in values:
            parsed = urlparse(value)
            if len(value) > 2048 or parsed.scheme != "https" or parsed.hostname not in allowed or parsed.username or parsed.password:
                raise ValueError("Use HTTPS source video/channel URLs from YouTube, Bilibili, TikTok or Douyin.")
        return values

    @model_validator(mode="after")
    def valid_profile(self) -> ReupProfileBody:
        if self.strategy_mode == "manual" and not self.topic.strip() and not self.source_urls:
            raise ValueError("Dán link nguồn, nhập chủ đề hoặc chọn tự phân tích kênh và thị trường.")
        if self.max_seconds < self.min_seconds:
            raise ValueError("Maximum duration must be >= minimum duration.")
        if self.automation == "auto" and not self.rights_confirmed:
            raise ValueError("Confirm source usage rights before enabling automatic publishing.")
        if self.automation == "auto" and self.speaker_mode == "manual":
            raise ValueError("Manual speaker assignment requires assisted or manual mode.")
        if self.metadata_provider == "ollama" and not self.llm_model.strip():
            raise ValueError("Choose an installed Ollama model for AI metadata.")
        if self.enabled and self.make_short and not self.llm_model.strip():
            raise ValueError("Choose an installed local model for automatic summary shorts.")
        if self.speaker_mode == "auto" and any(not self.speaker_voices.get(f"speaker_{i}", "").strip() for i in range(2)):
            raise ValueError("Assign voices to speaker_0 and speaker_1 before enabling automatic speaker detection.")
        if self.enabled and not self.platforms and not self.source_urls:
            raise ValueError("Choose at least one search platform or source URL before enabling periodic scanning.")
        return self


class ShortRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_seconds: int = Field(default=60, ge=10, le=180)
    llm_model: str = Field(default="", max_length=200)


class CutsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: dict[str, Any]

    @field_validator("plan")
    @classmethod
    def bounded_cuts(cls, value: dict) -> dict:
        cuts = value.get("cuts")
        if not isinstance(cuts, list) or not 1 <= len(cuts) <= 30:
            raise ValueError("Select between 1 and 30 cuts.")
        return value


def _duration(video: Path) -> float:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video)],
                            capture_output=True, text=True, check=True, timeout=30)
    return float(json.loads(result.stdout)["format"]["duration"])


def _selector(model: str) -> PublishingLLMClient | None:
    from universal_video_ai.provider_runtime import default_provider_mode, ProviderMode
    if default_provider_mode() is ProviderMode.MOCK:
        return None
    selected = model.strip() or os.getenv("OLLAMA_MODEL", "").strip()
    if not selected:
        return None
    return PublishingLLMClient(PublishingLLMConfig(provider="ollama", model=selected, timeout_seconds=90,
                               base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
                               ollama_keep_alive=60, ollama_think=False, ollama_num_predict=1400))


class ReupRuntime:
    """One application process owns workers; durable checkpoints survive restart."""
    def __init__(self, store: Store, submit: Callable[[dict, int, str], Awaitable[dict]], output_dir: Path) -> None:
        self.store, self.submit, self.output_dir = store, submit, output_dir
        self.repository = ReupRepository(store)
        self.source_sessions = SourceSessions(store.db_path.parent / "source_sessions")
        self.discovery = SourceDiscovery(sessions=self.source_sessions)
        self.publisher = YouTubeResumablePublisher(GoogleOAuthTokenService(store))
        self.metrics_client = YouTubeReadOnlyService(GoogleOAuthTokenService(store))
        self.strategy = ReupStrategyService(store, self.metrics_client, self.discovery)
        self.strategy_tasks: dict[int, asyncio.Task] = {}
        self.scan_tasks: dict[int, asyncio.Task] = {}
        self.source_check_tasks: dict[tuple[int, str], asyncio.Task] = {}
        self.background_tasks: set[asyncio.Task] = set()
        self.lock = asyncio.Lock()
        self.short_lock = threading.Lock()
        self.retry_localization = None

    def _background(self, operation: Awaitable[Any]) -> asyncio.Task:
        task = asyncio.create_task(operation)
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)
        return task

    def source_status(self, owner: int, platform: str) -> dict:
        task = self.source_check_tasks.get((owner, platform))
        return {**self.source_sessions.status(owner, platform), "checking": bool(task and not task.done())}

    def start_source_check(self, owner: int, platform: str) -> dict:
        task = self.source_check_tasks.get((owner, platform))
        if task is None or task.done():
            self.source_check_tasks[(owner, platform)] = asyncio.create_task(self._check_source(owner, platform))
        return self.source_status(owner, platform)

    async def _check_source(self, owner: int, platform: str) -> None:
        query = self.source_query(owner, platform)
        self.discovery.reset(owner, platform)
        def work():
            with self.discovery.user_scope(owner):
                directory = self.source_sessions.directory(owner, platform)
                try:
                    found = self.discovery.search(platform, query, limit=3)
                    if found:
                        self.source_sessions.save_search(owner, platform, query, [row.to_dict() for row in found], directory=directory)
                    issue = {"status": "searched", "message": f"Đã kiểm tra và nhận được {len(found)} video. Có thể tìm video lại."} if found else {
                        "status": "no_results", "message": "Đã kiểm tra phiên nhưng chưa nhận được video. Giữ phiên hiện tại; kiểm tra trang nguồn hoặc thử từ khóa khác."}
                    count = len(found)
                except Exception as error:
                    issue, count = discovery_issue(error), 0
                if self.source_sessions.directory(owner, platform) != directory:
                    return
                self.source_sessions.record_access(owner, platform, issue, count=count, query=query,
                                                   signature=self.source_sessions.signature(owner, platform))
        try:
            await asyncio.wait_for(asyncio.to_thread(work), timeout=60)
        except TimeoutError:
            self.source_sessions.record_access(owner, platform, {"status": "timeout", "message": "Kiểm tra nguồn quá lâu. Phiên được giữ; tải lại trang nguồn hoặc kiểm tra sau."}, query=query)

    def source_query(self, owner: int, platform: str) -> str:
        config = self.repository.profile(owner) or {}
        report = self.strategy.status(owner).get("report") or {}
        directions = report.get("directions", [])
        chosen = next((row for row in directions if row.get("id") == config.get("strategy_direction_id")), directions[0] if directions else {})
        queries = chosen.get("search_queries", {}).get(platform) or []
        if not queries:
            state = self.repository.discovery(owner)
            previous = next((row for row in state.get("platform_status", []) if row.get("platform") == platform), {})
            queries = previous.get("queries") or []
        return str(queries[0])[:200] if queries else {"douyin": "穿搭", "tiktok": "street dance", "bilibili": "舞蹈"}[platform]

    async def scan(self, user_id: int, *, automatic: bool = False) -> dict:
        task = self.scan_tasks.get(user_id)
        if task is None or task.done():
            with self.discovery.user_scope(user_id), self.source_sessions.download_scope(user_id):
                task = asyncio.create_task(self._scan(user_id, automatic=automatic))
            self.scan_tasks[user_id] = task
        return await asyncio.shield(task)

    async def _scan(self, user_id: int, *, automatic: bool = False) -> dict:
        config = self.repository.profile(user_id)
        if not config:
            raise ValueError("Chưa có cấu hình nguồn. Chọn kênh và nguồn video rồi bấm bắt đầu; cấu hình sẽ được lưu tự động.")
        saved_config = config
        report = None
        if config.get("strategy_mode") == "auto":
            state = await self.analyze_strategy(user_id)
            report = state["report"]
            if self.strategy.config_key(self.repository.profile(user_id)) != self.strategy.config_key(config):
                raise ValueError("Cấu hình đã thay đổi khi đang phân tích. Bấm tìm nguồn lại để dùng cấu hình mới.")
            directions = report["directions"]
            selected = config.get("strategy_direction_id")
            direction = next((row for row in directions if row["id"] == selected), None) if selected else directions[0]
            if direction is None:
                raise ValueError("Hướng đã chọn không còn trong phân tích mới. Chọn lại hướng hoặc để Agent tự chọn.")
            seeds = [*direction.get("social_evidence", []), *direction.get("market_evidence", [])]
            if report["mode"] == "new_channel":
                seeds.extend(direction.get("evidence", []))
            config = {**config, "search_version": 4, "channel_id": report["channel"]["channel_id"], "topic": direction["topic"], "source_urls": [],
                      "keywords": [], "search_queries": direction["search_queries"],
                      "content_profile": direction.get("content_profile"),
                      "seed_candidates": seeds,
                      "strategy_evidence": {"direction_id": direction["id"], "generated_at": report["generated_at"],
                                            "mode": report["mode"], "evidence": direction["evidence"],
                                            "social_evidence": direction.get("social_evidence", []), "plan": direction.get("plan")}}
        result = await asyncio.to_thread(self.discovery.scan, config)
        if self.repository.profile(user_id) != saved_config:
            raise ValueError("Cấu hình vừa thay đổi. Chưa tạo job mới; bấm tìm nguồn lại để dùng cấu hình mới.")
        runs = []
        own_ids = self.strategy.own_video_ids(user_id, config.get("channel_id", "")) if report else set()
        for candidate in result["candidates"]:
            if candidate.get("platform") == "youtube" and candidate.get("video_id") in own_ids:
                continue
            source_evidence = {key: candidate[key] for key in ("url", "platform", "title", "duration", "view_count", "published_at", "like_count", "comment_count", "search_query", "ranking", "relevance") if key in candidate}
            explicit = candidate["url"] in config.get("source_urls", [])
            row = self.repository.enqueue(user_id, candidate, {**config, "source_evidence": source_evidence},
                                          restart_cancelled=not automatic and explicit)
            if row:
                if row["config"].get("automation") != "manual" and row["status"] == "discovered" and candidate.get("duration", 0) > 0:
                    row = self.repository.update(user_id, row["id"], status="queued", refs={**row["refs"], "trigger": "automatic" if automatic else "manual"})
                elif row["status"] == "discovered" and not candidate.get("duration"):
                    result["warnings"].append("Source duration is unknown; automatic processing is paused until its length is verified or it is explicitly selected for manual processing.")
                runs.append(row)
        warnings = list(dict.fromkeys(result["warnings"] + (report["warnings"] if report else [])))
        summary = {"query_version": 4, "completed_at": datetime.now(timezone.utc).timestamp(), "warnings": warnings, "platform_status": result.get("platform_status", []),
                   "videos_found": len(result["candidates"]), "runs_created": len(runs), "run_ids": [row["id"] for row in runs],
                   "channel_id": config.get("channel_id", "")}
        self.repository.save_discovery(user_id, summary)
        return {**summary, "runs": runs, "strategy": report}

    async def analyze_strategy(self, user_id: int, *, force: bool = False) -> dict:
        task = self.strategy_tasks.get(user_id)
        if task is None or task.done():
            async def work() -> dict:
                config = self.repository.profile(user_id)
                if not config:
                    raise ValueError("Lưu cấu hình trước khi phân tích.")
                while True:
                    state = await asyncio.to_thread(self.strategy.analyze, user_id, config, force=force,
                                                    client=_selector(config.get("llm_model", "")))
                    if state["status"] == "ready":
                        return state
                    await asyncio.sleep(0)
            task = asyncio.create_task(work())
            self.strategy_tasks[user_id] = task
        return await asyncio.shield(task)

    def _owned_job(self, user_id: int, job_id: str) -> Any:
        job = self.store.get_job(job_id)
        if not job or job.user_id != user_id:
            raise LookupError("Localization job not found.")
        return job

    def plan_short(self, user_id: int, job_id: str, body: ShortRequest) -> dict:
        job = self._owned_job(user_id, job_id)
        video = Path(job.final_video_path or "")
        if job.status != "done" or not video.is_file():
            raise ValueError("Finish the localized video before creating a short.")
        segments = [TranscriptSegment.from_dict(row) for row in json.loads(job.segments_json or "[]")]
        return build_short_plan(segments, _duration(video), target_seconds=body.target_seconds,
                                client=_selector(body.llm_model)).to_dict()

    def make_short(self, user_id: int, job_id: str, raw: dict) -> dict:
        with self.short_lock:
            return self._make_short(user_id, job_id, raw)

    def _make_short(self, user_id: int, job_id: str, raw: dict) -> dict:
        job = self._owned_job(user_id, job_id)
        source = Path(job.final_video_path or "")
        if job.status != "done" or not source.is_file():
            raise ValueError("Localized video is not ready.")
        plan = ShortPlan.from_dict({**raw, "source_duration": _duration(source)})
        stat = source.stat()
        plan_key = json.dumps({"plan": plan.to_dict(), "source": str(source.resolve()),
                               "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}, sort_keys=True, ensure_ascii=False)
        output = self.output_dir / str(user_id) / job_id / hashlib.sha256(plan_key.encode()).hexdigest()[:32]
        final = output / "short.mp4"
        expected = sum(c.source_end - c.source_start for c in plan.cuts)
        valid_output = False
        if final.is_file() and final.stat().st_size:
            try:
                valid_output = abs(_duration(final) - expected) <= max(1.0, len(plan.cuts) * 0.1)
            except (ValueError, subprocess.SubprocessError, KeyError):
                pass
        if not valid_output:
            final = render_short(source, plan, output)
        if abs(_duration(final) - expected) > max(1.0, len(plan.cuts) * 0.1):
            raise ValueError("Short output duration failed validation.")
        segments = [TranscriptSegment.from_dict(row) for row in json.loads(job.segments_json or "[]")]
        selected = [s.to_dict() for s in remap_segments(segments, plan)]
        (output / "plan.json").write_text(json.dumps(plan.to_dict(), ensure_ascii=False), encoding="utf-8")
        (output / "segments.json").write_text(json.dumps(selected, ensure_ascii=False), encoding="utf-8")
        # A short gets its own metadata package derived from selected events only.
        original_config = PublishingPackConfig.from_dict(job.publishing_config)
        config = PublishingPackConfig.from_dict({**(job.publishing_config or {}), "enabled": True,
                                               "generate_publish_ready_video": False,
                                               "platforms": ["youtube"]})
        manifest = output / "publishing_pack" / "manifest.json"
        completed_pack = False
        if manifest.is_file():
            try:
                completed_pack = json.loads(manifest.read_text(encoding="utf-8")).get("overall_status") == "success"
            except (ValueError, OSError):
                pass
        if completed_pack:
            return {"video_path": str(final), "pack_dir": str(manifest.parent), "short_id": output.name,
                    "plan": plan.to_dict()}
        llm = _selector(original_config.local_model or "") if original_config.provider == "ollama" else None
        pack = PublishingPackService(llm_client=llm).generate(config=config, output_dir=output, final_video_path=final,
                  source_video_path=final, source_url=job.source_url, source_metadata={},
                  translated_segments=selected, source_segments=[], user_id=user_id, job_id=job_id,
                  target_language=job.target_language)
        return {"video_path": str(final), "pack_dir": str(pack.pack_dir), "short_id": output.name,
                "plan": plan.to_dict()}

    def _package(self, user_id: int, run: dict, job: Any) -> dict:
        refs = run["refs"]
        if run["config"].get("make_short") and not refs.get("short_id"):
            if not run["config"].get("llm_model"):
                raise ValueError("Automatic summary shorts require a configured local LLM; use the short preview for manual selection.")
            plan = refs.get("short_plan") or self.plan_short(user_id, job.id, ShortRequest(target_seconds=run["config"]["short_seconds"],
                                                                                       llm_model=run["config"]["llm_model"]))
            refs["short_plan"] = plan
            self.repository.update(user_id, run["id"], status="packaging", refs=refs)
            refs.update(self.make_short(user_id, job.id, plan))
        video = Path(refs.get("video_path") or job.final_video_path or "")
        pack = Path(refs.get("pack_dir") or job.publishing_pack_path or "")
        metadata_path = pack / "youtube_metadata.json"
        if not video.is_file() or not metadata_path.is_file():
            raise ValueError("Reup publishing package is incomplete; retry its failed components from job history.")
        qc = inspect_for_publish(video)
        if not qc.ready:
            raise ValueError("Video QA failed: " + "; ".join(issue.message for issue in qc.issues if issue.severity == "error"))
        refs["qc"] = prepublish_report_to_dict(qc)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        title = str(metadata.get("recommended_title") or "").strip()
        description = str(metadata.get("description") or "").strip()
        title = title[:100].rstrip()
        if not title or not description or len(description) > 5000:
            raise ValueError("Generated YouTube title/description exceeds limits or is empty; edit the package before retry.")
        thumbs = metadata.get("thumbnails") or []
        thumb = pack / str(thumbs[0]) if thumbs else None
        if not thumb or pack.resolve() not in thumb.resolve().parents or not thumb.is_file():
            raise ValueError("YouTube thumbnail is unavailable; retry thumbnail generation.")
        # Compute source fingerprint for cross-platform duplicate protection.
        source = Path(job.source_video_path or "")
        if source.is_file():
            digest = hashlib.sha256()
            with source.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            refs["source_sha256"] = digest.hexdigest()
            for prior in self.repository.list(user_id):
                if prior["id"] != run["id"] and prior["channel_id"] == run["channel_id"] and prior["refs"].get("source_sha256") == refs["source_sha256"]:
                    raise ValueError("Duplicate source file already exists in this channel's reup history.")
        refs.update({"video_path": str(video), "pack_dir": str(pack), "title": title, "description": description,
                     "tags": metadata.get("tags", []), "thumbnail_path": str(thumb)})
        return refs

    def _publish(self, user_id: int, run: dict) -> None:
        config, refs = run["config"], run["refs"]
        if not config.get("rights_confirmed"):
            raise ValueError("Confirm usage rights in the profile before preparing this run.")
        account = self.store.get_social_account(user_id, "youtube")
        if not account or not config.get("channel_id") or account["account_ref"] != config["channel_id"]:
            raise ValueError("Connected YouTube channel does not match this run; reconnect the intended channel.")
        if not refs.get("external_video_id"):
            if not refs.get("upload_session_uri"):
                zone = ZoneInfo(config["timezone"])
                today = datetime.now(zone).date()
                prior_runs = self.repository.list(user_id)
                started_today = sum(
                    1 for row in prior_runs if row["id"] != run["id"] and row["channel_id"] == run["channel_id"]
                    and row["refs"].get("upload_started_at") and datetime.fromisoformat(row["refs"]["upload_started_at"]).astimezone(zone).date() == today
                )
                if started_today >= config["daily_limit"]:
                    self.repository.update(user_id, run["id"], status="ready", refs=refs, error="Daily upload limit reached; waiting for the next local day.")
                    return
                schedule = refs.get("schedule_utc")
                if config["publish_mode"] == "schedule" and not schedule:
                    zone = ZoneInfo(config["timezone"])
                    now = datetime.now(zone)
                    hour, minute = map(int, config["publish_time"].split(":"))
                    when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    if when <= now + timedelta(minutes=5):
                        when += timedelta(days=1)
                    prior_times = [datetime.fromisoformat(row["refs"]["schedule_utc"].replace("Z", "+00:00"))
                                   for row in self.repository.list(user_id) if row["id"] != run["id"] and row["channel_id"] == run["channel_id"]
                                   and row["refs"].get("schedule_utc") and row["status"] != "cancelled"]
                    if prior_times:
                        when = max(when, max(prior_times) + timedelta(minutes=config["publish_spacing_minutes"]))
                    while sum(1 for prior in prior_times if prior.astimezone(zone).date() == when.astimezone(zone).date()) >= config["daily_limit"]:
                        when = (when.astimezone(zone) + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
                    schedule = when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                video = Path(refs["video_path"])
                job = {**refs, "privacy": "public" if config["publish_mode"] == "immediate" else "private",
                       "schedule_utc": schedule, "upload_size": video.stat().st_size}
                refs["schedule_utc"] = schedule
                refs["upload_started_at"] = datetime.now(timezone.utc).isoformat()
                refs["upload_session_uri"] = self.publisher.begin(user_id, job)
                self.repository.update(user_id, run["id"], status="publishing", refs=refs)
            payload = self.publisher.upload(user_id, refs["upload_session_uri"], refs["video_path"], Path(refs["video_path"]).stat().st_size)
            refs["external_video_id"] = str(payload["id"])
            refs["external_url"] = f"https://www.youtube.com/watch?v={payload['id']}"
            self.repository.update(user_id, run["id"], status="processing", refs=refs)
        if refs.get("thumbnail_status") != "uploaded":
            self.publisher.thumbnail(user_id, refs["external_video_id"], refs["thumbnail_path"])
            refs["thumbnail_status"] = "uploaded"
            self.repository.update(user_id, run["id"], status="processing", refs=refs)
        remote = self.publisher.status(user_id, refs["external_video_id"])
        status = remote.get("status") or {}
        processing = (remote.get("processingDetails") or {}).get("processingStatus")
        if processing == "failed" or status.get("uploadStatus") in {"failed", "rejected", "deleted"}:
            raise ValueError("YouTube rejected or failed to process the upload.")
        local = "processing"
        if status.get("privacyStatus") in {"public", "unlisted"} and processing in {None, "succeeded"} and status.get("uploadStatus") in {"processed", "uploaded"}:
            local = "published"
        elif status.get("privacyStatus") == "private":
            publish_at = status.get("publishAt")
            if publish_at and datetime.fromisoformat(publish_at.replace("Z", "+00:00")) > datetime.now(timezone.utc):
                local = "scheduled"
            elif processing in {None, "succeeded"} and status.get("uploadStatus") in {"processed", "uploaded"}:
                local = "uploaded_private"
        warning = None
        if local == "uploaded_private" and config["publish_mode"] != "private":
            warning = "YouTube still reports private. Verify the schedule and publishing permissions in YouTube Studio."
        self.repository.update(user_id, run["id"], status=local, refs=refs, error=warning)

    async def advance(self, user_id: int, run_id: str) -> dict:
        async with self.lock:
            run = self.repository.get(user_id, run_id, private=True)
            try:
                if run["status"] == "queued":
                    config = run["config"]
                    if config["speaker_mode"] == "auto":
                        from universal_video_ai.speech.diarization import diarization_readiness
                        if not diarization_readiness()["ready"]:
                            raise ValueError("Automatic speaker detection is not configured. Install pyannote.audio and set DIARIZATION_MODEL_PATH, or switch to manual speaker assignment.")
                    body = {"url": run["source_url"], "target_language": config["target_language"],
                            "max_concurrent": 1, "processing_mode": "fast", "review_before_render": config["speaker_mode"] == "manual",
                            "speaker_config": {"mode": config["speaker_mode"], "voices": config["speaker_voices"], "max_speakers": 2},
                            "publishing_config": {"enabled": True, "platforms": ["youtube"], "provider": config.get("metadata_provider", "none"),
                                                  "channel_name": (self.store.get_social_account(user_id, "youtube") or {}).get("account_name") or config.get("channel_id") or "Kênh YouTube",
                                                  "local_model": config.get("llm_model") or None,
                                                  "generate_publish_ready_video": False,
                                                  "custom_instructions": f"Channel topic: {config['topic']}"}}
                    job = await self.submit(body, user_id, self.repository.localization_id(run))
                    return self.repository.update(user_id, run_id, status="localizing", job_id=job["id"])
                if run["status"] == "localizing":
                    job = self._owned_job(user_id, run["job_id"])
                    if job.status in {"error", "cancelled"}:
                        error = str(job.error or "").lower()
                        if "khởi động lại" in error:
                            detail = "Video bị gián đoạn khi khởi động lại server. Bấm Thử lại trên thẻ này để tiếp tục job liên kết."
                        elif "douyin" in error and any(marker in error for marker in ("cookie", "phiên", "403", "xác minh")):
                            detail = "Douyin chưa cho tải video trong phiên hiện tại. Bấm Kiểm tra video nguồn, hoàn tất yêu cầu nếu có, rồi bấm Thử lại trên thẻ này."
                        else:
                            detail = "Job xử lý đã dừng. Kiểm tra chi tiết trong Lịch sử; bấm Thử lại trên thẻ này để chạy lại job liên kết."
                        _logger.info("Reup run %s waiting for user retry: linked job %s is %s", run_id, job.id, job.status)
                        return self.repository.update(user_id, run_id, status="failed", error=detail)
                    if job.status == "done":
                        self.repository.update(user_id, run_id, status="packaging")
                        refs = await asyncio.to_thread(self._package, user_id, run, job)
                        return self.repository.update(user_id, run_id, status="ready", refs=refs)
                if run["status"] == "packaging":
                    refs = await asyncio.to_thread(self._package, user_id, run, self._owned_job(user_id, run["job_id"]))
                    return self.repository.update(user_id, run_id, status="ready", refs=refs)
                if run["status"] == "ready" and not run["approved"] and run["config"]["automation"] != "auto":
                    return self.repository.get(user_id, run_id)
                if run["status"] in {"ready", "publishing", "processing", "scheduled"}:
                    await asyncio.to_thread(self._publish, user_id, run)
            except Exception as exc:
                _logger.exception("Reup run %s failed", run_id)
                # Do not persist provider payloads, OAuth tokens or session URLs in error text.
                from universal_video_ai.channel_agent.production_publishing import ProductionPublishingError
                detail = str(exc)[:500] if isinstance(exc, (ValueError, ProductionPublishingError)) else (
                    str(exc.detail)[:500] if isinstance(exc, HTTPException) else
                    "Check source access, linked localization job, package and YouTube connection, then retry.")
                self.repository.update(user_id, run_id, status="failed", error=f"{run['status']}: {detail}")
            return self.repository.get(user_id, run_id)

    def refresh_metrics(self, user_id: int, run_id: str) -> dict:
        run = self.repository.get(user_id, run_id, private=True)
        refs = run["refs"]
        if not refs.get("external_video_id"):
            raise ValueError("Upload the video before refreshing metrics.")
        payload = self.metrics_client.data_request(user_id, "videos", {
            "part": "statistics,snippet", "id": refs["external_video_id"], "maxResults": 1,
        })
        items = payload.get("items") or []
        if not items or (items[0].get("snippet") or {}).get("channelId") != run["channel_id"]:
            raise ValueError("YouTube did not return this run's video under its expected channel.")
        statistics = items[0].get("statistics") or {}
        snapshot = {"observed_at": datetime.now(timezone.utc).isoformat()}
        for name, field in (("views", "viewCount"), ("likes", "likeCount"), ("comments", "commentCount")):
            value = statistics.get(field)
            snapshot[name] = int(value) if isinstance(value, (str, int)) and str(value).isdigit() else None
        refs["metrics"] = snapshot
        refs["metrics_history"] = [*(refs.get("metrics_history") or [])[-29:], snapshot]
        refs.pop("metrics_error", None)
        return self.repository.update(user_id, run_id, refs=refs, status=run["status"])

    def _package_asset(self, user_id: int, run_id: str) -> tuple[Path, Path]:
        run = self.repository.get(user_id, run_id)
        pack = Path(run["refs"].get("pack_dir") or "").resolve()
        thumbnail = Path(run["refs"].get("thumbnail_path") or "").resolve()
        if pack.is_dir() and pack in thumbnail.parents and thumbnail.is_file():
            return pack, thumbnail
        raise LookupError("Publishing package is unavailable.")

    def package_preview(self, user_id: int, run_id: str) -> dict:
        pack, _ = self._package_asset(user_id, run_id)
        metadata = json.loads((pack / "youtube_metadata.json").read_text(encoding="utf-8"))
        hashtags = metadata.get("hashtags") or []
        tags = metadata.get("tags") or []
        return {"title": str(metadata.get("recommended_title") or "")[:100],
                "description": str(metadata.get("description") or "")[:5000],
                "hashtags": [str(value)[:100] for value in hashtags[:10]] if isinstance(hashtags, list) else [],
                "tags": [str(value)[:100] for value in tags[:20]] if isinstance(tags, list) else [],
                "thumbnail_url": f"/api/reup-agent/runs/{run_id}/thumbnail"}

    async def loop(self) -> None:
        while True:
            try:
                for user_id in self.strategy.pending():
                    prior = self.strategy_tasks.get(user_id)
                    if prior and not prior.done():
                        continue
                    async def resume(owner: int = user_id) -> None:
                        try:
                            await self.analyze_strategy(owner)
                        except Exception:
                            _logger.warning("Reup analysis resume failed for user %s", owner)
                    self._background(resume())
                for user_id, _ in self.repository.due_profiles(datetime.now().timestamp()):
                    async def discover(owner: int = user_id) -> None:
                        try:
                            await self.scan(owner, automatic=True)
                        except Exception:
                            _logger.warning("Periodic reup discovery failed for user %s", owner)
                    self._background(discover())
                for user_id, run_id in self.repository.active():
                    run = self.repository.get(user_id, run_id)
                    if run["refs"].get("trigger") == "automatic" and not self.repository.profile(user_id).get("enabled"):
                        continue
                    await self.advance(user_id, run_id)
                for user_id, run_id in self.repository.due_metrics(datetime.now().timestamp()):
                    async with self.lock:
                        try:
                            await asyncio.to_thread(self.refresh_metrics, user_id, run_id)
                        except Exception:
                            run = self.repository.get(user_id, run_id, private=True)
                            refs = run["refs"]
                            refs["metrics_error"] = "Metrics unavailable; check YouTube read permissions."
                            self.repository.update(user_id, run_id, refs=refs)
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("Reup worker cycle failed")
            await asyncio.sleep(30)


def install_reup_routes(app: FastAPI, store: Store, submit: Callable, output_dir: Path, *, retry_localization: Callable | None = None) -> ReupRuntime:
    runtime = ReupRuntime(store, submit, output_dir)
    runtime.retry_localization = retry_localization
    router = APIRouter(prefix="/api/reup-agent", tags=["Reup Agent"])

    @router.get("/profile")
    def profile(user_id: int = Depends(get_current_user_id)) -> dict:
        value = runtime.repository.profile(user_id)
        account = store.get_social_account(user_id, "youtube")
        return {**value, "channel_id": account["account_ref"] or ""} if account else value

    @router.get("/source-sessions")
    async def source_sessions(user_id: int = Depends(get_current_user_id)) -> dict:
        return {"owner_id": user_id, "sources": [runtime.source_status(user_id, platform) for platform in SOURCE_HOME]}

    @router.post("/source-sessions/{platform}/{action}")
    async def source_login(platform: Literal["douyin", "tiktok", "bilibili"], action: Literal["login", "switch", "verify", "save", "retry", "check", "reload"],
                     request: Request, user_id: int = Depends(get_current_user_id)) -> dict:
        try:
            if action in {"login", "switch", "verify"}:
                if not request.client or request.client.host not in {"127.0.0.1", "::1"}:
                    raise HTTPException(400, "Mở Reup Agent tại localhost trên máy chạy web để đăng nhập nguồn bằng cửa sổ trình duyệt.")
                if action == "verify":
                    return runtime.source_sessions.start(user_id, platform, query=runtime.source_query(user_id, platform))
                return runtime.source_sessions.start(user_id, platform, switch=True) if action == "switch" else runtime.source_sessions.start(user_id, platform)
            if action == "save":
                return runtime.source_sessions.finish(user_id, platform)
            if action == "reload":
                return runtime.source_sessions.control(user_id, platform, "reload")
            if action in {"check", "retry"}:
                return runtime.start_source_check(user_id, platform)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @router.put("/profile")
    def save_profile(body: ReupProfileBody, user_id: int = Depends(get_current_user_id)) -> dict:
        account = store.get_social_account(user_id, "youtube")
        value = body.model_dump()
        if value["enabled"] and value["speaker_mode"] == "auto":
            from universal_video_ai.speech.diarization import diarization_readiness
            if not diarization_readiness()["ready"]:
                raise HTTPException(400, "Automatic speaker detection needs pyannote.audio and a local model. Check the speaker setup panel or choose manual assignment.")
        if account:
            value["channel_id"] = account["account_ref"] or ""
        if value["enabled"] and value["automation"] == "auto" and (
            not account or not value["channel_id"] or account["account_ref"] != value["channel_id"]
        ):
            raise HTTPException(400, "Connect the selected YouTube channel before enabling autonomous upload.")
        return runtime.repository.save_profile(user_id, value)

    @router.post("/scan")
    async def scan(user_id: int = Depends(get_current_user_id)) -> dict:
        try:
            return await runtime.scan(user_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except YouTubeReadOnlyError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc

    @router.get("/strategy")
    def strategy_status(user_id: int = Depends(get_current_user_id)) -> dict:
        state = runtime.strategy.status(user_id)
        account = store.get_social_account(user_id, "youtube")
        if state.get("channel_id") and (not account or account["account_ref"] != state["channel_id"]):
            state = {"status": "not_analyzed", "videos_scanned": 0, "report": None}
        elif state.get("report") and state["report"].get("query_version") != 4:
            state = {"status": "not_analyzed", "videos_scanned": state.get("videos_scanned", 0), "report": None,
                     "message": "Bộ tạo từ khóa nội dung đã cập nhật. Phân tích lại để tự chuẩn bị truy vấn tiếng Trung và lọc video theo nội dung."}
        elif state.get("report"):
            state = runtime.strategy.refresh_keywords(user_id, runtime.repository.profile(user_id) or {})
        task = runtime.strategy_tasks.get(user_id)
        return {**state, "running": bool(task and not task.done())}

    @router.post("/strategy/analyze")
    async def analyze_strategy(user_id: int = Depends(get_current_user_id)) -> dict:
        if not runtime.repository.profile(user_id):
            raise HTTPException(400, "Lưu cấu hình kênh trước khi phân tích.")
        async def analyze() -> None:
            try:
                await runtime.analyze_strategy(user_id, force=True)
            except Exception:
                _logger.warning("Reup strategy analysis failed user_id=%s", user_id)
        runtime._background(analyze())
        return {"started": True}

    @router.get("/runs")
    def runs(user_id: int = Depends(get_current_user_id)) -> list[dict]:
        return runtime.repository.list(user_id)

    @router.get("/discovery")
    def discovery_status(user_id: int = Depends(get_current_user_id)) -> dict:
        state = runtime.repository.discovery(user_id, include_completed_at=True)
        account = store.get_social_account(user_id, "youtube")
        if account and state.get("channel_id") and state["channel_id"] != account["account_ref"]:
            return {}
        for source in state.get("platform_status", []):
            if source.get("status") != "not_requested" and not any(str(query).strip() for query in source.get("queries", [])):
                source.update(status="no_query", message="Lượt này chưa gửi truy vấn tìm kiếm. Từ khóa sẽ được chuẩn bị từ nội dung kênh trước khi tìm lại.")
        return state

    @router.get("/runs/{run_id}/package")
    def package(run_id: str, user_id: int = Depends(get_current_user_id)) -> dict:
        try:
            return runtime.package_preview(user_id, run_id)
        except (LookupError, OSError, ValueError) as exc:
            raise HTTPException(404, "Publishing package is unavailable.") from exc

    @router.get("/runs/{run_id}/thumbnail")
    def thumbnail(run_id: str, user_id: int = Depends(get_current_user_id)) -> FileResponse:
        try:
            _, path = runtime._package_asset(user_id, run_id)
            return FileResponse(path)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.post("/runs/{run_id}/{action}")
    async def mutate(run_id: str, action: Literal["process", "approve", "pause", "resume", "retry", "cancel", "metrics", "verify_source"],
                     request: Request, user_id: int = Depends(get_current_user_id)) -> dict:
        async with runtime.lock:
            try:
                run = runtime.repository.get(user_id, run_id, private=True)
            except LookupError as exc:
                raise HTTPException(404, str(exc)) from exc
            status, refs = run["status"], run["refs"]
            if action == "verify_source":
                if not request.client or request.client.host not in {"127.0.0.1", "::1"}:
                    raise HTTPException(400, "Mở Reup Agent tại localhost trên máy chạy web để kiểm tra video nguồn.")
                from universal_video_ai.reup.discovery import source_identity
                platform, video_id, _ = source_identity(run["source_url"])
                if platform != "douyin":
                    raise HTTPException(400, "Video này không thuộc nguồn Douyin.")
                try:
                    return runtime.source_sessions.start(user_id, platform, video_id=video_id)
                except ValueError as error:
                    raise HTTPException(400, str(error)) from error
            evidence = run["config"].get("source_evidence") or {}
            legacy = run["config"].get("strategy_mode") == "auto" and bool(evidence) and not evidence.get("relevance")
            if legacy and action in {"process", "retry", "resume", "approve"}:
                raise HTTPException(409, "Video này được chọn bằng bộ tìm cũ và chưa qua kiểm tra nội dung. Phân tích lại kênh để tìm video phù hợp; không chạy lại nguồn cũ.")
            if action == "metrics" and status in {"published", "uploaded_private", "scheduled"}:
                try:
                    return await asyncio.to_thread(runtime.refresh_metrics, user_id, run_id)
                except Exception as exc:
                    raise HTTPException(400, "Metrics unavailable; check YouTube read permissions and channel identity.") from exc
            elif action == "process" and status == "cancelled":
                restarted = runtime.repository.enqueue(user_id, {"url": run["source_url"], "title": run["title"]},
                                                        run["config"], restart_cancelled=True)
                if not restarted or restarted["status"] != "discovered":
                    raise HTTPException(409, "Cannot restart this source: check the daily limit and any existing upload.")
                runtime.repository.update(user_id, run_id, status="queued", refs={"trigger": "manual"})
            elif action == "process" and status == "discovered":
                runtime.repository.update(user_id, run_id, status="queued", refs={**refs, "trigger": "manual"})
            elif action == "approve" and status == "ready":
                if not run["config"].get("rights_confirmed"):
                    if not runtime.repository.profile(user_id).get("rights_confirmed"):
                        raise HTTPException(400, "Confirm source usage rights in the profile before approving this upload.")
                    # Explicit approval attests the source in this run after the profile was updated.
                    config = {**run["config"], "rights_confirmed": True}
                else:
                    config = run["config"]
                runtime.repository.update(user_id, run_id, approved=True, config=config,
                                          refs={**refs, "trigger": "manual"})
            elif action == "pause" and status in {"queued", "localizing", "packaging", "ready"}:
                refs["paused_from"] = status
                runtime.repository.update(user_id, run_id, status="paused", refs=refs)
            elif action == "resume" and status == "paused":
                runtime.repository.update(user_id, run_id, status=refs.pop("paused_from", "queued"), refs=refs)
            elif action == "retry" and status == "failed":
                next_stage = "publishing" if refs.get("upload_session_uri") or refs.get("external_video_id") else (
                    "ready" if refs.get("video_path") else "localizing" if run["job_id"] else "queued")
                if next_stage == "localizing":
                    job = runtime._owned_job(user_id, run["job_id"])
                    if job.status == "error":
                        if runtime.retry_localization is None:
                            raise HTTPException(409, "Chưa có bộ chạy lại job. Thử lại job liên kết trong Lịch sử trước.")
                        await runtime.retry_localization(job.id, user_id)
                    elif job.status == "cancelled":
                        raise HTTPException(409, "Job đã bị hủy. Chọn lại video để tạo lượt xử lý mới.")
                runtime.repository.update(user_id, run_id, status=next_stage)
            elif action == "cancel" and status in {"discovered", "queued", "localizing", "ready", "failed", "paused"}:
                if refs.get("external_video_id") or refs.get("upload_session_uri"):
                    raise HTTPException(400, "Remote upload may exist. Manage it in YouTube Studio.")
                runtime.repository.update(user_id, run_id, status="cancelled")
            else:
                raise HTTPException(409, "Action is unavailable in this run state.")
        return await runtime.advance(user_id, run_id)

    app.include_router(router)

    @app.post("/api/jobs/{job_id}/short-plan")
    async def short_plan(job_id: str, body: ShortRequest, user_id: int = Depends(get_current_user_id)) -> dict:
        try:
            return await asyncio.to_thread(runtime.plan_short, user_id, job_id, body)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (ValueError, subprocess.SubprocessError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/jobs/{job_id}/shorts")
    async def make_short(job_id: str, body: CutsBody, user_id: int = Depends(get_current_user_id)) -> dict:
        try:
            result = await asyncio.to_thread(runtime.make_short, user_id, job_id, body.plan)
            return {"short_id": result["short_id"], "plan": result["plan"],
                    "video_url": f"/api/jobs/{job_id}/shorts/{result['short_id']}/video"}
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (ValueError, KeyError, subprocess.SubprocessError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/jobs/{job_id}/shorts/{short_id}/video")
    def short_video(job_id: str, short_id: str, user_id: int = Depends(get_current_user_id)) -> FileResponse:
        try:
            runtime._owned_job(user_id, job_id)
            if len(short_id) != 32 or any(c not in "0123456789abcdef" for c in short_id):
                raise LookupError("Short not found.")
            path = runtime.output_dir / str(user_id) / job_id / short_id / "short.mp4"
            if not path.is_file():
                raise LookupError("Short not found.")
            return FileResponse(path, media_type="video/mp4")
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.on_event("startup")
    async def start_worker() -> None:
        app.state.reup_task = asyncio.create_task(runtime.loop())

    @app.on_event("shutdown")
    async def stop_worker() -> None:
        runtime.source_sessions.close()
        task = getattr(app.state, "reup_task", None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        tasks = set(runtime.background_tasks) | set(runtime.strategy_tasks.values()) | set(runtime.scan_tasks.values()) | set(runtime.source_check_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    return runtime
