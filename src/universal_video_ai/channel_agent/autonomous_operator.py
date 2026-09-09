"""CP11 persistent, policy-gated autonomous channel operator.

The operator schedules and records cycles, while the CP10 orchestrator remains
the only component that executes production stages.  There is intentionally no
in-memory daemon: ``run_due`` is a restart-safe scheduler tick for the web app
or a deployment scheduler to invoke.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from universal_video_ai.channel_agent.automation import (
    AutomationError,
    AutomationOrchestrator,
    _contains_secret,
)
from universal_video_ai.provider_runtime import ProviderMode, get_cost_report
from universal_video_ai.web.store import Store


AUTONOMOUS_SCHEMA = """
CREATE TABLE IF NOT EXISTS autonomous_channel_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    channel_key TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    mode TEXT NOT NULL DEFAULT 'FULL_AUTONOMOUS',
    provider_mode TEXT NOT NULL DEFAULT 'DRY_RUN',
    cadence TEXT NOT NULL DEFAULT 'daily',
    local_time TEXT NOT NULL DEFAULT '09:00',
    timezone_name TEXT NOT NULL DEFAULT 'UTC',
    missed_run_policy TEXT NOT NULL DEFAULT 'run_once',
    next_run_at REAL,
    stop_after_current INTEGER NOT NULL DEFAULT 0,
    configuration_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(user_id, channel_key)
);
CREATE INDEX IF NOT EXISTS idx_autonomous_configs_due
    ON autonomous_channel_configs(enabled, next_run_at);

CREATE TABLE IF NOT EXISTS autonomous_channel_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    config_id INTEGER NOT NULL,
    automation_run_id INTEGER,
    status TEXT NOT NULL DEFAULT 'queued',
    current_stage TEXT,
    progress REAL NOT NULL DEFAULT 0,
    trigger TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    config_snapshot_json TEXT NOT NULL DEFAULT '{}',
    outputs_json TEXT NOT NULL DEFAULT '{}',
    cost_baseline_json TEXT NOT NULL DEFAULT '{}',
    cost_report_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    updated_at REAL NOT NULL,
    completed_at REAL,
    UNIQUE(user_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_autonomous_cycles_owner
    ON autonomous_channel_cycles(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_autonomous_cycles_config
    ON autonomous_channel_cycles(config_id, status);

CREATE TABLE IF NOT EXISTS autonomous_cycle_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    cycle_id INTEGER NOT NULL,
    stage TEXT NOT NULL,
    order_index INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    output_refs_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    updated_at REAL NOT NULL,
    UNIQUE(user_id, cycle_id, stage)
);
CREATE INDEX IF NOT EXISTS idx_autonomous_cycle_steps_owner
    ON autonomous_cycle_steps(user_id, cycle_id, order_index);
"""

AUTONOMOUS_STAGES = (
    "research", "competitor", "opportunities", "shortlist", "production",
    "script", "assets", "render", "publish", "analytics", "learning",
)


DEFAULT_APPROVAL_POLICY = {
    "require_opportunity_approval": True,
    "require_script_approval": True,
    "require_asset_approval": True,
    "require_publish_approval": True,
}
DEFAULT_CONFIG: dict[str, Any] = {
    "research_refresh": True,
    "competitor_refresh": True,
    "max_opportunities": 5,
    "production_limit": 1,
    "max_concurrent_cycles": 1,
    "max_concurrent_downloads": 2,
    "max_concurrent_renders": 1,
    "max_concurrent_publishes": 1,
    "target_platforms": [],
    "default_publishing_privacy": "private",
    "auto_generate_script": False,
    "auto_generate_assets": False,
    "auto_render_after_approval": False,
    "auto_publish": False,
    "refresh_analytics": False,
    "generate_learning": False,
    "minimum_opportunity_score": 0.65,
    "minimum_opportunity_confidence": "medium",
    "auto_shortlist": True,
    "daily_cycle_limit": 1,
    "weekly_cycle_limit": 7,
    "analytics_min_refresh_hours": 6,
    "approval_policy": DEFAULT_APPROVAL_POLICY,
    "budgets": {
        "max_llm_calls": 10,
        "max_external_api_calls": 50,
        "max_tts_requests": 5,
        "max_upload_attempts": 2,
        "max_live_acceptance_calls": 0,
    },
    "failure_policy": "pause",
    "retry_policy": {"max_retries": 3},
}


class AutonomousOperatorError(RuntimeError):
    pass


class AutonomousOperatorNotFound(AutonomousOperatorError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _loads(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


class AutonomousChannelOperator:
    def __init__(self, store: Store, orchestrator: AutomationOrchestrator, *,
                 now: Callable[[], float] = time.time) -> None:
        self.store, self.orchestrator, self.now = store, orchestrator, now
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self.store._connect() as conn:
            conn.executescript(AUTONOMOUS_SCHEMA)
            columns = {row["name"] for row in conn.execute(
                "PRAGMA table_info(autonomous_channel_cycles)"
            ).fetchall()}
            if "cost_baseline_json" not in columns:
                conn.execute(
                    "ALTER TABLE autonomous_channel_cycles ADD COLUMN cost_baseline_json TEXT NOT NULL DEFAULT '{}'"
                )

    def _normalize(self, configuration: Optional[dict[str, Any]]) -> dict[str, Any]:
        incoming = dict(configuration or {})
        if _contains_secret(incoming):
            raise AutonomousOperatorError("Autonomous configuration cannot contain credentials or tokens.")
        output = dict(DEFAULT_CONFIG)
        output.update({key: value for key, value in incoming.items() if key not in {"approval_policy", "budgets", "retry_policy"}})
        approval = dict(DEFAULT_APPROVAL_POLICY)
        approval.update(dict(incoming.get("approval_policy") or {}))
        output["approval_policy"] = {key: value is not False for key, value in approval.items()}
        budgets = dict(DEFAULT_CONFIG["budgets"])
        budgets.update(dict(incoming.get("budgets") or {}))
        output["budgets"] = {key: max(0, int(value)) for key, value in budgets.items()}
        retry = dict(DEFAULT_CONFIG["retry_policy"])
        retry.update(dict(incoming.get("retry_policy") or {}))
        retry["max_retries"] = min(10, max(0, int(retry.get("max_retries", 3))))
        output["retry_policy"] = retry
        output["max_opportunities"] = min(20, max(1, int(output["max_opportunities"])))
        output["production_limit"] = min(10, max(1, int(output["production_limit"])))
        for name in ("max_concurrent_cycles", "max_concurrent_downloads", "max_concurrent_renders", "max_concurrent_publishes"):
            output[name] = min(20, max(1, int(output[name])))
        platforms = output.get("target_platforms") or []
        if not isinstance(platforms, list) or any(item not in {"youtube", "facebook"} for item in platforms):
            raise AutonomousOperatorError("Target platforms must contain only youtube and/or facebook.")
        output["target_platforms"] = list(dict.fromkeys(platforms))
        return output

    @staticmethod
    def next_run(cadence: str, local_time: str, timezone_name: str, after: float) -> float:
        try:
            zone = ZoneInfo(timezone_name)
        except Exception as exc:
            raise AutonomousOperatorError("Invalid scheduler timezone.") from exc
        current = datetime.fromtimestamp(after, tz=timezone.utc).astimezone(zone)
        cadence = str(cadence).lower()
        if cadence == "hourly":
            return (current + timedelta(hours=1)).astimezone(timezone.utc).timestamp()
        try:
            hour, minute = (int(part) for part in str(local_time).split(":", 1))
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                raise ValueError
        except ValueError as exc:
            raise AutonomousOperatorError("local_time must use HH:MM.") from exc
        candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= current:
            candidate += timedelta(days=1 if cadence == "daily" else 7)
        if cadence not in {"daily", "weekly"}:
            raise AutonomousOperatorError("Cadence must be hourly, daily, or weekly.")
        return candidate.astimezone(timezone.utc).timestamp()

    def save_config(self, user_id: int, channel_key: str, *, enabled: bool = False,
                    mode: str = "FULL_AUTONOMOUS", provider_mode: str = "DRY_RUN",
                    cadence: str = "daily", local_time: str = "09:00",
                    timezone_name: str = "UTC", missed_run_policy: str = "run_once",
                    configuration: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        mode, provider_mode = str(mode).upper(), str(provider_mode).upper()
        if mode != "FULL_AUTONOMOUS":
            raise AutonomousOperatorError("Autonomous channel config requires FULL_AUTONOMOUS mode.")
        try:
            ProviderMode(provider_mode)
        except ValueError as exc:
            raise AutonomousOperatorError("Provider mode must be MOCK, CACHE, DRY_RUN, or LIVE.") from exc
        if missed_run_policy not in {"run_once", "skip", "reschedule"}:
            raise AutonomousOperatorError("Missed run policy must be run_once, skip, or reschedule.")
        normalized = self._normalize(configuration)
        now = self.now()
        next_run_at = self.next_run(cadence, local_time, timezone_name, now)
        from universal_video_ai.downloader.concurrency import DownloadSlots
        slots = DownloadSlots(self.store.db_path, user_id)
        with slots.configuration_lock():
            if slots.active_count() > normalized["max_concurrent_downloads"]:
                raise AutonomousOperatorError("Wait for active downloads before lowering their concurrency limit.")
            with self.store._connect() as conn:
                conn.execute(
                    "INSERT INTO autonomous_channel_configs "
                    "(user_id,channel_key,enabled,mode,provider_mode,cadence,local_time,timezone_name,missed_run_policy,next_run_at,configuration_json,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,channel_key) DO UPDATE SET "
                    "enabled=excluded.enabled,mode=excluded.mode,provider_mode=excluded.provider_mode,cadence=excluded.cadence,"
                    "local_time=excluded.local_time,timezone_name=excluded.timezone_name,missed_run_policy=excluded.missed_run_policy,"
                    "next_run_at=excluded.next_run_at,configuration_json=excluded.configuration_json,updated_at=excluded.updated_at",
                    (user_id, channel_key, int(enabled), mode, provider_mode, cadence, local_time,
                     timezone_name, missed_run_policy, next_run_at, _json(normalized), now, now),
                )
        return self.get_config(user_id, channel_key)

    def _config_row(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        data["enabled"] = bool(data["enabled"])
        data["stop_after_current"] = bool(data["stop_after_current"])
        data["configuration"] = _loads(data.pop("configuration_json", "{}"))
        return data

    def get_config(self, user_id: int, channel_key: str) -> dict[str, Any]:
        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT * FROM autonomous_channel_configs WHERE user_id=? AND channel_key=?",
                (user_id, channel_key),
            ).fetchone()
        if not row:
            raise AutonomousOperatorNotFound("Autonomous channel config not found.")
        return self._config_row(row)

    def list_configs(self, user_id: int) -> list[dict[str, Any]]:
        with self.store._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM autonomous_channel_configs WHERE user_id=? ORDER BY updated_at DESC", (user_id,),
            ).fetchall()
        return [self._config_row(row) for row in rows]

    def _cycle_row(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        data["config_snapshot"] = _loads(data.pop("config_snapshot_json", "{}"))
        data["outputs"] = _loads(data.pop("outputs_json", "{}"))
        data["cost_baseline"] = _loads(data.pop("cost_baseline_json", "{}"))
        data["cost_report"] = _loads(data.pop("cost_report_json", "{}"))
        return data

    def get_cycle(self, user_id: int, cycle_id: int) -> dict[str, Any]:
        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT * FROM autonomous_channel_cycles WHERE user_id=? AND id=?", (user_id, cycle_id),
            ).fetchone()
            steps = conn.execute(
                "SELECT * FROM autonomous_cycle_steps WHERE user_id=? AND cycle_id=? ORDER BY order_index",
                (user_id, cycle_id),
            ).fetchall() if row else []
        if not row:
            raise AutonomousOperatorNotFound("Autonomous cycle not found.")
        cycle = self._cycle_row(row)
        cycle["steps"] = [dict(step) for step in steps]
        for step in cycle["steps"]:
            step["output_refs"] = _loads(step.pop("output_refs_json", "{}"))
        return cycle

    def list_cycles(self, user_id: int, limit: int = 50) -> list[dict[str, Any]]:
        with self.store._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM autonomous_channel_cycles WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",
                (user_id, min(100, max(1, int(limit)))),
            ).fetchall()
        return [self._cycle_row(row) for row in rows]

    def _limit_ok(self, user_id: int, config: dict[str, Any]) -> None:
        cfg, now = config["configuration"], self.now()
        with self.store._connect() as conn:
            active = conn.execute(
                "SELECT COUNT(*) c FROM autonomous_channel_cycles WHERE user_id=? AND status IN ('queued','running','waiting_approval')",
                (user_id,),
            ).fetchone()["c"]
            daily = conn.execute(
                "SELECT COUNT(*) c FROM autonomous_channel_cycles WHERE user_id=? AND created_at>=?",
                (user_id, now - 86400),
            ).fetchone()["c"]
            weekly = conn.execute(
                "SELECT COUNT(*) c FROM autonomous_channel_cycles WHERE user_id=? AND created_at>=?",
                (user_id, now - 7 * 86400),
            ).fetchone()["c"]
        if active >= int(cfg["max_concurrent_cycles"]):
            raise AutonomousOperatorError("Maximum concurrent autonomous cycles reached.")
        if daily >= int(cfg["daily_cycle_limit"]) or weekly >= int(cfg["weekly_cycle_limit"]):
            raise AutonomousOperatorError("Autonomous cycle time-window limit reached.")

    def start_cycle(self, user_id: int, config_id: int, *, trigger: str = "manual",
                    idempotency_key: Optional[str] = None, advance: bool = True) -> tuple[dict[str, Any], bool]:
        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT * FROM autonomous_channel_configs WHERE user_id=? AND id=?", (user_id, config_id),
            ).fetchone()
        if not row:
            raise AutonomousOperatorNotFound("Autonomous channel config not found.")
        config = self._config_row(row)
        now = self.now()
        key = idempotency_key or hashlib.sha256(
            f"{user_id}:{config_id}:{trigger}:{now:.6f}".encode("utf-8")
        ).hexdigest()
        with self.store._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM autonomous_channel_cycles WHERE user_id=? AND idempotency_key=?", (user_id, key),
            ).fetchone()
            if existing:
                return self._cycle_row(existing), False
        self._limit_ok(user_id, config)
        with self.store._connect() as conn:
            cur = conn.execute(
                "INSERT INTO autonomous_channel_cycles "
                "(user_id,config_id,status,trigger,idempotency_key,config_snapshot_json,cost_baseline_json,cost_report_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (user_id, config_id, "queued", trigger, key, _json(config),
                 _json(get_cost_report().to_dict()), _json({}), now, now),
            )
            cycle_id = int(cur.lastrowid)
            conn.executemany(
                "INSERT INTO autonomous_cycle_steps (user_id,cycle_id,stage,order_index,updated_at) VALUES (?,?,?,?,?)",
                [(user_id, cycle_id, stage, index, now)
                 for index, stage in enumerate(AUTONOMOUS_STAGES)],
            )
        run_config = dict(config["configuration"])
        run_config["provider_mode"] = config["provider_mode"]
        run, _ = self.orchestrator.start(
            user_id, mode="FULL_AUTONOMOUS", configuration=run_config,
            trigger=trigger, run_type="autonomous_channel_cycle", channel_id=config["channel_key"],
            idempotency_key=f"cp11-cycle-{cycle_id}",
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE autonomous_channel_cycles SET automation_run_id=?,started_at=?,status='running',updated_at=? WHERE user_id=? AND id=?",
                (run["id"], now, now, user_id, cycle_id),
            )
        cycle = self.get_cycle(user_id, cycle_id)
        return (self.advance_cycle(user_id, cycle_id) if advance else cycle), True

    def advance_cycle(self, user_id: int, cycle_id: int) -> dict[str, Any]:
        cycle = self.get_cycle(user_id, cycle_id)
        if cycle["status"] in {"completed", "cancelled"}:
            return cycle
        try:
            run = self.orchestrator.advance(user_id, int(cycle["automation_run_id"]))
            mapping = {
                "completed": "completed", "failed": "failed", "cancelled": "cancelled",
                "paused": "paused", "waiting_approval": "waiting_approval",
            }
            status = mapping.get(run["status"], "running")
            error = run.get("error")
        except AutomationError as exc:
            run, status, error = {}, "failed", str(exc)
        now = self.now()
        current_cost = get_cost_report().to_dict()
        baseline = cycle.get("cost_baseline") or {}
        cycle_cost: dict[str, Any] = {}
        for key, value in current_cost.items():
            if key == "by_provider":
                before = baseline.get(key) or {}
                cycle_cost[key] = {
                    provider: max(0, int(count) - int(before.get(provider) or 0))
                    for provider, count in value.items()
                }
            else:
                cycle_cost[key] = max(0, int(value or 0) - int(baseline.get(key) or 0))
        with self.store._connect() as conn:
            for step in run.get("steps", []):
                stage = "opportunities" if step["stage"] == "opportunity" else step["stage"]
                conn.execute(
                    "UPDATE autonomous_cycle_steps SET status=?,output_refs_json=?,error=?,updated_at=? "
                    "WHERE user_id=? AND cycle_id=? AND stage=?",
                    (step["status"], _json(step.get("output_refs") or {}), step.get("error"),
                     now, user_id, cycle_id, stage),
                )
            shortlist_status = "completed" if (run.get("refs") or {}).get("opportunity_id") else (
                "waiting" if run.get("current_stage") == "opportunity" else "pending"
            )
            conn.execute(
                "UPDATE autonomous_cycle_steps SET status=?,output_refs_json=?,updated_at=? "
                "WHERE user_id=? AND cycle_id=? AND stage='shortlist'",
                (shortlist_status, _json({
                    "opportunity_id": (run.get("refs") or {}).get("opportunity_id"),
                    "reason": (run.get("refs") or {}).get("shortlist_reason"),
                }), now, user_id, cycle_id),
            )
            conn.execute(
                "UPDATE autonomous_channel_cycles SET status=?,current_stage=?,progress=?,outputs_json=?,"
                "cost_report_json=?,error=?,updated_at=?,completed_at=? WHERE user_id=? AND id=?",
                (status, run.get("current_stage"), float(run.get("progress") or cycle["progress"]),
                 _json(run.get("refs") or cycle["outputs"]), _json(cycle_cost), error,
                 now, now if status in {"completed", "failed", "cancelled"} else None, user_id, cycle_id),
            )
        return self.get_cycle(user_id, cycle_id)

    def set_enabled(self, user_id: int, config_id: int, enabled: bool, *, stop_after_current: bool = False) -> dict[str, Any]:
        now = self.now()
        with self.store._connect() as conn:
            cur = conn.execute(
                "UPDATE autonomous_channel_configs SET enabled=?,stop_after_current=?,updated_at=? WHERE user_id=? AND id=?",
                (int(enabled), int(stop_after_current), now, user_id, config_id),
            )
            if not cur.rowcount:
                raise AutonomousOperatorNotFound("Autonomous channel config not found.")
            row = conn.execute("SELECT * FROM autonomous_channel_configs WHERE id=?", (config_id,)).fetchone()
        return self._config_row(row)

    def run_due(self, *, at: Optional[float] = None,
                user_id: Optional[int] = None) -> list[dict[str, Any]]:
        now = self.now() if at is None else float(at)
        sql = (
            "SELECT * FROM autonomous_channel_configs WHERE enabled=1 "
            "AND stop_after_current=0 AND next_run_at<=?"
        )
        params: list[Any] = [now]
        if user_id is not None:
            sql += " AND user_id=?"
            params.append(int(user_id))
        sql += " ORDER BY next_run_at"
        with self.store._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            config = self._config_row(row)
            missed = now - float(config["next_run_at"] or now) > 60
            policy = config["missed_run_policy"]
            should_run = not missed or policy == "run_once"
            next_at = self.next_run(config["cadence"], config["local_time"], config["timezone_name"], now)
            with self.store._connect() as conn:
                conn.execute(
                    "UPDATE autonomous_channel_configs SET next_run_at=?,updated_at=? WHERE id=? AND user_id=?",
                    (next_at, now, config["id"], config["user_id"]),
                )
            if should_run:
                key = f"scheduled-{config['id']}-{int(config['next_run_at'])}"
                try:
                    cycle, _ = self.start_cycle(
                        int(config["user_id"]), int(config["id"]), trigger="scheduled",
                        idempotency_key=key, advance=True,
                    )
                    output.append(cycle)
                except AutonomousOperatorError:
                    # A concurrency/budget window is a persisted next-tick concern,
                    # never a reason to burst-create missed cycles.
                    continue
        return output
