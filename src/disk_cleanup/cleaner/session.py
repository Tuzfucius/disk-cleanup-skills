from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from disk_cleanup.cleaner.audit import append_audit
from disk_cleanup.cleaner.cleanup_plan import (
    CleanupPlanError,
    consume_approval,
    create_cleanup_plan,
    load_persisted_plan,
    update_plan_state,
)
from disk_cleanup.cleaner.executor import execute_plan, preview_plan
from disk_cleanup.cleaner.recycle import RecycleBackend
from disk_cleanup.models import CleanupPlan


class CleanupSessionError(ValueError):
    """Raised when cleanup session state transition is invalid."""


@dataclass
class CleanupSession:
    db_path: Path
    scan_id: int
    allowed_root: str = "C:\\"
    run_id: str = ""
    expires_at: str = ""
    protected_roots: tuple[Path, ...] = ()
    audit_path: Path | None = None
    scan_fingerprint: str = ""
    rule_pack_hash: str = ""
    scan_truncated: bool = False
    selected_candidate_ids: list[str] = field(default_factory=list)
    plan: CleanupPlan | None = None
    state: str = "SCANNED"
    preview: dict[str, Any] | None = None
    result: dict[str, Any] | None = None

    @property
    def task_root(self) -> Path:
        return self.db_path.parent

    def selection(self, candidate_ids: list[str]) -> dict[str, Any]:
        if self.state not in {"SCANNED", "PLANNED", "NEEDS_REVIEW"}:
            raise CleanupSessionError("当前状态不能重新选择候选项")
        self.selected_candidate_ids = list(candidate_ids)
        self.plan = self.preview = self.result = None
        self.state = "SCANNED"
        return {"state": self.state, "candidate_ids": self.selected_candidate_ids}

    def generate_preview(self) -> dict[str, Any]:
        try:
            self.plan = create_cleanup_plan(
                self.db_path, self.scan_id, self.selected_candidate_ids,
                run_id=self.run_id, expires_at=self.expires_at,
                allowed_root=self.allowed_root, scan_fingerprint=self.scan_fingerprint,
                rule_pack_hash=self.rule_pack_hash, scan_truncated=self.scan_truncated,
            )
        except CleanupPlanError as exc:
            raise CleanupSessionError(str(exc)) from exc
        self.preview = preview_plan(self.plan)
        self.state = "PLANNED"
        append_audit(
            "cleanup_planned", path=self.audit_path, run_id=self.run_id,
            plan_hash=self.plan.plan_hash, risk_batch=self.plan.risk_batch,
            candidate_ids=self.selected_candidate_ids,
        )
        return {"state": self.state, **self.preview}

    def confirm(self, plan_hash: str, approval_code: str = "") -> dict[str, Any]:
        try:
            consume_approval(self.task_root, plan_hash, approval_code)
            self.plan = load_persisted_plan(self.task_root, plan_hash)
        except CleanupPlanError as exc:
            self.state = "NEEDS_REVIEW"
            raise CleanupSessionError(str(exc)) from exc
        self.state = "APPROVED"
        append_audit("cleanup_approved", path=self.audit_path, run_id=self.run_id, plan_hash=plan_hash)
        return {"state": self.state, "plan_hash": plan_hash}

    def execute(
        self,
        plan_hash: str,
        confirmation: str = "",
        *,
        backend: RecycleBackend | None = None,
    ) -> dict[str, Any]:
        if self.state != "APPROVED":
            self.confirm(plan_hash, confirmation)
        if self.plan is None or self.plan.plan_hash != plan_hash:
            raise CleanupSessionError("plan hash 不匹配")
        try:
            update_plan_state(self.task_root, plan_hash, "APPROVED", "EXECUTING")
            self.state = "EXECUTING"
            self.result = execute_plan(
                self.plan, allowed_root=self.allowed_root,
                protected_roots=self.protected_roots, backend=backend,
                audit_path=self.audit_path,
            )
            self.state = str(self.result["execution_status"])
            update_plan_state(self.task_root, plan_hash, "EXECUTING", self.state)
        except Exception as exc:
            self.state = "NEEDS_REVIEW"
            try:
                update_plan_state(self.task_root, plan_hash, "EXECUTING", "NEEDS_REVIEW")
            except (CleanupPlanError, OSError):
                pass
            raise CleanupSessionError(str(exc)) from exc
        return {"state": self.state, "result": self.result}

    def require_plan_hash(self, plan_hash: str) -> None:
        if self.plan is None or self.plan.plan_hash != plan_hash:
            raise CleanupSessionError("plan hash 不匹配")
