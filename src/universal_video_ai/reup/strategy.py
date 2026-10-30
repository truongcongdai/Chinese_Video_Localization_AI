"""Evidence-based channel directions, with resumable full uploads enumeration."""
from __future__ import annotations

from collections import defaultdict
from contextlib import nullcontext
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import logging
import re
import threading
import time
import uuid
from typing import Any

from universal_video_ai.channel_agent.youtube import YouTubeReadOnlyService
from universal_video_ai.publishing.llm import PublishingLLMClient
from universal_video_ai.reup.discovery import Candidate, SourceDiscovery, discovery_issue
from universal_video_ai.reup.ranking import rank_source, source_rank_key
from universal_video_ai.reup.keywords import KEYWORD_VERSION, KeywordPlanner, content_keywords, content_relevance
from universal_video_ai.web.store import Store

__all__ = ["ReupStrategyService"]
_logger = logging.getLogger(__name__)
_PAGE_SIZE = 50
_PAGES_PER_BATCH = 10
_REPORT_TTL_SECONDS = 6 * 3600
_TOP_DIRECTIONS = 3
_SOCIAL_PLATFORMS = frozenset({"douyin", "tiktok", "bilibili"})
_STOPWORDS = frozenset("và của cho một những các là the and for with this that video shorts youtube tiktok official viral trending trend fyp foryou".split())
_SCHEMA = """
CREATE TABLE IF NOT EXISTS reup_channel_catalog (
 user_id INTEGER NOT NULL, channel_id TEXT NOT NULL, video_id TEXT NOT NULL,
 generation TEXT NOT NULL, metadata TEXT NOT NULL,
 PRIMARY KEY(user_id,channel_id,video_id)
);
CREATE TABLE IF NOT EXISTS reup_strategy_state (
 user_id INTEGER PRIMARY KEY, channel_id TEXT NOT NULL, generation TEXT NOT NULL,
 cursor TEXT, status TEXT NOT NULL, config_key TEXT NOT NULL, report TEXT,
 error TEXT, updated_at REAL NOT NULL
);
"""


class ReupStrategyService:
    def __init__(self, store: Store, youtube: YouTubeReadOnlyService, discovery: Any) -> None:
        self.store, self.youtube, self.discovery = store, youtube, discovery
        self.keywords = KeywordPlanner(store)
        self._locks: dict[int, threading.Lock] = {}
        self._lock_guard = threading.Lock()
        with store._connect() as conn:
            conn.executescript(_SCHEMA)

    def status(self, user_id: int) -> dict:
        with self.store._connect() as conn:
            row = conn.execute("SELECT * FROM reup_strategy_state WHERE user_id=?", (user_id,)).fetchone()
            count = conn.execute("SELECT COUNT(*) FROM reup_channel_catalog WHERE user_id=? AND channel_id=? AND generation=?",
                                 (user_id, row["channel_id"], row["generation"])).fetchone()[0] if row else 0
        if not row:
            return {"status": "not_analyzed", "videos_scanned": 0, "report": None}
        return {"status": row["status"], "channel_id": row["channel_id"], "videos_scanned": count,
                "updated_at": row["updated_at"], "error": row["error"],
                "report": json.loads(row["report"]) if row["report"] else None}

    def _state(self, user_id: int) -> dict | None:
        with self.store._connect() as conn:
            row = conn.execute("SELECT * FROM reup_strategy_state WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def refresh_keywords(self, user_id: int, config: dict) -> dict:
        """Repair an old ready report using its own evidence, without network IO."""
        with self._lock_guard:
            lock = self._locks.setdefault(user_id, threading.Lock())
        if not lock.acquire(blocking=False):
            return self.status(user_id)
        try:
            self._refresh_keywords(user_id, config)
            return self.status(user_id)
        finally:
            lock.release()

    def _refresh_keywords(self, user_id: int, config: dict) -> None:
        state = self._state(user_id)
        if not state or state["status"] != "ready" or state["config_key"] != self.config_key(config):
            return
        report = json.loads(state["report"] or "{}")
        if report.get("keyword_version", 0) >= KEYWORD_VERSION:
            return
        if not report.get("directions"):
            return
        report["warnings"] = [notice for notice in report.get("warnings", []) if not notice.startswith("Chưa tạo được từ khóa tiếng Trung")]
        self.keywords.enrich(report, config, local_only=True)
        report["keywords_updated_at"] = time.time()
        for direction in report["directions"]:
            direction["plan"] = self._channel_plan(direction, report.get("mode", "existing_channel"), config)
        # Keep the original market timestamp and IDs, so selections survive and
        # repairing keywords does not pretend to refresh external trend data.
        with self.store._connect() as conn:
            conn.execute("UPDATE reup_strategy_state SET report=? WHERE user_id=? AND generation=? AND status='ready' AND config_key=?",
                         (json.dumps(report, ensure_ascii=False), user_id, state["generation"], state["config_key"]))

    def pending(self) -> list[int]:
        with self.store._connect() as conn:
            rows = conn.execute("SELECT user_id FROM reup_strategy_state WHERE status='scanning'").fetchall()
        return [row["user_id"] for row in rows]

    @staticmethod
    def config_key(config: dict) -> str:
        keys = ("market_region", "topic", "keywords", "exclude_keywords", "platforms", "llm_model", "strategy_mode", "daily_limit")
        effective = {**config, "topic": "", "keywords": []} if config.get("strategy_mode") == "auto" else config
        return hashlib.sha256(json.dumps({"version": 4, **{k: effective.get(k) for k in keys}}, sort_keys=True).encode()).hexdigest()

    def analyze(self, user_id: int, config: dict, *, force: bool = False,
                client: PublishingLLMClient | None = None) -> dict:
        with self._lock_guard:
            lock = self._locks.setdefault(user_id, threading.Lock())
        if not lock.acquire(blocking=False):
            raise ValueError("Kênh này đang được phân tích. Xem tiến độ trước khi thử lại.")
        try:
            scope = self.discovery.user_scope(user_id) if isinstance(self.discovery, SourceDiscovery) else nullcontext()
            with scope:
                return self._analyze(user_id, config, force=force, client=client)
        except Exception:
            # Never misclassify failed channel reads as an empty/new channel.
            with self.store._connect() as conn:
                conn.execute("""INSERT INTO reup_strategy_state(user_id,channel_id,generation,status,config_key,error,updated_at)
                    VALUES(?,?,?,'error',?,?,?) ON CONFLICT(user_id) DO UPDATE SET status='error',error=excluded.error,updated_at=excluded.updated_at""",
                             (user_id, config.get("channel_id", ""), uuid.uuid4().hex, self.config_key(config),
                              "Chưa phân tích xong. Kiểm tra kết nối YouTube, quyền đọc kênh hoặc dữ liệu thị trường rồi thử lại; tiến độ quét đã được lưu.", time.time()))
            raise
        finally:
            lock.release()

    def _analyze(self, user_id: int, config: dict, *, force: bool, client: PublishingLLMClient | None) -> dict:
        channel = self.youtube.get_own_channel(user_id)
        if config.get("channel_id") and config["channel_id"] != channel.channel_id:
            raise ValueError("Kênh đích không khớp tài khoản YouTube đã kết nối. Kết nối đúng kênh trước khi phân tích.")
        state = self._state(user_id)
        key = self.config_key(config)
        compatible = state and state["channel_id"] == channel.channel_id and state["config_key"] == key
        if compatible and state["status"] == "ready" and not force and time.time() - state["updated_at"] < _REPORT_TTL_SECONDS:
            self._refresh_keywords(user_id, config)
            return self.status(user_id)
        resume = compatible and state["status"] in {"scanning", "error"}
        generation = state["generation"] if resume else uuid.uuid4().hex
        cursor = state["cursor"] if resume else None
        with self.store._connect() as conn:
            conn.execute("""INSERT INTO reup_strategy_state(user_id,channel_id,generation,cursor,status,config_key,updated_at)
                VALUES(?,?,?,?,'scanning',?,?) ON CONFLICT(user_id) DO UPDATE SET channel_id=excluded.channel_id,
                generation=excluded.generation,cursor=excluded.cursor,status='scanning',config_key=excluded.config_key,
                report=NULL,error=NULL,updated_at=excluded.updated_at""", (user_id, channel.channel_id, generation, cursor, key, time.time()))
        seen_cursors = set()
        complete = not channel.uploads_playlist_id
        if complete and channel.video_count:
            raise ValueError("YouTube chưa trả về danh sách video của kênh; không thể kết luận đây là kênh mới.")
        for _ in range(_PAGES_PER_BATCH if not complete else 0):
            if cursor in seen_cursors:
                raise ValueError("YouTube lặp lại trang video. Tiến độ được giữ để thử lại.")
            seen_cursors.add(cursor)
            query = {"part": "contentDetails", "playlistId": channel.uploads_playlist_id, "maxResults": _PAGE_SIZE}
            if cursor:
                query["pageToken"] = cursor
            payload = self.youtube.data_request(user_id, "playlistItems", query)
            if not isinstance(payload.get("items"), list):
                raise ValueError("Danh sách video YouTube không hợp lệ.")
            ids = list(dict.fromkeys(str(item.get("contentDetails", {}).get("videoId") or "") for item in payload["items"]))
            ids = [video_id for video_id in ids if video_id]
            details = self.youtube.data_request(user_id, "videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(ids)}) if ids else {"items": []}
            if not isinstance(details.get("items"), list):
                raise ValueError("YouTube chưa trả về metadata video.")
            next_cursor = payload.get("nextPageToken")
            if next_cursor and (not isinstance(next_cursor, str) or next_cursor in seen_cursors):
                raise ValueError("YouTube trả về mã trang lặp hoặc không hợp lệ.")
            with self.store._connect() as conn:
                for item in details["items"]:
                    if item.get("snippet", {}).get("channelId") != channel.channel_id:
                        continue
                    conn.execute("""INSERT INTO reup_channel_catalog(user_id,channel_id,video_id,generation,metadata)
                        VALUES(?,?,?,?,?) ON CONFLICT(user_id,channel_id,video_id) DO UPDATE SET
                        generation=excluded.generation,metadata=excluded.metadata""",
                                 (user_id, channel.channel_id, item["id"], generation, json.dumps(item, ensure_ascii=False)))
                conn.execute("UPDATE reup_strategy_state SET cursor=?,updated_at=? WHERE user_id=?",
                             (next_cursor, time.time(), user_id))
            cursor = next_cursor
            if not cursor:
                complete = True
                break
        if not complete:
            return self.status(user_id)
        with self.store._connect() as conn:
            rows = conn.execute("SELECT metadata FROM reup_channel_catalog WHERE user_id=? AND channel_id=? AND generation=?",
                                (user_id, channel.channel_id, generation)).fetchall()
        own_videos = [json.loads(row["metadata"]) for row in rows]
        if not own_videos and channel.video_count:
            raise ValueError("Kênh có video nhưng chưa đọc được metadata; hãy kiểm tra quyền thay vì phân tích như kênh mới.")
        # A new channel can start from any reachable social market, not only YouTube.
        region = config.get("market_region", "VN")
        warnings, coverage, social_videos = [], [], []
        blocked_platforms = set()
        market_items = []
        try:
            market = self.youtube.data_request(user_id, "videos", {"part": "snippet,statistics,contentDetails", "chart": "mostPopular", "regionCode": region, "maxResults": _PAGE_SIZE})
            market_items = market.get("items") or []
            if not isinstance(market_items, list) or not market_items:
                raise ValueError("Empty YouTube market sample")
            coverage.append({"platform": "youtube", "basis": "mostPopular", "sample_size": len(market_items), "region": region})
        except Exception:
            market_items = []
            _logger.warning("YouTube market sample unavailable", exc_info=True)
            warnings.append("Chưa đọc được mẫu phổ biến YouTube; Agent dùng dữ liệu social truy cập được và video của kênh nếu có.")
        try:
            categories = self.youtube.data_request(user_id, "videoCategories", {"part": "snippet", "regionCode": region, "hl": "vi"})
        except Exception:
            _logger.warning("YouTube category names unavailable", exc_info=True)
            categories = {}
        category_names = {str(item["id"]): str(item.get("snippet", {}).get("title") or item["id"]) for item in categories.get("items", [])}
        platforms = list(dict.fromkeys(platform for platform in config.get("platforms", []) if platform in _SOCIAL_PLATFORMS))
        def read_market(platform: str):
            try:
                return self.discovery.market(platform), None
            except Exception as error:
                return [], error
        with ThreadPoolExecutor(max_workers=max(1, len(platforms))) as pool:
            market_reads = [(platform, pool.submit(copy_context().run, read_market, platform)) for platform in platforms]
        for platform, future in market_reads:
            sample, error = future.result()
            if error is None:
                coverage.append({"platform": platform, "basis": "public_discovery_sample", "sample_size": len(sample),
                                 "examples": [candidate.to_dict() for candidate in sample[:5]]})
                if not sample:
                    warnings.append(f"Chưa lấy được mẫu thị trường {platform}; không coi đây là dữ liệu top trending.")
                for candidate in sample:
                    item = self._market_video(candidate)
                    if item is not None:
                        social_videos.append(item)
                        category_names[item["snippet"]["categoryId"]] = f"Chủ đề từ {platform}"
            else:
                _logger.warning("Reup market sample unavailable platform=%s", platform)
                if discovery_issue(error)["status"] in {"verification_required", "login_required", "http_412", "browser_unavailable", "rate_limited"}:
                    blocked_platforms.add(platform)
                warnings.append(f"Chưa đọc được thị trường {platform}; cần đăng nhập/xác minh hoặc thử lại. Agent tiếp tục với dữ liệu truy cập được.")
        if not own_videos and not market_items and not social_videos:
            raise ValueError("Chưa có dữ liệu thị trường thực để đề xuất chiến lược cho kênh mới. Kiểm tra kết nối nền tảng rồi thử lại.")
        analysis_config = {**config, "topic": "", "keywords": [], "_channel_name": channel.title} if config.get("strategy_mode") == "auto" else config
        directions = self._directions(own_videos or [*market_items, *social_videos], category_names, analysis_config)[:_TOP_DIRECTIONS]
        for direction in directions:
            category_id = direction["category_id"]
            benchmark = sorted((self._evidence(item) for item in market_items if str(item.get("snippet", {}).get("categoryId")) == category_id),
                               key=lambda item: item["views_per_day"] or 0, reverse=True)
            direction["market_supporting_videos"] = len(benchmark)
            direction["market_evidence"] = benchmark[:3]
            direction["basis"] = "channel_history" if own_videos else "social_market"
            direction["social_evidence"] = [item for item in direction["evidence"] if item.get("platform") in _SOCIAL_PLATFORMS]
        report = {"query_version": 4, "channel": channel.to_dict(), "mode": "existing_channel" if own_videos else "new_channel",
                  "catalog_complete": True, "videos_analyzed": len(own_videos), "market_region": region,
                  "market_sample_size": len(market_items) + len(social_videos), "market_coverage": coverage,
                  "method": "metadata_scoring", "directions": directions, "warnings": warnings,
                  "generated_at": time.time(), "market_examples": [self._evidence(item) for item in [*market_items, *social_videos]][:15]}
        self.keywords.enrich(report, config, client)
        for direction in directions:
            self._survey_related(direction, config, warnings, blocked_platforms)
            direction["plan"] = self._channel_plan(direction, report["mode"], config)
        # Existing channels keep their own niche; external results validate it.
        # New channels can be ranked directly from social evidence as well as YouTube.
        report["directions"] = sorted(directions, key=lambda item: (item["score"], bool(item["social_evidence"])), reverse=True)
        if not report["directions"]:
            raise ValueError("Dữ liệu hiện tại chưa đủ để đề xuất chiến lược. Thử lại khảo sát hoặc bỏ bớt từ khóa loại trừ trong tùy chọn nâng cao.")
        with self.store._connect() as conn:
            conn.execute("UPDATE reup_strategy_state SET status='ready',cursor=NULL,report=?,error=NULL,updated_at=? WHERE user_id=?",
                         (json.dumps(report, ensure_ascii=False), time.time(), user_id))
            conn.execute("DELETE FROM reup_channel_catalog WHERE user_id=? AND channel_id=? AND generation!=?", (user_id, channel.channel_id, generation))
        return self.status(user_id)

    @staticmethod
    def _market_video(candidate: Candidate) -> dict | None:
        if not candidate.title.strip():
            return None
        tags = list(candidate.tags) or re.findall(r"#([^\s#]+)", candidate.title)
        topic = tags[0] if tags else candidate.title.strip()[:80]
        category = f"social:{hashlib.sha256(topic.casefold().encode()).hexdigest()[:16]}"
        return {"id": f"{candidate.platform}:{candidate.video_id}",
                "snippet": {"title": candidate.title, "tags": tags or [topic], "categoryId": category,
                            "publishedAt": candidate.published_at},
                "statistics": {"viewCount": candidate.view_count}, "source": candidate.to_dict()}

    def _survey_related(self, direction: dict, config: dict, warnings: list[str], blocked: set[str]) -> None:
        evidence = {row["url"]: row for row in direction["social_evidence"]}
        for platform in config.get("platforms", []):
            if platform not in _SOCIAL_PLATFORMS or platform in blocked:
                continue
            queries = direction["search_queries"].get(platform) or []
            if not queries:
                continue
            try:
                for query in queries[:3]:
                    matched = False
                    for candidate in self.discovery.search(platform, query, limit=5):
                        if candidate.platform != platform:
                            continue
                        relevance = content_relevance(candidate.to_dict(), direction["content_profile"])
                        if not relevance["accepted"]:
                            continue
                        item = self._market_video(replace(candidate, search_query=query))
                        if item is None:
                            continue
                        row = self._evidence(item)
                        row["relevance"] = relevance
                        evidence[row["url"]] = row
                        matched = True
                    if matched:
                        break
            except Exception as error:
                blocked.add(platform)
                issue = discovery_issue(error)
                _logger.info("Related social search paused platform=%s status=%s", platform, issue["status"])
                warning = f"{platform}: {issue['message']} Các nguồn khác vẫn tiếp tục được tìm kiếm."
                if not any(platform in item for item in warnings):
                    warnings.append(warning)
        direction["social_evidence"] = sorted(evidence.values(), key=source_rank_key, reverse=True)[:10]

    @staticmethod
    def _channel_plan(direction: dict, mode: str, config: dict) -> dict:
        established = mode == "existing_channel"
        return {"goal": "Phát triển tiếp nội dung đã có khán giả" if established else "Thử nghiệm hướng nội dung cho kênh mới",
                "topic": direction["topic"], "daily_limit": config.get("daily_limit", 1),
                "steps": ["Giữ hướng nội dung rút ra từ video của kênh." if established else "Bắt đầu với hướng được chọn từ mẫu social đã khảo sát.",
                          "Tìm video cùng chủ đề trên các nền tảng đã chọn; ưu tiên tín hiệu lượt xem, tương tác và độ mới khi có số liệu.",
                          "Xử lý, lồng tiếng và chuẩn bị đăng theo chế độ bạn chọn.",
                          "Các lần phân tích tiếp theo dùng thêm video đã đăng để điều chỉnh hướng phát triển."],
                "evidence_platforms": sorted({item.get("platform", "youtube") for item in [*direction.get("evidence", []), *direction.get("social_evidence", [])]}),
                "measurement": "Tín hiệu từ metadata công khai; không cam kết lượt xem hoặc khả năng viral."}

    def own_video_ids(self, user_id: int, channel_id: str) -> set[str]:
        with self.store._connect() as conn:
            rows = conn.execute("SELECT video_id FROM reup_channel_catalog WHERE user_id=? AND channel_id=?", (user_id, channel_id)).fetchall()
        return {row["video_id"] for row in rows}

    @staticmethod
    def _evidence(item: dict) -> dict:
        snippet, stats = item.get("snippet", {}), item.get("statistics", {})
        views = int(stats["viewCount"]) if str(stats.get("viewCount", "")).isdigit() else None
        published = snippet.get("publishedAt")
        days = None
        if published:
            try:
                days = max(1.0, (datetime.now(timezone.utc) - datetime.fromisoformat(published.replace("Z", "+00:00"))).total_seconds() / 86400)
            except (ValueError, TypeError):
                pass
        source = item.get("source") or {"url": f"https://www.youtube.com/watch?v={item['id']}", "platform": "youtube",
            "video_id": item["id"], "channel_id": snippet.get("channelId", ""), "title": snippet.get("title", ""),
            "view_count": views, "published_at": published, "like_count": stats.get("likeCount"),
            "comment_count": stats.get("commentCount"), "tags": snippet.get("tags", []),
            "description": str(snippet.get("description") or "")[:1600]}
        return {**source, "views": views, "published_at": published,
                "views_per_day": round(views / days, 2) if views is not None and days else None,
                "ranking": rank_source(source)}

    def _directions(self, videos: list[dict], categories: dict, config: dict) -> list[dict]:
        groups: dict[str, list[dict]] = defaultdict(list)
        excluded = [word.casefold() for word in config.get("exclude_keywords", []) if word]
        interests = [word.casefold() for word in config.get("keywords", []) if word]
        if config.get("topic", "").strip():
            interests.append(config["topic"].strip().casefold())
        for item in videos:
            snippet = item.get("snippet", {})
            text = " ".join([str(snippet.get("title") or ""), str(snippet.get("description") or ""), *snippet.get("tags", [])]).casefold()
            if any(word in text for word in excluded):
                continue
            # User interests narrow automatic choices, when supplied.
            if interests and not any(word in text for word in interests):
                continue
            groups[str(snippet.get("categoryId") or "other")].append(item)
        clusters = []
        for category, items in groups.items():
            buckets = []
            for item in items:
                tags = {tag.strip().casefold() for tag in item.get("snippet", {}).get("tags", [])
                        if isinstance(tag, str) and 2 <= len(tag.strip()) <= 80 and tag.casefold() not in _STOPWORDS}
                matching = [bucket for bucket in buckets if tags & bucket[0] or not tags and not bucket[0]]
                if matching:
                    selected = matching[0]
                    selected[0].update(tags)
                    selected[1].append(item)
                    for other in matching[1:]:
                        selected[0].update(other[0])
                        selected[1].extend(other[1])
                        buckets.remove(other)
                else:
                    buckets.append((tags, [item]))
            clusters.extend((category, tags, items) for tags, items in buckets)
        directions = []
        for category, cluster_tags, items in clusters:
            evidence = sorted((self._evidence(item) for item in items), key=source_rank_key, reverse=True)
            keywords = content_keywords(items, config.get("_channel_name", ""))
            if not keywords:
                continue
            measured = [row["ranking"]["score"] for row in evidence if row["ranking"]["score"] is not None]
            score = sum(measured) / len(measured) if measured else 0
            cluster = hashlib.sha256(sorted(cluster_tags)[0].encode()).hexdigest()[:12] if cluster_tags else "untagged"
            directions.append({"id": f"category:{category}:{cluster}", "category_id": category, "topic": " · ".join(keywords[:2]), "content_keywords": keywords,
                               "category": categories.get(category, category), "score": round(score, 3),
                               "supporting_videos": len(items), "reason": "Đối chiếu chủ đề, lượt xem, tương tác và độ mới khi có metadata. Mẫu thiếu số liệu được giữ riêng; không suy đoán retention hoặc doanh thu.",
                               "evidence": evidence[:5], "search_queries": {platform: keywords[:3] for platform in config.get("platforms", [])}})
        return sorted(directions, key=lambda row: row["score"], reverse=True)
