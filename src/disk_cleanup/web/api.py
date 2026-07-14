from __future__ import annotations

from pathlib import Path
from typing import Any

from disk_cleanup.indexer.queries import candidate_rows, children_by_node_id, extension_summary, largest_directories, scan_summary


class AuditApi:
    def __init__(self, db_path: Path, scan_id: int, token: str, *, allowed_root: str = "C:\\", run_id: str = "", expires_at: str = "") -> None:
        self.db_path = db_path
        self.scan_id = scan_id
        self.token = token
        self.run_id = run_id

    def session(self) -> dict[str, Any]:
        return {"scan_id": self.scan_id, "run_id": self.run_id, "mode": "read_only", "token_required": True}

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
