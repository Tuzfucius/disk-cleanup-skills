from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from disk_cleanup.cleaner.audit import append_audit
from disk_cleanup.cleaner.recycle import RecycleBackend, WindowsIFileOperationBackend
from disk_cleanup.models import CleanupPlan
from disk_cleanup.security.paths import assert_execution_target, handle_identity


def preview_plan(plan: CleanupPlan) -> dict[str, Any]:
    return {
        "plan": plan_to_public(plan),
        "preview_status": "previewed",
        "actions": [
            {**asdict(action), "preview_status": "ok", "preview_message": "已锁定候选项身份，等待新一轮明确审批。"}
            for action in plan.actions
        ],
    }


def execute_plan(
    plan: CleanupPlan,
    *,
    allowed_root: str,
    protected_roots: tuple[Path, ...] = (),
    backend: RecycleBackend | None = None,
    audit_path: Path | None = None,
) -> dict[str, Any]:
    if plan.allowed_root and __import__("os").path.normcase(plan.allowed_root) != __import__("os").path.normcase(allowed_root):
        raise ValueError("计划绑定的扫描根与执行根不一致")
    recycler = backend or WindowsIFileOperationBackend()
    results: list[dict[str, Any]] = []
    moved = 0
    for action in plan.actions:
        try:
            if action.risk not in {"safe_cache", "safe_redownload"}:
                raise ValueError("风险门禁拒绝 review/protected 候选项")
            path, identity = assert_execution_target(
                action.path, allowed_root, protected_roots,
                allow_directory=action.node_type == "directory",
            )
            planned = (action.volume_serial, action.file_id, action.modified_ns, action.size_bytes)
            observed = (identity.volume_serial, identity.file_id, identity.modified_ns, identity.size_bytes)
            if None in planned or observed != planned:
                raise ValueError("目标自计划生成后已变化")
            # Keep the final handle identity check immediately adjacent to IFileOperation.
            adjacent = handle_identity(path)
            if adjacent != identity:
                raise ValueError("目标在回收执行前发生变化")
            recycler.recycle(path)
            if path.exists():
                status = "UNKNOWN"
                message = "回收站操作已返回，但原路径仍存在，不能视为成功。"
            else:
                status = "RECYCLED"
                message = "已移入 Windows 回收站；空间需在清空回收站后才会释放。"
                moved += action.expected_reclaim_bytes
        except ValueError as exc:
            status, message = "BLOCKED", str(exc)
        except OSError as exc:
            status, message = "FAILED", str(exc)
        row = {**asdict(action), "execution_status": status, "message": message}
        results.append(row)
        append_audit(
            "cleanup_action", path=audit_path, plan_hash=plan.plan_hash,
            candidate_id=action.candidate_id, target=action.path, status=status,
            expected_bytes=action.expected_reclaim_bytes,
        )
    final = "COMPLETED" if results and all(row["execution_status"] == "RECYCLED" for row in results) else "PARTIAL"
    append_audit(
        "cleanup_finished", path=audit_path, plan_hash=plan.plan_hash,
        status=final, bytes_moved_to_recycle_bin=moved,
    )
    return {
        "plan_id": plan.plan_id, "plan_hash": plan.plan_hash,
        "execution_status": final,
        "pending_reclaim_bytes": plan.expected_reclaim_bytes,
        "bytes_moved_to_recycle_bin": moved,
        "actions": results,
    }


def recycle_path(path: Path) -> None:
    """Compatibility entry point; intentionally has no permanent-delete fallback."""
    WindowsIFileOperationBackend().recycle(path)


def plan_to_public(plan: CleanupPlan) -> dict[str, Any]:
    from disk_cleanup.cleaner.cleanup_plan import plan_to_dict

    return plan_to_dict(plan)
