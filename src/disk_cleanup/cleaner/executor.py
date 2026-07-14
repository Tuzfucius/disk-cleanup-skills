from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from disk_cleanup.cleaner.audit import append_audit
from disk_cleanup.cleaner.recycle import RecycleBackend, WindowsIFileOperationBackend
from disk_cleanup.models import CleanupPlan
from disk_cleanup.security.paths import assert_execution_target, directory_manifest, handle_identity


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
        staged_path: Path | None = None
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
            if action.node_type == "directory":
                digest, count = directory_manifest(path)
                if digest != action.tree_digest or count != action.descendant_count:
                    raise ValueError("目录内容自审批后已变化")
            # Keep the final handle identity check immediately adjacent to IFileOperation.
            adjacent = handle_identity(path)
            if adjacent != identity:
                raise ValueError("目标在回收执行前发生变化")
            staged_path = _stage_target(path, plan.plan_id, action.candidate_id)
            staged_identity = handle_identity(staged_path)
            staged_observed = (
                staged_identity.volume_serial,
                staged_identity.file_id,
                staged_identity.modified_ns,
                staged_identity.size_bytes,
            )
            if staged_observed != observed:
                raise ValueError("目标隔离后身份发生变化")
            if action.node_type == "directory":
                digest, count = directory_manifest(staged_path)
                if digest != action.tree_digest or count != action.descendant_count:
                    raise ValueError("目录隔离后内容发生变化")
            recycler.recycle(staged_path)
            if staged_path.exists():
                status = "UNKNOWN"
                message = "回收站操作已返回，但隔离路径仍存在，不能视为成功。"
            else:
                status = "RECYCLED"
                message = "已移入 Windows 回收站；空间需在清空回收站后才会释放。"
                moved += action.expected_reclaim_bytes
        except ValueError as exc:
            status, message = "BLOCKED", str(exc)
        except OSError as exc:
            status, message = "FAILED", str(exc)
        except Exception as exc:
            status, message = "FAILED", f"unexpected recycle failure: {exc}"
        if staged_path is not None and staged_path.exists():
            try:
                if not path.exists():
                    staged_path.rename(path)
            except OSError as rollback_error:
                status = "FAILED"
                message = f"{message}；回滚失败，保留路径: {staged_path}: {rollback_error}"
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


def _stage_target(path: Path, plan_id: str, candidate_id: str) -> Path:
    suffix = f".{plan_id[-12:]}.{candidate_id}.disk-cleanup-pending"
    staged = path.with_name(f".{path.name}{suffix}")
    if staged.exists():
        raise ValueError("隔离路径已存在，拒绝覆盖")
    path.rename(staged)
    return staged


def recycle_path(path: Path) -> None:
    """Compatibility entry point; intentionally has no permanent-delete fallback."""
    WindowsIFileOperationBackend().recycle(path)


def plan_to_public(plan: CleanupPlan) -> dict[str, Any]:
    from disk_cleanup.cleaner.cleanup_plan import plan_to_dict

    return plan_to_dict(plan)
