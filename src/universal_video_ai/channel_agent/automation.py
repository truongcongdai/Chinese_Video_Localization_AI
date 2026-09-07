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
    "access_token", "refresh_token", "token", "client_secret", "app_secret", "password", "authorization",
})


class AutomationError(RuntimeError):
    pass


class AutomationNotFound(AutomationError):
    pass


class AutomationWaiting(AutomationError):
    """A safe preparation step needs explicit user input, not a failed run."""


StageHandler = Callable[[int, dict[str, Any], dict[str, Any]], dict[str, Any]]


def _contains_secret(value: Any) -> bool:
    if isinstance(value, dict):
        return any(str(key).casefold() in FORBIDDEN_CONFIG_KEYS or _contains_secret(item)
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
            matched = {row.get("platform"): row for row in rows
                       if row.get("platform") in requested and row.get("status") != "cancelled"}
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

    @staticmethod
    def _consume_provider_budget(stage: str, config: dict[str, Any],
                                 refs: dict[str, Any]) -> None:
        categories = {
            "research": "external_api_calls", "competitor": "external_api_calls",
            "script": "llm_calls", "assets": "llm_calls", "render": "tts_requests",
            "publish": "upload_attempts", "analytics": "external_api_calls",
            "learning": "llm_calls",
        }
        category = categories.get(stage)
        if not category:
            return
        limits = dict(config.get("budgets") or {})
        limit_key = {
            "external_api_calls": "max_external_api_calls",
            "llm_calls": "max_llm_calls",
            "upload_attempts": "max_upload_attempts",
            "tts_requests": "max_tts_requests",
        }[category]
        usage = dict(refs.get("provider_usage") or {})
        total = int(usage.get("provider_calls") or 0)
        total_limit = limits.get("max_external_api_calls")
        if total_limit is not None and total >= int(total_limit):
            raise AutomationError(
                f"Provider budget exceeded: max_external_api_calls={total_limit}."
            )
        current = int(usage.get(category) or 0)
        limit = limits.get(limit_key)
        if limit is not None and current >= int(limit):
            raise AutomationError(f"Provider budget exceeded: {limit_key}={limit}.")
        usage[category] = current + 1
        usage["provider_calls"] = total + 1
        refs["provider_usage"] = usage

    def _enforce_stage_concurrency(self, user_id: int, run_id: int, stage: str,
                                   config: dict[str, Any]) -> None:
        limit_key = {
            "render": "max_concurrent_renders",
            "publish": "max_concurrent_publishes",
        }.get(stage)
        if not limit_key:
            return
        limit = max(1, int(config.get(limit_key, 1)))
        with self.store._connect() as conn:
            running = int(conn.execute(
                "SELECT COUNT(*) c FROM automation_steps WHERE user_id=? AND stage=? "
                "AND status='running' AND run_id<>?",
                (user_id, stage, run_id),
            ).fetchone()["c"])
        if running >= limit:
            raise AutomationWaiting(f"Concurrency limit reached: {limit_key}={limit}.")

    def _execute_stage(self, user_id: int, stage: str, config: dict[str, Any],
                       refs: dict[str, Any], handler: Optional[StageHandler]) -> dict[str, Any]:
        operation = lambda: (
            handler(user_id, config, refs) if handler
            else self._default_execute(user_id, stage, config, refs)
        )
        if "provider_mode" not in config or stage in {"opportunity", "production"}:
            return operation()
        self._consume_provider_budget(stage, config, refs)
        simulated = lambda: {
            "provider_mode": str(config["provider_mode"]).upper(),
            "simulated": True,
            "stage": stage,
        }
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

    def _set_waiting(self, user_id: int, run_id: int, stage: str, reason: str) -> dict[str, Any]:
        step = self.store.get_automation_step(user_id, run_id, stage)
        if step and step["status"] == "running":
            self.store.update_automation_step(user_id, run_id, stage, {
                "status": "waiting", "error": None,
            })
        self.store.update_automation_run(user_id, run_id, {
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
        run = self.get(user_id, run_id)
        if run["status"] == "failed":
            raise AutomationError("Failed runs must use bounded step retry.")
        if run["status"] in {"paused", "cancelled", "completed"}:
            raise AutomationError(f"Cannot advance a {run['status']} automation run.")
        config, refs = run["configuration"], dict(run["refs"])
        # A process may have died after marking a step running.  Re-evaluate
        # persisted output first, then safely retry only that step.
        for step in run["steps"]:
            if step["status"] == "running":
                self.store.update_automation_step(user_id, run_id, step["stage"], {
                    "status": "pending", "error": "Recovered after interrupted process.",
                })
        run = self.get(user_id, run_id)
        completed_count = sum(step["status"] in {"completed", "skipped"} for step in run["steps"])
        self.store.update_automation_run(user_id, run_id, {
            "status": "running", "started_at": run.get("started_at") or self.now(),
            "waiting_reason": None, "error": None,
        })
        budget = 1 if run["mode"] in {"MANUAL_STEP", "ASSISTED"} else len(PIPELINE_STAGES)
        executed = 0
        for step in run["steps"]:
            stage = str(step["stage"])
            if step["status"] in {"completed", "skipped"}:
                gate = GATE_AFTER_STAGE.get(stage)
                if gate and self._gate_required(config, gate) and not self._gate_satisfied(user_id, run_id, gate):
                    return self._set_waiting(
                        user_id, run_id, stage, f"Human {gate} approval is required."
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
                self.store.update_automation_run(user_id, run_id, {"refs": refs})
                completed_count += 1
            else:
                try:
                    self._enforce_stage_concurrency(user_id, run_id, stage, config)
                except AutomationWaiting as exc:
                    return self._set_waiting(user_id, run_id, stage, str(exc))
                self.store.update_automation_step(user_id, run_id, stage, {
                    "status": "running", "input_refs": refs,
                    "started_at": step.get("started_at") or self.now(), "error": None,
                })
                self.store.update_automation_run(user_id, run_id, {
                    "current_stage": stage, "current_step_id": step["id"],
                    "progress": round(completed_count / len(PIPELINE_STAGES) * 100, 1),
                })
                try:
                    handler = self.handlers.get(stage)
                    output = self._execute_stage(user_id, stage, config, refs, handler)
                    if not isinstance(output, dict):
                        raise AutomationError("Automation stage output must be a mapping.")
                    refs.update(output)
                    self.store.update_automation_step(user_id, run_id, stage, {
                        "status": "completed", "output_refs": output,
                        "completed_at": self.now(), "error": None,
                    })
                    self.store.update_automation_run(user_id, run_id, {"refs": refs})
                    completed_count += 1
                except AutomationWaiting as exc:
                    return self._set_waiting(user_id, run_id, stage, str(exc))
                except Exception as exc:
                    self.store.update_automation_step(user_id, run_id, stage, {
                        "status": "failed", "error": str(exc),
                    })
                    self.store.update_automation_run(user_id, run_id, {
                        "status": "failed", "current_stage": stage,
                        "current_step_id": step["id"], "error": str(exc),
                    })
                    return self.get(user_id, run_id)
            executed += 1
            gate = GATE_AFTER_STAGE.get(stage)
            if gate and self._gate_required(config, gate) and not self._gate_satisfied(user_id, run_id, gate):
                return self._set_waiting(
                    user_id, run_id, stage, f"Human {gate} approval is required."
                )
        refreshed = self.get(user_id, run_id)
        remaining = [step for step in refreshed["steps"]
                     if step["status"] not in {"completed", "skipped"}]
        if not remaining:
            self.store.update_automation_run(user_id, run_id, {
                "status": "completed", "current_stage": None, "current_step_id": None,
                "progress": 100, "completed_at": self.now(), "waiting_reason": None,
            })
        else:
            next_step = remaining[0]
            self.store.update_automation_run(user_id, run_id, {
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
        self.store.update_automation_run(user_id, run_id, {
            "status": "queued", "waiting_reason": None, "error": None, "refs": merged,
        })
        return self.get(user_id, run_id)

    def pause(self, user_id: int, run_id: int) -> dict[str, Any]:
        run = self.get(user_id, run_id)
        if run["status"] in {"completed", "cancelled"}:
            raise AutomationError("Completed or cancelled runs cannot be paused.")
        self.store.update_automation_run(user_id, run_id, {"status": "paused"})
        return self.get(user_id, run_id)

    def resume(self, user_id: int, run_id: int) -> dict[str, Any]:
        run = self.get(user_id, run_id)
        if run["status"] == "cancelled":
            raise AutomationError("Cancelled runs cannot be resumed.")
        if run["status"] == "completed":
            return run
        if run["status"] == "failed":
            return self.retry(user_id, run_id)
        self.store.update_automation_run(user_id, run_id, {"status": "queued", "error": None})
        return self.advance(user_id, run_id)

    def retry(self, user_id: int, run_id: int) -> dict[str, Any]:
        run = self.get(user_id, run_id)
        step = next((item for item in run["steps"] if item["status"] == "failed"), None)
        if not step:
            raise AutomationError("No failed automation step is available to retry.")
        count = int(step["retry_count"] or 0)
        if count >= MAX_RETRIES:
            raise AutomationError("Automation step retry limit reached.")
        self.store.update_automation_step(user_id, run_id, step["stage"], {
            "status": "pending", "retry_count": count + 1, "error": None,
        })
        self.store.update_automation_run(user_id, run_id, {
            "status": "queued", "error": None, "waiting_reason": None,
        })
        return self.advance(user_id, run_id)

    def cancel(self, user_id: int, run_id: int) -> dict[str, Any]:
        run = self.get(user_id, run_id)
        if run["status"] == "completed":
            raise AutomationError("Completed runs cannot be cancelled.")
        self.store.update_automation_run(user_id, run_id, {
            "status": "cancelled", "completed_at": self.now(),
            "waiting_reason": None, "error": None,
        })
        return self.get(user_id, run_id)

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
