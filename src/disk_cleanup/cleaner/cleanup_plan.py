from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from disk_cleanup.models import CleanupAction, CleanupPlan


class CleanupPlanError(ValueError):
    """Raised when a cleanup plan cannot be created or validated."""


def create_cleanup_plan(db_path: Path, scan_id: int, candidate_ids: list[str], *, run_id: str = "", expires_at: str = "") -> CleanupPlan:
    normalized = normalize_candidate_ids(candidate_ids)
    if not normalized:
        raise CleanupPlanError("清理计划至少需要一个 candidate_id")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in normalized)
        rows = conn.execute(
            f"""
            SELECT c.candidate_id, c.node_id, c.backend, c.recommended_action, c.risk,
                   c.reclaimable_bytes, n.full_path
            FROM candidates c
            JOIN nodes n ON n.id = c.node_id
            WHERE c.scan_id = ? AND c.candidate_id IN ({placeholders})
            ORDER BY c.candidate_id
            """,
            (scan_id, *normalized),
        ).fetchall()

    found = {str(row["candidate_id"]) for row in rows}
    missing = [candidate_id for candidate_id in normalized if candidate_id not in found]
    if missing:
        raise CleanupPlanError(f"未知候选项: {', '.join(missing)}")

    actions = tuple(action_from_candidate(row) for row in rows)
    created_at = datetime.now(timezone.utc).isoformat()
    plan_id = f"cleanup-{scan_id}-{hashlib.sha256('|'.join(normalized).encode('utf-8')).hexdigest()[:12]}"
    expected = sum(action.expected_reclaim_bytes for action in actions)
    hash_input = {
        "plan_id": plan_id,
        "run_id": run_id,
        "expires_at": expires_at,
        "scan_id": scan_id,
        "candidate_ids": normalized,
        "actions": [asdict(action) for action in actions],
        "expected_reclaim_bytes": expected,
    }
    plan_hash = stable_hash(hash_input)
    return CleanupPlan(
        plan_id=plan_id,
        scan_id=scan_id,
        created_at=created_at,
        expected_reclaim_bytes=expected,
        actions=actions,
        plan_hash=plan_hash,
        run_id=run_id,
        expires_at=expires_at,
    )


def normalize_candidate_ids(candidate_ids: list[str]) -> list[str]:
    if not isinstance(candidate_ids, list):
        raise CleanupPlanError("candidate_ids 必须是数组")
    result: list[str] = []
    for candidate_id in candidate_ids:
        if not isinstance(candidate_id, str) or not __import__("re").fullmatch(r"C\d{4,}", candidate_id):
            raise CleanupPlanError("只能提交 candidate_id，不能提交路径或命令")
        if candidate_id not in result:
            result.append(candidate_id)
    return result


def action_from_candidate(row: sqlite3.Row) -> CleanupAction:
    backend = str(row["backend"])
    path = str(row["full_path"])
    from disk_cleanup.security.paths import file_identity
    volume_serial = file_id = modified_ns = None
    try:
        volume_serial, file_id, modified_ns, _size = file_identity(Path(path))
    except OSError as exc:
        raise CleanupPlanError(f"无法读取候选项身份，拒绝生成删除计划: {path}: {exc}") from exc
    return CleanupAction(
        candidate_id=str(row["candidate_id"]),
        node_id=int(row["node_id"]),
        backend=backend,
        action_type="recycle" if backend == "file" else "cleaner",
        action_value=path if backend == "file" else str(row["recommended_action"]),
        path=path,
        risk=str(row["risk"]),
        expected_reclaim_bytes=int(row["reclaimable_bytes"]),
        volume_serial=volume_serial,
        file_id=file_id,
        modified_ns=modified_ns,
    )


def stable_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def plan_to_dict(plan: CleanupPlan) -> dict[str, Any]:
    return {
        "plan_id": plan.plan_id,
        "scan_id": plan.scan_id,
        "created_at": plan.created_at,
        "expected_reclaim_bytes": plan.expected_reclaim_bytes,
        "plan_hash": plan.plan_hash,
        "run_id": plan.run_id,
        "expires_at": plan.expires_at,
        "actions": [asdict(action) for action in plan.actions],
    }

