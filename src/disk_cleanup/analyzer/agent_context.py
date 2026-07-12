from __future__ import annotations

import json
from pathlib import Path

from disk_cleanup.indexer.queries import candidate_rows, extension_summary, largest_directories, largest_files, scan_summary


def build_agent_context(db_path: Path, scan_id: int) -> dict:
    return {
        "scan_summary": scan_summary(db_path, scan_id),
        "top_directories": largest_directories(db_path, scan_id, limit=30),
        "largest_files": largest_files(db_path, scan_id, limit=30),
        "extension_summary": extension_summary(db_path, scan_id, limit=30),
        "candidate_groups": group_candidates(candidate_rows(db_path, scan_id, limit=300)),
    }


def write_agent_context(db_path: Path, scan_id: int, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(build_agent_context(db_path, scan_id), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def group_candidates(rows: list[dict]) -> list[dict]:
    groups: dict[str, dict] = {}
    for row in rows:
        category = str(row["category"])
        group = groups.setdefault(
            category,
            {"category": category, "count": 0, "reclaimable_bytes": 0, "items": []},
        )
        group["count"] += 1
        group["reclaimable_bytes"] += int(row["reclaimable_bytes"])
        if len(group["items"]) < 20:
            group["items"].append(row)
    return sorted(groups.values(), key=lambda item: item["reclaimable_bytes"], reverse=True)
