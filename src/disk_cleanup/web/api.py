from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from disk_cleanup.cleaner.selection import create_selected_plan_set
from disk_cleanup.indexer.queries import candidate_rows, children_by_node_id, extension_summary, largest_directories, scan_summary
from disk_cleanup.tasks import update_task


class AuditApi:
    def __init__(self, db_path: Path, scan_id: int, token: str, *, allowed_root: str = "C:\\", run_id: str = "", expires_at: str = "") -> None:
        self.db_path = db_path
        self.scan_id = scan_id
        self.token = token
        self.run_id = run_id

    def session(self) -> dict[str, Any]:
        metadata = json.loads((self.db_path.parent / "task.json").read_text(encoding="utf-8"))
        return {
            "scan_id": self.scan_id, "run_id": self.run_id, "mode": "plan_only",
            "token_required": True, "state": metadata.get("state", "SCANNED"),
        }

    def summary(self) -> dict[str, Any]:
        cached = self.db_path.parent / "report-summary.json"
        if cached.is_file():
            return json.loads(cached.read_text(encoding="utf-8"))
        return {
            "scan": scan_summary(self.db_path, self.scan_id),
            "top_directories": largest_directories(self.db_path, self.scan_id, 10),
            "extension_summary": extension_summary(self.db_path, self.scan_id, 10),
        }

    def tree_children(self, node_id: int | None, limit: int) -> list[dict[str, Any]]:
        return children_by_node_id(self.db_path, self.scan_id, node_id, limit)

    def candidates(self, limit: int) -> list[dict[str, Any]]:
        return candidate_rows(self.db_path, self.scan_id, limit)

    def create_plan(self, candidate_ids: list[str]) -> dict[str, Any]:
        metadata_path = self.db_path.parent / "task.json"
        metadata = __import__("json").loads(metadata_path.read_text(encoding="utf-8"))
        task = type("Task", (), {"metadata_path": metadata_path})()
        result = create_selected_plan_set(
            self.db_path, self.scan_id, candidate_ids,
            run_id=self.run_id,
            expires_at=str(metadata["expires_at"]),
            allowed_root=str(metadata["target"]),
            scan_fingerprint=str(metadata.get("scan_fingerprint", "")),
            rule_pack_hash=str(metadata.get("rule_pack_hash", "")),
            scan_truncated=bool(metadata.get("truncated", False)),
        )
        update_task(task, state="PLANNED", plan_hash=result["plans"][0]["plan_hash"])
        return result
