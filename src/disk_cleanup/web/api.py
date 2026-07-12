from __future__ import annotations

from pathlib import Path
from typing import Any

from disk_cleanup.cleaner.session import CleanupSession, CleanupSessionError
from disk_cleanup.indexer.queries import candidate_rows, children_by_node_id, extension_summary, largest_directories, scan_summary


class AuditApi:
    def __init__(self, db_path: Path, scan_id: int, token: str, *, allowed_root: str = "C:\\", run_id: str = "", expires_at: str = "") -> None:
        self.db_path = db_path
        self.scan_id = scan_id
        self.token = token
        self.cleanup = CleanupSession(db_path=db_path, scan_id=scan_id, allowed_root=allowed_root, run_id=run_id, expires_at=expires_at, protected_roots=(db_path.parent,))

    def session(self) -> dict[str, Any]:
        return {"scan_id": self.scan_id, "mode": "clean", "token_required": True, "cleanup_state": self.cleanup.state}

    def summary(self) -> dict[str, Any]:
        return {
            "scan": scan_summary(self.db_path, self.scan_id),
            "top_directories": largest_directories(self.db_path, self.scan_id, 10),
            "extension_summary": extension_summary(self.db_path, self.scan_id, 10),
        }

    def tree_children(self, node_id: int | None, limit: int) -> list[dict[str, Any]]:
        return children_by_node_id(self.db_path, self.scan_id, node_id, limit)

    def candidates(self, limit: int) -> list[dict[str, Any]]:
        return candidate_rows(self.db_path, self.scan_id, limit)

    def selection(self, payload: dict[str, Any]) -> dict[str, Any]:
        require_keys(payload, {"candidate_ids"})
        return self.cleanup.selection(payload["candidate_ids"])

    def preview(self) -> dict[str, Any]:
        return self.cleanup.generate_preview()

    def confirm(self, payload: dict[str, Any]) -> dict[str, Any]:
        require_keys(payload, {"plan_hash"})
        return self.cleanup.confirm(str(payload["plan_hash"]))

    def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise CleanupSessionError("Web 界面仅用于审查；真实删除必须通过带 run_id 的 CLI 执行")


def require_keys(payload: dict[str, Any], allowed: set[str]) -> None:
    unknown = set(payload) - allowed
    if unknown:
        raise CleanupSessionError(f"不允许提交字段: {', '.join(sorted(unknown))}")
