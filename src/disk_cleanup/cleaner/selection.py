from __future__ import annotations

import json
import ntpath
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from disk_cleanup.cleaner.cleanup_plan import CleanupPlanError, create_cleanup_plan, plan_to_dict
from disk_cleanup.models import CleanupPlan

SELECTION_PLAN_NAME = "selected-plan.json"


def create_selected_plan_set(
    db_path: Path,
    scan_id: int,
    candidate_ids: list[str],
    *,
    run_id: str,
    expires_at: str,
    allowed_root: str,
    scan_fingerprint: str,
    rule_pack_hash: str,
    scan_truncated: bool,
) -> dict[str, Any]:
    """Persist one or two immutable plans selected from the local report."""
    rows = _selected_rows(db_path, scan_id, candidate_ids)
    _reject_overlapping_paths(rows)
    by_risk: dict[str, list[str]] = {}
    for row in rows:
        risk = str(row["risk"])
        if risk not in {"safe_cache", "safe_redownload"}:
            raise CleanupPlanError("仅可将绿色或黄色可执行候选项加入清理计划")
        by_risk.setdefault(risk, []).append(str(row["candidate_id"]))

    plans: list[CleanupPlan] = []
    for risk in ("safe_cache", "safe_redownload"):
        ids = by_risk.get(risk)
        if ids:
            plans.append(create_cleanup_plan(
                db_path, scan_id, ids, run_id=run_id, expires_at=expires_at,
                allowed_root=allowed_root, scan_fingerprint=scan_fingerprint,
                rule_pack_hash=rule_pack_hash, scan_truncated=scan_truncated,
            ))
    if not plans:
        raise CleanupPlanError("未选择可执行候选项")

    payload = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "state": "PLANNED",
        "plans": [
            {
                "plan_hash": plan.plan_hash,
                "approval_code": plan.approval_code,
                "risk_batch": plan.risk_batch,
                "expected_reclaim_bytes": plan.expected_reclaim_bytes,
            }
            for plan in plans
        ],
    }
    _atomic_json(db_path.parent / SELECTION_PLAN_NAME, payload)
    return {
        "state": "PLANNED",
        "plans": [plan_to_dict(plan, include_approval_code=False) for plan in plans],
        "expected_reclaim_bytes": sum(plan.expected_reclaim_bytes for plan in plans),
    }


def load_selected_plan_set(task_root: Path) -> dict[str, Any]:
    path = task_root / SELECTION_PLAN_NAME
    if not path.is_file():
        raise CleanupPlanError("网页尚未生成清理计划")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("state") != "PLANNED" or not isinstance(payload.get("plans"), list):
        raise CleanupPlanError("网页清理计划已使用或状态无效")
    return payload


def consume_selected_plan_set(task_root: Path) -> dict[str, Any]:
    payload = load_selected_plan_set(task_root)
    payload["state"] = "EXECUTING"
    _atomic_json(task_root / SELECTION_PLAN_NAME, payload)
    return payload


def finish_selected_plan_set(task_root: Path, state: str) -> None:
    path = task_root / SELECTION_PLAN_NAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["state"] = state
    _atomic_json(path, payload)


def _selected_rows(db_path: Path, scan_id: int, candidate_ids: list[str]) -> list[sqlite3.Row]:
    if not isinstance(candidate_ids, list) or not candidate_ids:
        raise CleanupPlanError("至少选择一个 candidate_id")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in candidate_ids)
        rows = conn.execute(
            f"""
            SELECT c.candidate_id, c.risk, n.full_path
            FROM candidates c JOIN nodes n ON n.id = c.node_id
            WHERE c.scan_id = ? AND c.candidate_id IN ({placeholders})
            """,
            (scan_id, *candidate_ids),
        ).fetchall()
    if len({str(row["candidate_id"]) for row in rows}) != len(set(candidate_ids)):
        raise CleanupPlanError("包含未知 candidate_id")
    return rows


def _reject_overlapping_paths(rows: list[sqlite3.Row]) -> None:
    paths = sorted((ntpath.normcase(ntpath.normpath(str(row["full_path"]))) for row in rows), key=len)
    for index, parent in enumerate(paths):
        prefix = parent.rstrip("\\/") + "\\"
        if any(child.startswith(prefix) for child in paths[index + 1:]):
            raise CleanupPlanError("不能同时选择父目录及其子项，请仅保留一个目标")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    temporary.replace(path)
