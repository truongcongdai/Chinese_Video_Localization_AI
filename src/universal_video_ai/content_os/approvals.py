"""Bind human approval to immutable artifact bytes, independently of web schema."""
from __future__ import annotations

from .artifact_store import ArtifactStore
from .enums import ApprovalType, ArtifactType
from .exceptions import WorkflowError
from .repository import ContentOSRepository


class ArtifactApprovals:
    def __init__(self, repository: ContentOSRepository, artifacts: ArtifactStore) -> None:
        self.repository, self.artifacts = repository, artifacts
        with repository._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS content_os_approval_artifacts (
                approval_id INTEGER PRIMARY KEY, artifact_type TEXT NOT NULL,
                version INTEGER NOT NULL, checksum TEXT NOT NULL)""")

    def record(self, run_id: int, user_id: int, approval_type: ApprovalType,
               decision: str, note: str = "") -> None:
        run = self.repository.get_run(run_id, user_id)
        if run is None:
            raise WorkflowError("Run not found")
        identity = None
        if decision == "approved":
            identity = self.artifacts.describe(user_id, run.project_id, run_id, ArtifactType(approval_type.value))
        approval = self.repository.create_approval(run_id, user_id, approval_type.value, decision, note)
        if identity:
            # An interrupted write fails closed: an unbound approval cannot authorize production.
            with self.repository._connect() as conn:
                conn.execute("INSERT INTO content_os_approval_artifacts VALUES(?,?,?,?)",
                             (approval.id, identity["artifact_type"], identity["version"], identity["checksum"]))

    def read(self, run_id: int, user_id: int, approval_type: ApprovalType) -> dict:
        run = self.repository.get_run(run_id, user_id)
        if run is None:
            raise WorkflowError("Run not found")
        approval = self.repository.get_latest_approval(run_id, approval_type.value)
        if not approval or approval.user_id != user_id or approval.decision != "approved":
            raise WorkflowError(f"Current {approval_type.value} requires approval")
        with self.repository._connect() as conn:
            binding = conn.execute("SELECT * FROM content_os_approval_artifacts WHERE approval_id=?", (approval.id,)).fetchone()
        kind = ArtifactType(approval_type.value)
        identity = self.artifacts.describe(user_id, run.project_id, run_id, kind)
        if not binding or any(identity[key] != binding[key] for key in ("artifact_type", "version", "checksum")):
            raise WorkflowError(f"{approval_type.value.capitalize()} changed or has no artifact approval; approve the current version")
        return self.artifacts.read(user_id, run.project_id, run_id, kind, binding["version"])
