"""Durable execution ownership and provider reservations for channel workflows.

Connections belong to this module and are always closed. Schema additions are
independent of Store migrations so web and channel workers can upgrade separately.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Iterator, Optional
import uuid


class ExecutionBusy(RuntimeError):
    """Another executor still owns the resource."""


class ExecutionStopped(RuntimeError):
    """Ownership was lost or the user stopped the workflow."""


class WorkflowBudgetExceeded(RuntimeError):
    """A durable provider reservation would exceed a configured limit."""


@contextmanager
def connection(path: Path | str, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if immediate:
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_schema(path: Path | str) -> None:
    with connection(path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS channel_execution_leases (
                user_id INTEGER NOT NULL, resource TEXT NOT NULL,
                owner TEXT NOT NULL, expires_at REAL NOT NULL,
                PRIMARY KEY(user_id, resource)
            );
            CREATE TABLE IF NOT EXISTS channel_provider_usage (
                user_id INTEGER NOT NULL, run_id INTEGER NOT NULL,
                category TEXT NOT NULL, units INTEGER NOT NULL,
                PRIMARY KEY(user_id, run_id, category)
            );
            CREATE TABLE IF NOT EXISTS channel_upload_intents (
                user_id INTEGER NOT NULL, job_id INTEGER NOT NULL,
                state TEXT NOT NULL, updated_at REAL NOT NULL,
                PRIMARY KEY(user_id, job_id)
            );
        """)


class ExecutionLease:
    """Atomic, renewable ownership; an expired owner cannot update run state."""

    def __init__(self, path: Path | str, user_id: int, resource: str, *, ttl: float = 120) -> None:
        self.path, self.user_id, self.resource = path, user_id, resource
        self.owner, self.ttl = uuid.uuid4().hex, max(1., ttl)
        self._stop = threading.Event()
        self._heartbeat: Optional[threading.Thread] = None

    def __enter__(self) -> ExecutionLease:
        ensure_schema(self.path)
        now = time.time()
        with connection(self.path, immediate=True) as conn:
            cur = conn.execute(
                "INSERT INTO channel_execution_leases VALUES (?,?,?,?) "
                "ON CONFLICT(user_id,resource) DO UPDATE SET owner=excluded.owner,expires_at=excluded.expires_at "
                "WHERE channel_execution_leases.expires_at<=?",
                (self.user_id, self.resource, self.owner, now + self.ttl, now),
            )
            if not cur.rowcount:
                raise ExecutionBusy("Execution is already owned by an active worker; retry later.")
        self._heartbeat = threading.Thread(target=self._renew, daemon=True,
                                           name="channel-execution-lease")
        self._heartbeat.start()
        return self

    def _renew(self) -> None:
        while not self._stop.wait(self.ttl / 3):
            try:
                with connection(self.path, immediate=True) as conn:
                    now = time.time()
                    cur = conn.execute(
                        "UPDATE channel_execution_leases SET expires_at=? "
                        "WHERE user_id=? AND resource=? AND owner=? AND expires_at>?",
                        (now + self.ttl, self.user_id, self.resource, self.owner, now),
                    )
                    if not cur.rowcount:
                        return
            except sqlite3.Error:
                # Expiry, rather than an in-memory flag, fences every writer.
                return

    def check(self, conn: sqlite3.Connection) -> None:
        row = conn.execute(
            "SELECT 1 FROM channel_execution_leases "
            "WHERE user_id=? AND resource=? AND owner=? AND expires_at>?",
            (self.user_id, self.resource, self.owner, time.time()),
        ).fetchone()
        if not row:
            raise ExecutionStopped("Execution lease expired or changed owner.")

    def assert_owned(self) -> None:
        with connection(self.path) as conn:
            self.check(conn)

    def __exit__(self, *args: Any) -> None:
        self._stop.set()
        if self._heartbeat:
            self._heartbeat.join(timeout=1)
        with connection(self.path) as conn:
            conn.execute("DELETE FROM channel_execution_leases WHERE user_id=? AND resource=? AND owner=?",
                         (self.user_id, self.resource, self.owner))


def update_run(lease: ExecutionLease, run_id: int, data: dict[str, Any]) -> None:
    """Fence worker updates against cancellation, pause, and lease replacement."""
    with connection(lease.path, immediate=True) as conn:
        check_run(conn, lease, run_id)
        payload = dict(data)
        if "refs" in payload:
            refs = dict(payload.pop("refs"))
            usage = read_usage(conn, lease.user_id, run_id)
            if usage:
                refs["provider_usage"] = usage
            payload["refs_json"] = json.dumps(refs, sort_keys=True)
        payload["updated_at"] = time.time()
        conn.execute(
            "UPDATE automation_runs SET " + ",".join(f"{key}=?" for key in payload)
            + " WHERE user_id=? AND id=?",
            [*payload.values(), lease.user_id, run_id],
        )


def control_run(path: Path | str, user_id: int, run_id: int,
                data: dict[str, Any], allowed: set[str]) -> None:
    """Serialize user controls with worker completion without waiting for work."""
    with connection(path, immediate=True) as conn:
        row = conn.execute("SELECT status FROM automation_runs WHERE user_id=? AND id=?",
                           (user_id, run_id)).fetchone()
        if not row or row["status"] not in allowed:
            raise ExecutionStopped("Workflow state changed; the requested action is no longer available.")
        payload = dict(data)
        if "refs" in payload:
            payload["refs_json"] = json.dumps(payload.pop("refs"), sort_keys=True)
        payload["updated_at"] = time.time()
        conn.execute("UPDATE automation_runs SET " + ",".join(f"{key}=?" for key in payload)
                     + " WHERE user_id=? AND id=?", [*payload.values(), user_id, run_id])


def check_run(conn: sqlite3.Connection, lease: ExecutionLease, run_id: int) -> None:
    lease.check(conn)
    row = conn.execute("SELECT status FROM automation_runs WHERE user_id=? AND id=?",
                       (lease.user_id, run_id)).fetchone()
    if not row or row["status"] in {"paused", "cancelled", "completed"}:
        raise ExecutionStopped("Workflow is stopped or already completed.")


def read_usage(conn: sqlite3.Connection, user_id: int, run_id: int) -> dict[str, int]:
    return {row["category"]: int(row["units"]) for row in conn.execute(
        "SELECT category,units FROM channel_provider_usage WHERE user_id=? AND run_id=?",
        (user_id, run_id),
    )}


class StageBudget:
    """Reserve before dispatch; the first leaf consumes the prepaid stage call.

    Prepayment also bounds failures in custom handlers that have no instrumented
    provider boundary. Subsequent leaf operations each reserve their own unit.
    """

    def __init__(self, lease: ExecutionLease, run_id: int, category: str,
                 limits: dict[str, Any], refs: dict[str, Any]) -> None:
        self.lease, self.run_id, self.limits, self.refs = lease, run_id, limits, refs
        self.prepaid: Optional[str] = category
        self._reserve(category)

    def _reserve(self, category: str) -> None:
        keys = {"external_api_calls": "max_external_api_calls", "llm_calls": "max_llm_calls",
                "tts_requests": "max_tts_requests", "upload_attempts": "max_upload_attempts"}
        with connection(self.lease.path, immediate=True) as conn:
            check_run(conn, self.lease, self.run_id)
            # Preserve usage written by versions predating the independent ledger.
            row = conn.execute("SELECT refs_json FROM automation_runs WHERE user_id=? AND id=?",
                               (self.lease.user_id, self.run_id)).fetchone()
            persisted_refs = json.loads(row["refs_json"] or "{}")
            for key, value in (persisted_refs.get("provider_usage") or {}).items():
                conn.execute("INSERT OR IGNORE INTO channel_provider_usage VALUES (?,?,?,?)",
                             (self.lease.user_id, self.run_id, key, int(value)))
            usage = read_usage(conn, self.lease.user_id, self.run_id)
            total = int(usage.get("provider_calls", 0))
            for key, current in (("max_external_api_calls", total),
                                 (keys[category], int(usage.get(category, 0)))):
                if self.limits.get(key) is not None and current >= int(self.limits[key]):
                    raise WorkflowBudgetExceeded(f"Provider budget exceeded: {key}={self.limits[key]}.")
            usage["provider_calls"] = total + 1
            usage[category] = int(usage.get(category, 0)) + 1
            for key, value in usage.items():
                conn.execute("INSERT INTO channel_provider_usage VALUES (?,?,?,?) "
                             "ON CONFLICT(user_id,run_id,category) DO UPDATE SET units=excluded.units",
                             (self.lease.user_id, self.run_id, key, value))
            persisted_refs["provider_usage"] = usage
            conn.execute("UPDATE automation_runs SET refs_json=?,updated_at=? WHERE user_id=? AND id=?",
                         (json.dumps(persisted_refs, sort_keys=True), time.time(),
                          self.lease.user_id, self.run_id))
            self.refs["provider_usage"] = usage

    def consume(self, category: str) -> None:
        if self.prepaid == category:
            with connection(self.lease.path) as conn:
                check_run(conn, self.lease, self.run_id)
            self.prepaid = None
        else:
            self._reserve(category)


_BUDGET: ContextVar[Optional[StageBudget]] = ContextVar("channel_stage_budget", default=None)


@contextmanager
def budget_scope(budget: StageBudget) -> Iterator[None]:
    token = _BUDGET.set(budget)
    try:
        yield
    finally:
        _BUDGET.reset(token)


def reserve_provider_call(category: str) -> None:
    """No-op outside automation; count each actual leaf before its side effect."""
    budget = _BUDGET.get()
    if budget is not None:
        budget.consume(category)


def upload_intent(path: Path | str, user_id: int, job_id: int) -> Optional[str]:
    with connection(path) as conn:
        row = conn.execute("SELECT state FROM channel_upload_intents WHERE user_id=? AND job_id=?",
                           (user_id, job_id)).fetchone()
        return row["state"] if row else None


def set_upload_intent(lease: ExecutionLease, job_id: int, state: str) -> None:
    with connection(lease.path, immediate=True) as conn:
        lease.check(conn)
        conn.execute("INSERT INTO channel_upload_intents VALUES (?,?,?,?) "
                     "ON CONFLICT(user_id,job_id) DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at",
                     (lease.user_id, job_id, state, time.time()))
