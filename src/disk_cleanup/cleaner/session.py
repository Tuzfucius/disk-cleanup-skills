from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from disk_cleanup.cleaner.cleanup_plan import CleanupPlanError, create_cleanup_plan
from disk_cleanup.cleaner.executor import execute_plan, preview_plan
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
    selected_candidate_ids: list[str] = field(default_factory=list)
    plan: CleanupPlan | None = None
    state: str = "REVIEW_READY"
    preview: dict[str, Any] | None = None
    result: dict[str, Any] | None = None

    def selection(self, candidate_ids: list[str]) -> dict[str, Any]:
        self.selected_candidate_ids = list(candidate_ids)
        self.plan = None
        self.preview = None
        self.result = None
        self.state = "USER_SELECTED"
        return {"state": self.state, "candidate_ids": self.selected_candidate_ids}

    def generate_preview(self) -> dict[str, Any]:
        try:
            self.plan = create_cleanup_plan(self.db_path, self.scan_id, self.selected_candidate_ids, run_id=self.run_id, expires_at=self.expires_at)
        except CleanupPlanError as exc:
            raise CleanupSessionError(str(exc)) from exc
        self.preview = preview_plan(self.plan)
        self.state = "PREVIEWED"
        return {"state": self.state, **self.preview}

    def confirm(self, plan_hash: str) -> dict[str, Any]:
        self.require_plan_hash(plan_hash)
        if self.state != "PREVIEWED":
            raise CleanupSessionError("必须先预览，再确认")
        self.state = "USER_CONFIRMED"
        return {"state": self.state, "plan_hash": plan_hash}

    def execute(self, plan_hash: str, confirmation: str = "") -> dict[str, Any]:
        self.require_plan_hash(plan_hash)
        if self.state != "USER_CONFIRMED":
            raise CleanupSessionError("必须先确认，再执行")
        assert self.plan is not None
        expected = f"DELETE {self.run_id[:8]}" if self.run_id else "DELETE"
        if confirmation != expected:
            raise CleanupSessionError(f"确认短语不匹配，应为: {expected}")
        self.state = "EXECUTING"
        self.result = execute_plan(self.plan, allowed_root=self.allowed_root, protected_roots=self.protected_roots)
        final = self.result["execution_status"]
        self.state = "COMPLETED" if final == "COMPLETED" else "PARTIAL"
        return {"state": self.state, "result": self.result}

    def require_plan_hash(self, plan_hash: str) -> None:
        if self.plan is None:
            raise CleanupSessionError("清理计划不存在")
        if self.plan.plan_hash != plan_hash:
            raise CleanupSessionError("plan hash 不匹配")
