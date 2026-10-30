"""CP10 persistent, restart-safe orchestration with mandatory human gates."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Callable, Optional

from universal_video_ai.channel_agent.opportunities import ContentOpportunityService
from universal_video_ai.channel_agent.production import ProductionQueueService
from universal_video_ai.channel_agent.production_assets import ProductionAssetService
from universal_video_ai.web.store import Store
from universal_video_ai.provider_runtime import (
    ProviderMode,
    execute_provider_call,
)
from universal_video_ai.channel_agent.coordination import (
    ExecutionBusy, ExecutionLease, ExecutionStopped, StageBudget, budget_scope,
    check_run, connection, control_run, update_run,
)
from universal_video_ai.channel_agent.social_publishing import (
    COMPLETED_PUBLISHING_STATES, matches_publish_request,
)


AUTOMATION_MODES = frozenset({
    "MANUAL_STEP", "ASSISTED", "AUTOMATION_WITH_GATES", "FULL_AUTONOMOUS",
})
RUN_STATUSES = frozenset({
    "queued", "running", "waiting_approval", "paused", "completed", "failed", "cancelled",
})
STEP_STATUSES = frozenset({"pending", "running", "waiting", "completed", "failed", "skipped"})
PIPELINE_STAGES = (
    "research", "competitor", "opportunity", "production", "script", "assets",
    "render", "publish", "analytics", "learning",
)
GATE_AFTER_STAGE = {
    "opportunity": "opportunity", "script": "script", "assets": "assets", "render": "publish",
}
GATE_POLICY_KEYS = {
    "opportunity": "require_opportunity_approval",
    "script": "require_script_approval",
    "assets": "require_asset_approval",
    "publish": "require_publish_approval",
}
MAX_RETRIES = 3
FORBIDDEN_CONFIG_KEYS = frozenset({
    "access_token", "refresh_token", "token", "client_secret", "app_secret", "password", "authorization", "api_key", "cookie", "cookies", "appsecret_proof", "secret",
})


class AutomationError(RuntimeError):
    pass


class AutomationNotFound(AutomationError):
    pass


class AutomationBusy(AutomationError):
    pass


class AutomationWaiting(AutomationError):
    """A safe preparation step needs explicit user input, not a failed run."""


StageHandler = Callable[[int, dict[str, Any], dict[str, Any]], dict[str, Any]]


def _contains_secret(value: Any) -> bool:
    if isinstance(value, dict):
        return any((str(key).strip().casefold() in FORBIDDEN_CONFIG_KEYS
                    or str(key).strip().casefold().endswith(("_token", "_secret", "_password", "_api_key"))
                    or _contains_secret(item))
                   for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    return False


class AutomationOrchestrator:
    """Coordinates existing services; it does not create a scheduler or worker loop."""

    def __init__(self, store: Store, *, handlers: Optional[dict[str, StageHandler]] = None,
                 now: Callable[[], float] = time.time) -> None:
        self.store, self.handlers, self.now = store, handlers or {}, now

    def start(self, user_id: int, *, mode: str, configuration: dict[str, Any],
              trigger: str = "manual", run_type: str = "content_pipeline",
              channel_id: Optional[str] = None,
              idempotency_key: Optional[str] = None) -> tuple[dict[str, Any], bool]:
        mode = str(mode).upper()
        if mode not in AUTOMATION_MODES:
            raise AutomationError(
                "Mode must be MANUAL_STEP, ASSISTED, AUTOMATION_WITH_GATES, or FULL_AUTONOMOUS."
            )
        if _contains_secret(configuration):
            raise AutomationError("Automation configuration cannot contain credentials or tokens.")
        platforms = configuration.get("target_platforms") or []
        if not isinstance(platforms, list) or any(item not in {"youtube", "facebook"} for item in platforms):
            raise AutomationError("Target platforms must be a list containing youtube and/or facebook.")
        configuration = dict(configuration)
        approval_policy = dict(configuration.get("approval_policy") or {})
        for policy_key in GATE_POLICY_KEYS.values():
            approval_policy[policy_key] = approval_policy.get(policy_key) is not False
        configuration.update({
            "target_platforms": list(dict.fromkeys(platforms)),
            "max_opportunities": min(20, max(1, int(configuration.get("max_opportunities", 5)))),
            "production_limit": min(10, max(1, int(configuration.get("production_limit", 1)))),
            "approval_policy": approval_policy,
        })
        key = idempotency_key or hashlib.sha256(
            f"{user_id}:{uuid.uuid4().hex}:{json.dumps(configuration, sort_keys=True)}".encode()
        ).hexdigest()
        return self.store.create_automation_run(user_id, {
            "channel_id": channel_id, "run_type": run_type, "mode": mode,
            "trigger": trigger, "configuration": configuration,
            "initiated_by": user_id, "idempotency_key": key,
        }, list(PIPELINE_STAGES))

    def get(self, user_id: int, run_id: int) -> dict[str, Any]:
        run = self.store.get_automation_run(user_id, run_id)
        if not run:
            raise AutomationNotFound("Automation run not found.")
        return run

    def list(self, user_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list_automation_runs(user_id, limit=limit)

    @staticmethod
    def _skip(stage: str, config: dict[str, Any]) -> bool:
        return (
            (stage == "research" and not config.get("research_refresh", True))
            or (stage == "competitor" and not config.get("competitor_refresh", True))
            or (stage == "publish" and not config.get("target_platforms"))
            or (stage == "publish" and "auto_publish" in config and not config.get("auto_publish"))
            or (stage == "analytics" and not config.get("refresh_analytics", False))
            or (stage == "learning" and not config.get("generate_learning", False))
        )

    def _valid_existing_output(self, user_id: int, stage: str,
                               config: dict[str, Any], refs: dict[str, Any]) -> Optional[dict[str, Any]]:
        opportunity_id = refs.get("opportunity_id") or config.get("opportunity_id")
        item_id = refs.get("production_item_id") or config.get("production_item_id")
        if stage == "opportunity" and opportunity_id:
            row = self.store.get_content_opportunity(user_id, int(opportunity_id))
            return {"opportunity_id": int(opportunity_id)} if row else None
        if stage == "production" and opportunity_id:
            row = self.store.get_production_item_by_opportunity(user_id, int(opportunity_id))
            return {"production_item_id": int(row["id"])} if row else None
        if not item_id:
            return None
        item_id = int(item_id)
        if stage == "script":
            rows = self.store.list_production_assets(
                user_id, item_id, asset_type="script_draft", status="approved"
            )
            return {"script_asset_id": int(rows[0]["id"])} if rows else None
        if stage == "assets":
            try:
                package = ProductionAssetService(self.store, provider=None).package(user_id, item_id)  # type: ignore[arg-type]
            except Exception:
                # A stale/mocked reference is not a valid reusable output. The
                # stage executor will either recreate it or persist its exact
                # failure; idempotency inspection itself must not crash a run.
                return None
            return {"asset_package": package} if package.get("asset_ready") else None
        if stage == "render":
            rows = self.store.list_production_render_jobs(user_id, item_id)
            row = next((item for item in rows if item.get("status") == "completed"
                        and item.get("qc", {}).get("passed")), None)
            return {"render_job_id": int(row["id"])} if row else None
        if stage == "publish":
            requested = set(config.get("target_platforms") or [])
            rows = self.store.list_production_publishing_jobs(user_id, item_id)
            render_id = int(refs.get("render_job_id") or 0)
            matched = {}
            for row in rows:
                platform = row.get("platform")
                if (platform in requested and platform not in matched
                        and row.get("status") in COMPLETED_PUBLISHING_STATES
                        and row.get("external_video_id")
                        and matches_publish_request(self.store, user_id, row, render_id, config)):
                    matched[platform] = row
            if requested and requested.issubset(matched):
                return {"publishing_jobs": {
                    platform: {"job_id": int(row["id"]), "status": row["status"]}
                    for platform, row in matched.items()
                }}
        if stage == "analytics":
            jobs = refs.get("publishing_jobs") or {}
            youtube = jobs.get("youtube") if isinstance(jobs, dict) else None
            if youtube:
                snapshots = self.store.list_video_performance_snapshots(
                    user_id, publishing_job_id=int(youtube["job_id"]), limit=1
                )
                return {"snapshot_id": int(snapshots[0]["id"])} if snapshots else None
        if stage == "learning":
            jobs = refs.get("publishing_jobs") or {}
            youtube = jobs.get("youtube") if isinstance(jobs, dict) else None
            if youtube:
                reports = self.store.list_video_learning_reports(
                    user_id, publishing_job_id=int(youtube["job_id"]), limit=1
                )
                return {"learning_report_id": int(reports[0]["id"])} if reports else None
        return None

    def _default_execute(self, user_id: int, stage: str,
                         config: dict[str, Any], refs: dict[str, Any]) -> dict[str, Any]:
        if stage == "opportunity":
            result = ContentOpportunityService(self.store).generate(
                user_id, limit=int(config["max_opportunities"])
            )
            items = list(result.get("items", []))
            output: dict[str, Any] = {
                "opportunity_candidates": [int(item["id"]) for item in items]
            }
            if config.get("auto_shortlist", False):
                minimum = float(config.get("minimum_opportunity_score", 0.65))
                confidence_rank = {"low": 1, "medium": 2, "high": 3}
                required_confidence = confidence_rank.get(
                    str(config.get("minimum_opportunity_confidence", "medium")).lower(), 2
                )
                eligible = [item for item in items if (
                    float(item.get("final_score") or item.get("score") or 0) >= minimum
                    and confidence_rank.get(str(item.get("confidence") or "low").lower(), 1) >= required_confidence
                    and str(item.get("rights_gate_status") or item.get("rights_status") or "cleared")
                    not in {"blocked", "research_only"}
                )]
                if eligible:
                    selected = eligible[0]
                    output["opportunity_id"] = int(selected["id"])
                    output["shortlist_reason"] = {
                        "score": float(selected.get("final_score") or selected.get("score") or 0),
                        "confidence": str(selected.get("confidence") or "low"),
                        "bounded_limit": int(config.get("production_limit", 1)),
                    }
                    if not self._gate_required(config, "opportunity"):
                        ContentOpportunityService(self.store).change_status(
                            user_id, int(selected["id"]), status="approved"
                        )
            return output
        if stage == "production":
            opportunity_id = refs.get("opportunity_id") or config.get("opportunity_id")
            if not opportunity_id:
                raise AutomationWaiting("Select and approve an opportunity before production.")
            item, created = ProductionQueueService(self.store).create(user_id, int(opportunity_id))
            return {"production_item_id": int(item["id"]), "production_created": created}
        raise AutomationWaiting(f"{stage.title()} requires an explicit existing-service action.")

    def _claim_stage(self, user_id: int, run_id: int, step: dict[str, Any],
                     config: dict[str, Any], refs: dict[str, Any], lease: ExecutionLease) -> None:
        stage = step["stage"]
        limit_key = {
            "render": "max_concurrent_renders",
            "publish": "max_concurrent_publishes",
        }.get(stage)
        with connection(self.store.db_path, immediate=True) as conn:
            check_run(conn, lease, run_id)
            if limit_key:
                limit = max(1, int(config.get(limit_key, 1)))
                running = int(conn.execute(
                    "SELECT COUNT(*) c FROM automation_steps s JOIN automation_runs r ON r.id=s.run_id AND r.user_id=s.user_id "
                    "LEFT JOIN channel_execution_leases l ON l.user_id=r.user_id AND l.resource='automation:'||r.id "
                    "WHERE s.user_id=? AND s.stage=? AND s.status='running' AND s.run_id<>? "
                    "AND (r.status NOT IN ('cancelled','completed','failed','paused') OR l.expires_at>?)",
                    (user_id, stage, run_id, time.time()),
                ).fetchone()["c"])
                if running >= limit:
                    raise AutomationWaiting(f"Concurrency limit reached: {limit_key}={limit}.")
            conn.execute(
                "UPDATE automation_steps SET status='running',input_refs_json=?,started_at=?,error=NULL,updated_at=? "
                "WHERE user_id=? AND run_id=? AND stage=?",
                (json.dumps(refs, sort_keys=True), step.get("started_at") or self.now(),
                 self.now(), user_id, run_id, stage),
            )

    def _execute_stage(self, user_id: int, stage: str, config: dict[str, Any],
                       refs: dict[str, Any], handler: Optional[StageHandler],
                       *, lease: ExecutionLease, run_id: int) -> dict[str, Any]:
        def operation():
            from universal_video_ai.downloader.concurrency import download_scope
            with download_scope(self.store.db_path, user_id, config.get("max_concurrent_downloads", 20)):
                return (handler(user_id, config, refs) if handler
                        else self._default_execute(user_id, stage, config, refs))
        if stage in {"opportunity", "production"}:
            return operation()
        categories = {
            "research": "external_api_calls", "competitor": "external_api_calls",
            "script": "llm_calls", "assets": "llm_calls", "render": "tts_requests",
            "publish": "upload_attempts", "analytics": "external_api_calls", "learning": "llm_calls",
        }
        reservation = StageBudget(lease, run_id, categories[stage],
                                  dict(config.get("budgets") or {}), refs)
        simulated = lambda: {
            "provider_mode": str(config["provider_mode"]).upper(),
            "simulated": True,
            "stage": stage,
        }
        with budget_scope(reservation):
            if "provider_mode" not in config:
                return operation()
            return execute_provider_call(
                "channel_operator", stage,
                {"stage": stage, "config": config, "refs": refs},
                live=operation, mock=simulated,
                mode=ProviderMode(str(config["provider_mode"]).upper()),
                category=("llm" if stage in {"script", "assets", "learning"}
                          else "tts" if stage == "render"
                          else "upload" if stage == "publish" else "external"),
                cacheable=stage in {"research", "competitor", "script", "assets", "learning"},
            )

    def _set_waiting(self, user_id: int, run_id: int, stage: str, reason: str,
                     lease: ExecutionLease) -> dict[str, Any]:
        step = self.store.get_automation_step(user_id, run_id, stage)
        if step and step["status"] == "running":
            self.store.update_automation_step(user_id, run_id, stage, {
                "status": "waiting", "error": None,
            })
        update_run(lease, run_id, {
            "status": "waiting_approval", "current_stage": stage,
            "current_step_id": step["id"] if step else None, "waiting_reason": reason,
            "error": None,
        })
        return self.get(user_id, run_id)

    def _gate_satisfied(self, user_id: int, run_id: int, gate: str) -> bool:
        event = self.store.latest_automation_approval(user_id, run_id, gate)
        return bool(event and event.get("action") == "approved")

    @staticmethod
    def _gate_required(config: dict[str, Any], gate: str) -> bool:
        key = GATE_POLICY_KEYS.get(gate)
        return True if not key else dict(config.get("approval_policy") or {}).get(key) is not False

    def advance(self, user_id: int, run_id: int) -> dict[str, Any]:
        self.get(user_id, run_id)
        try:
            with ExecutionLease(self.store.db_path, user_id, f"automation:{run_id}") as lease:
                try:
                    return self._advance(user_id, run_id, lease)
                except ExecutionStopped:
                    return self.get(user_id, run_id)
        except ExecutionBusy as exc:
            raise AutomationBusy(str(exc)) from exc

    def _advance(self, user_id: int, run_id: int, lease: ExecutionLease) -> dict[str, Any]:
        run = self.get(user_id, run_id)
        if run["status"] == "failed":
            raise AutomationError("Failed runs must use bounded step retry.")
        if run["status"] in {"paused", "cancelled", "completed"}:
            raise AutomationError(f"Cannot advance a {run['status']} automation run.")
        config, refs = run["configuration"], dict(run["refs"])
        # Only the persisted lease owner may recover an interrupted stage.
        for step in run["steps"]:
            if step["status"] == "running":
                self.store.update_automation_step(user_id, run_id, step["stage"], {
                    "status": "pending", "error": "Recovered after interrupted process.",
                })
        run = self.get(user_id, run_id)
        completed_count = sum(step["status"] in {"completed", "skipped"} for step in run["steps"])
        update_run(lease, run_id, {
            "status": "running", "started_at": run.get("started_at") or self.now(),
            "waiting_reason": None, "error": None,
        })
        budget = 1 if run["mode"] in {"MANUAL_STEP", "ASSISTED"} else len(PIPELINE_STAGES)
        executed = 0
        for step in run["steps"]:
            with connection(self.store.db_path) as conn:
                check_run(conn, lease, run_id)
            stage = str(step["stage"])
            if step["status"] in {"completed", "skipped"}:
                gate = GATE_AFTER_STAGE.get(stage)
                if gate and self._gate_required(config, gate) and not self._gate_satisfied(user_id, run_id, gate):
                    return self._set_waiting(
                        user_id, run_id, stage, f"Human {gate} approval is required.", lease
                    )
                continue
            if executed >= budget:
                break
            if self._skip(stage, config):
                self.store.update_automation_step(user_id, run_id, stage, {
                    "status": "skipped", "completed_at": self.now(), "error": None,
                })
                completed_count += 1
                continue
            existing = self._valid_existing_output(user_id, stage, config, refs)
            if existing:
                refs.update(existing)
                self.store.update_automation_step(user_id, run_id, stage, {
                    "status": "completed", "output_refs": existing,
                    "completed_at": self.now(), "error": None,
                })
                update_run(lease, run_id, {"refs": refs})
                completed_count += 1
            else:
                try:
                    self._claim_stage(user_id, run_id, step, config, refs, lease)
                except AutomationWaiting as exc:
                    return self._set_waiting(user_id, run_id, stage, str(exc), lease)
                update_run(lease, run_id, {
                    "current_stage": stage, "current_step_id": step["id"],
                    "progress": round(completed_count / len(PIPELINE_STAGES) * 100, 1),
                })
                try:
                    handler = self.handlers.get(stage)
                    output = self._execute_stage(user_id, stage, config, refs, handler,
                                                 lease=lease, run_id=run_id)
                    if not isinstance(output, dict):
                        raise AutomationError("Automation stage output must be a mapping.")
                    if _contains_secret(output):
                        raise AutomationError("Automation output cannot contain credentials or tokens.")
                    refs.update(output)
                    update_run(lease, run_id, {"refs": refs})
                    self.store.update_automation_step(user_id, run_id, stage, {"output_refs": output})
                    if stage == "publish" and not (output.get("simulated") or output.get("dry_run")):
                        jobs = output.get("publishing_jobs") or {}
                        incomplete = [platform for platform in config.get("target_platforms", [])
                                      if jobs.get(platform, {}).get("status") not in COMPLETED_PUBLISHING_STATES]
                        if incomplete:
                            states = [jobs.get(platform, {}).get("status", "missing") for platform in incomplete]
                            if any(state in {"failed", "outcome_unknown", "missing"} for state in states):
                                raise AutomationError("Publishing incomplete for: " + ", ".join(incomplete))
                            raise AutomationWaiting("Remote publishing is still processing: " + ", ".join(incomplete))
                    self.store.update_automation_step(user_id, run_id, stage, {
                        "status": "completed", "output_refs": output,
                        "completed_at": self.now(), "error": None,
                    })
                    update_run(lease, run_id, {"refs": refs})
                    completed_count += 1
                except AutomationWaiting as exc:
                    return self._set_waiting(user_id, run_id, stage, str(exc), lease)
                except ExecutionStopped:
                    raise
                except Exception as exc:
                    self.store.update_automation_step(user_id, run_id, stage, {
                        "status": "failed", "error": str(exc),
                    })
                    update_run(lease, run_id, {
                        "status": "failed", "current_stage": stage,
                        "current_step_id": step["id"], "error": str(exc),
                    })
                    return self.get(user_id, run_id)
            executed += 1
            gate = GATE_AFTER_STAGE.get(stage)
            if gate and self._gate_required(config, gate) and not self._gate_satisfied(user_id, run_id, gate):
                return self._set_waiting(
                    user_id, run_id, stage, f"Human {gate} approval is required.", lease
                )
        refreshed = self.get(user_id, run_id)
        remaining = [step for step in refreshed["steps"]
                     if step["status"] not in {"completed", "skipped"}]
        if not remaining:
            update_run(lease, run_id, {
                "status": "completed", "current_stage": None, "current_step_id": None,
                "progress": 100, "completed_at": self.now(), "waiting_reason": None,
            })
        else:
            next_step = remaining[0]
            update_run(lease, run_id, {
                "status": (
                    "running" if run["mode"] in {"AUTOMATION_WITH_GATES", "FULL_AUTONOMOUS"}
                    else "queued"
                ),
                "current_stage": next_step["stage"], "current_step_id": next_step["id"],
                "progress": round(completed_count / len(PIPELINE_STAGES) * 100, 1),
            })
        return self.get(user_id, run_id)

    def approve(self, user_id: int, run_id: int, stage: str, *,
                refs: Optional[dict[str, Any]] = None, note: Optional[str] = None) -> dict[str, Any]:
        if stage not in {"opportunity", "script", "assets", "publish"}:
            raise AutomationError("Unknown approval gate.")
        run = self.get(user_id, run_id)
        if run["status"] not in {"queued", "waiting_approval"}:
            raise AutomationError("Only queued or waiting runs can accept approvals.")
        if _contains_secret(refs or {}):
            raise AutomationError("Automation references cannot contain credentials or tokens.")
        merged = dict(run["refs"])
        merged.update(refs or {})
        item_id = merged.get("production_item_id") or run["configuration"].get("production_item_id")
        if stage == "opportunity":
            opportunity_id = merged.get("opportunity_id")
            row = self.store.get_content_opportunity(user_id, int(opportunity_id or 0))
            if not row or row.get("status") != "approved":
                raise AutomationError("Select an owner-scoped approved opportunity before approving this gate.")
        elif stage == "script":
            rows = self.store.list_production_assets(
                user_id, int(item_id or 0), asset_type="script_draft", status="approved"
            )
            if not rows:
                raise AutomationError("An approved Script Draft is required.")
            merged["script_asset_id"] = int(rows[0]["id"])
        elif stage == "assets":
            package = ProductionAssetService(self.store, provider=None).package(  # type: ignore[arg-type]
                user_id, int(item_id or 0)
            )
            if not package.get("asset_ready"):
                raise AutomationError("The approved Production Asset Package is not ready.")
            merged["asset_package"] = package
        elif stage == "publish":
            render_id = merged.get("render_job_id")
            render = self.store.get_production_render_job(user_id, int(item_id or 0), int(render_id or 0))
            if not render or render.get("status") != "completed" or not render.get("qc", {}).get("passed"):
                raise AutomationError("A completed render with passing QC is required before publish approval.")
        event = self.store.add_automation_approval(user_id, run_id, stage, "approved", note)
        if not event:
            raise AutomationNotFound("Automation run not found.")
        self._control(user_id, run_id, {
            "status": "queued", "waiting_reason": None, "error": None, "refs": merged,
        }, {"queued", "waiting_approval"})
        return self.get(user_id, run_id)

    def pause(self, user_id: int, run_id: int) -> dict[str, Any]:
        run = self.get(user_id, run_id)
        if run["status"] in {"completed", "cancelled"}:
            raise AutomationError("Completed or cancelled runs cannot be paused.")
        self._control(user_id, run_id, {"status": "paused"}, set(RUN_STATUSES) - {"completed", "cancelled"})
        return self.get(user_id, run_id)

    def resume(self, user_id: int, run_id: int) -> dict[str, Any]:
        run = self.get(user_id, run_id)
        if run["status"] == "cancelled":
            raise AutomationError("Cancelled runs cannot be resumed.")
        if run["status"] == "completed":
            return run
        if run["status"] == "failed":
            return self.retry(user_id, run_id)
        self._control(user_id, run_id, {"status": "queued", "error": None},
                      {"queued", "paused", "waiting_approval", "running"})
        return self.advance(user_id, run_id)

    def retry(self, user_id: int, run_id: int) -> dict[str, Any]:
        self.get(user_id, run_id)
        try:
            with ExecutionLease(self.store.db_path, user_id, f"automation:{run_id}") as lease:
                return self._retry(user_id, run_id, lease)
        except ExecutionBusy as exc:
            raise AutomationBusy(str(exc)) from exc
        except ExecutionStopped:
            return self.get(user_id, run_id)

    def _retry(self, user_id: int, run_id: int, lease: ExecutionLease) -> dict[str, Any]:
        run = self.get(user_id, run_id)
        if run["status"] != "failed":
            raise AutomationError("Only failed runs can retry a step.")
        step = next((item for item in run["steps"] if item["status"] == "failed"), None)
        if not step:
            raise AutomationError("No failed automation step is available to retry.")
        count = int(step["retry_count"] or 0)
        if count >= MAX_RETRIES:
            raise AutomationError("Automation step retry limit reached.")
        with connection(self.store.db_path, immediate=True) as conn:
            check_run(conn, lease, run_id)
            conn.execute("UPDATE automation_steps SET status='pending',retry_count=?,error=NULL "
                         "WHERE user_id=? AND run_id=? AND stage=?",
                         (count + 1, user_id, run_id, step["stage"]))
            conn.execute("UPDATE automation_runs SET status='queued',error=NULL,waiting_reason=NULL "
                         "WHERE user_id=? AND id=?", (user_id, run_id))
        return self._advance(user_id, run_id, lease)

    def cancel(self, user_id: int, run_id: int) -> dict[str, Any]:
        run = self.get(user_id, run_id)
        if run["status"] == "completed":
            raise AutomationError("Completed runs cannot be cancelled.")
        self._control(user_id, run_id, {
            "status": "cancelled", "completed_at": self.now(),
            "waiting_reason": None, "error": None,
        }, set(RUN_STATUSES) - {"completed"})
        return self.get(user_id, run_id)

    def _control(self, user_id: int, run_id: int, data: dict[str, Any], allowed: set[str]) -> None:
        try:
            control_run(self.store.db_path, user_id, run_id, data, allowed)
        except ExecutionStopped as exc:
            raise AutomationError(str(exc)) from exc

    # Scheduler-ready entry points only; CP10 deliberately has no recurring loop.
    def run_daily_research(self, user_id: int, configuration: dict[str, Any]) -> dict[str, Any]:
        run, _ = self.start(
            user_id, mode="AUTOMATION_WITH_GATES", configuration=configuration,
            trigger="scheduler_ready", run_type="daily_research",
        )
        return self.advance(user_id, int(run["id"]))

    def run_content_pipeline(self, user_id: int, configuration: dict[str, Any]) -> dict[str, Any]:
        run, _ = self.start(
            user_id, mode="AUTOMATION_WITH_GATES", configuration=configuration,
            trigger="manual", run_type="content_pipeline",
        )
        return self.advance(user_id, int(run["id"]))

    def refresh_published_metrics(self, user_id: int, configuration: dict[str, Any]) -> dict[str, Any]:
        prepared = dict(configuration)
        prepared.update({"research_refresh": False, "competitor_refresh": False,
                         "refresh_analytics": True, "generate_learning": True})
        run, _ = self.start(
            user_id, mode="MANUAL_STEP", configuration=prepared,
            trigger="manual", run_type="refresh_published_metrics",
        )
        return self.get(user_id, int(run["id"]))
