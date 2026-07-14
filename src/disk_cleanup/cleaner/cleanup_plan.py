from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from disk_cleanup.models import CleanupAction, CleanupPlan

EXECUTABLE_RISKS = frozenset({"safe_cache", "safe_redownload"})
LEGACY_RISK_MAP = {"low": "safe_cache", "medium": "safe_redownload", "high": "review"}
CANDIDATE_ID_RE = re.compile(r"^C[0-9A-F]{12}$")


class CleanupPlanError(ValueError):
    """Raised when a cleanup plan cannot be created or validated."""


def create_cleanup_plan(
    db_path: Path,
    scan_id: int,
    candidate_ids: list[str],
    *,
    run_id: str = "",
    expires_at: str = "",
    persist: bool = True,
    now: datetime | None = None,
) -> CleanupPlan:
    normalized = normalize_candidate_ids(candidate_ids)
    if not normalized:
        raise CleanupPlanError("清理计划至少需要一个 candidate_id")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in normalized)
        rows = conn.execute(
            f"""
            SELECT c.candidate_id, c.node_id, c.backend, c.recommended_action, c.risk,
                   c.reclaimable_bytes, n.full_path, n.node_type
            FROM candidates c JOIN nodes n ON n.id = c.node_id
            WHERE c.scan_id = ? AND c.candidate_id IN ({placeholders})
            ORDER BY c.candidate_id
            """,
            (scan_id, *normalized),
        ).fetchall()
    found = {str(row["candidate_id"]) for row in rows}
    missing = [value for value in normalized if value not in found]
    if missing:
        raise CleanupPlanError(f"未知候选项: {', '.join(missing)}")
    risks = {_normalized_risk(str(row["risk"])) for row in rows}
    blocked = risks - EXECUTABLE_RISKS
    if blocked:
        raise CleanupPlanError(f"review/protected 候选项不能进入清理计划: {', '.join(sorted(blocked))}")
    if len(risks) != 1:
        raise CleanupPlanError("safe_cache 与 safe_redownload 必须生成独立审批批次")
    actions = tuple(action_from_candidate(row) for row in rows)
    clock = now or datetime.now(timezone.utc)
    created_at = clock.isoformat()
    approval_expires_at = (clock + timedelta(minutes=10)).isoformat()
    approval_code = f"RECYCLE {secrets.token_hex(4).upper()}"
    plan_id = f"cleanup-{scan_id}-{secrets.token_hex(6)}"
    expected = sum(action.expected_reclaim_bytes for action in actions)
    hash_input = {
        "plan_id": plan_id, "run_id": run_id, "expires_at": expires_at,
        "scan_id": scan_id, "created_at": created_at, "risk_batch": next(iter(risks)),
        "approval_expires_at": approval_expires_at,
        "actions": [asdict(action) for action in actions],
        "expected_reclaim_bytes": expected,
    }
    plan = CleanupPlan(
        plan_id=plan_id, scan_id=scan_id, created_at=created_at,
        expected_reclaim_bytes=expected, actions=actions,
        plan_hash=stable_hash(hash_input), run_id=run_id, expires_at=expires_at,
        risk_batch=next(iter(risks)), approval_expires_at=approval_expires_at,
        approval_code=approval_code,
    )
    if persist:
        persist_plan(db_path.parent, plan)
    return plan


def normalize_candidate_ids(candidate_ids: list[str]) -> list[str]:
    if not isinstance(candidate_ids, list):
        raise CleanupPlanError("candidate_ids 必须是数组")
    result: list[str] = []
    for candidate_id in candidate_ids:
        if not isinstance(candidate_id, str) or not CANDIDATE_ID_RE.fullmatch(candidate_id):
            raise CleanupPlanError("只能提交 C 加 12 位大写十六进制 candidate_id")
        if candidate_id not in result:
            result.append(candidate_id)
    return result


def action_from_candidate(row: sqlite3.Row) -> CleanupAction:
    backend = str(row["backend"])
    if backend not in {"file", "recycle"}:
        raise CleanupPlanError("仅允许回收站候选项，拒绝 cleaner 或命令后端")
    path = str(row["full_path"])
    from disk_cleanup.security.paths import file_identity
    try:
        volume_serial, file_id, modified_ns, size_bytes = file_identity(Path(path))
    except OSError as exc:
        raise CleanupPlanError(f"无法读取候选项身份，拒绝生成清理计划: {path}: {exc}") from exc
    return CleanupAction(
        candidate_id=str(row["candidate_id"]), node_id=int(row["node_id"]), backend="file",
        action_type="recycle", action_value=path, path=path,
        risk=_normalized_risk(str(row["risk"])),
        expected_reclaim_bytes=int(row["reclaimable_bytes"]),
        volume_serial=volume_serial, file_id=file_id, modified_ns=modified_ns,
        size_bytes=size_bytes, node_type=str(row["node_type"]),
    )


def _normalized_risk(value: str) -> str:
    return LEGACY_RISK_MAP.get(value, value)


def stable_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def plan_to_dict(plan: CleanupPlan, *, include_approval_code: bool = True) -> dict[str, Any]:
    payload = {
        "plan_id": plan.plan_id, "scan_id": plan.scan_id, "created_at": plan.created_at,
        "expected_reclaim_bytes": plan.expected_reclaim_bytes, "plan_hash": plan.plan_hash,
        "run_id": plan.run_id, "expires_at": plan.expires_at, "risk_batch": plan.risk_batch,
        "approval_expires_at": plan.approval_expires_at,
        "actions": [asdict(action) for action in plan.actions],
    }
    if include_approval_code:
        payload["approval_code"] = plan.approval_code
    return payload


def persist_plan(task_root: Path, plan: CleanupPlan) -> None:
    plans = task_root / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    immutable_path = plans / f"{plan.plan_hash}.json"
    state_path = plans / f"{plan.plan_hash}.state.json"
    immutable = json.dumps(plan_to_dict(plan, include_approval_code=False), ensure_ascii=False, sort_keys=True, indent=2)
    try:
        with immutable_path.open("x", encoding="utf-8") as handle:
            handle.write(immutable)
    except FileExistsError as exc:
        raise CleanupPlanError("计划哈希已存在，拒绝覆盖不可变计划") from exc
    state = {
        "state": "PLANNED", "approval_digest": _approval_digest(plan.plan_hash, plan.approval_code),
        "approval_expires_at": plan.approval_expires_at, "used_at": None,
    }
    _atomic_json(state_path, state)


def consume_approval(task_root: Path, plan_hash: str, approval_code: str, *, now: datetime | None = None) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{64}", plan_hash):
        raise CleanupPlanError("plan hash 格式无效")
    plans = task_root / "plans"
    immutable_path = plans / f"{plan_hash}.json"
    state_path = plans / f"{plan_hash}.state.json"
    if not immutable_path.is_file() or not state_path.is_file():
        raise CleanupPlanError("持久化清理计划不存在")
    payload = json.loads(immutable_path.read_text(encoding="utf-8"))
    _verify_persisted_payload(payload, plan_hash)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("state") != "PLANNED" or state.get("used_at") is not None:
        raise CleanupPlanError("审批码已使用或计划状态无效")
    clock = now or datetime.now(timezone.utc)
    if datetime.fromisoformat(state["approval_expires_at"]) <= clock:
        state["state"] = "NEEDS_REVIEW"
        _atomic_json(state_path, state)
        raise CleanupPlanError("审批码已过期，请重新生成计划")
    supplied = _approval_digest(plan_hash, approval_code)
    if not hmac.compare_digest(supplied, str(state["approval_digest"])):
        raise CleanupPlanError("审批码不匹配")
    # An exclusive marker closes the cross-process race before the mutable
    # state file is replaced. A crash after this point fails closed.
    claim_path = plans / f"{plan_hash}.approval-used"
    try:
        descriptor = os.open(claim_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise CleanupPlanError("审批码已使用") from exc
    else:
        with os.fdopen(descriptor, "w", encoding="ascii") as claim:
            claim.write(clock.isoformat())
            claim.flush()
            os.fsync(claim.fileno())
    state.update({"state": "APPROVED", "used_at": clock.isoformat()})
    _atomic_json(state_path, state)
    return payload


def load_persisted_plan(task_root: Path, plan_hash: str) -> CleanupPlan:
    path = task_root / "plans" / f"{plan_hash}.json"
    if not path.is_file():
        raise CleanupPlanError("持久化清理计划不存在")
    payload = json.loads(path.read_text(encoding="utf-8"))
    _verify_persisted_payload(payload, plan_hash)
    return CleanupPlan(
        plan_id=str(payload["plan_id"]), scan_id=int(payload["scan_id"]),
        created_at=str(payload["created_at"]),
        expected_reclaim_bytes=int(payload["expected_reclaim_bytes"]),
        actions=tuple(CleanupAction(**item) for item in payload["actions"]),
        plan_hash=plan_hash, run_id=str(payload.get("run_id", "")),
        expires_at=str(payload.get("expires_at", "")),
        risk_batch=str(payload["risk_batch"]),
        approval_expires_at=str(payload["approval_expires_at"]), approval_code="",
    )


def _verify_persisted_payload(payload: dict[str, Any], plan_hash: str) -> None:
    if payload.get("plan_hash") != plan_hash:
        raise CleanupPlanError("持久化计划已被篡改")
    hash_input = {
        "plan_id": payload["plan_id"], "run_id": payload.get("run_id", ""),
        "expires_at": payload.get("expires_at", ""), "scan_id": payload["scan_id"],
        "created_at": payload["created_at"], "risk_batch": payload["risk_batch"],
        "approval_expires_at": payload["approval_expires_at"],
        "actions": payload["actions"],
        "expected_reclaim_bytes": payload["expected_reclaim_bytes"],
    }
    if not hmac.compare_digest(stable_hash(hash_input), plan_hash):
        raise CleanupPlanError("持久化计划内容哈希不匹配")


def update_plan_state(task_root: Path, plan_hash: str, expected: str, new_state: str) -> None:
    state_path = task_root / "plans" / f"{plan_hash}.state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("state") != expected:
        raise CleanupPlanError(f"计划状态必须为 {expected}")
    state["state"] = new_state
    _atomic_json(state_path, state)


def _approval_digest(plan_hash: str, approval_code: str) -> str:
    return hashlib.sha256(f"{plan_hash}\0{approval_code}".encode("utf-8")).hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    temporary.replace(path)
