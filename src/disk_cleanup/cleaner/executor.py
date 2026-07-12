from __future__ import annotations

from dataclasses import asdict
from typing import Any

from pathlib import Path
from disk_cleanup.models import CleanupPlan
from disk_cleanup.security.paths import assert_deletable, file_identity


def preview_plan(plan: CleanupPlan) -> dict[str, Any]:
    return {
        "plan": plan_to_public(plan),
        "preview_status": "previewed",
        "actions": [
            {
                **asdict(action),
                "preview_status": "ok",
                "preview_message": "已验证为候选项派生动作，等待二次确认。",
            }
            for action in plan.actions
        ],
    }


def execute_plan(plan: CleanupPlan, *, allowed_root: str, protected_roots: tuple[Path, ...] = ()) -> dict[str, Any]:
    actions = []
    for action in plan.actions:
        actual = 0
        try:
            path = assert_deletable(action.path, allowed_root, protected_roots)
            identity = file_identity(path)
            if action.file_id is not None and (identity[0], identity[1], identity[2]) != (action.volume_serial, action.file_id, action.modified_ns):
                raise ValueError("目标自预览后已变化")
            if path.is_dir():
                raise ValueError("当前安全执行器仅允许回收单个文件，不递归删除目录")
            # Keep the identity check adjacent to the path-based Windows recycle call.
            if file_identity(path) != identity:
                raise ValueError("目标在执行前发生变化")
            recycle_path(path)
            status = "RECYCLED" if not path.exists() else "UNKNOWN"
            message = "已移入 Windows 回收站。" if status == "RECYCLED" else "回收站调用已返回，但目标仍存在。"
            actual = 0
        except ValueError as exc:
            status = "BLOCKED"
            message = str(exc)
        except OSError as exc:
            status = "FAILED"
            message = str(exc)
        actions.append(
            {
                **asdict(action),
                "execution_status": status,
                "actual_reclaimed_bytes": actual,
                "message": message,
            }
        )
    return {
        "plan_id": plan.plan_id,
        "plan_hash": plan.plan_hash,
        "execution_status": "COMPLETED" if all(a["execution_status"] == "RECYCLED" for a in actions) else "PARTIAL",
        "expected_reclaim_bytes": plan.expected_reclaim_bytes,
        "actual_reclaimed_bytes": sum(a["actual_reclaimed_bytes"] for a in actions),
        "actions": actions,
    }


def recycle_path(path: Path) -> None:
    if __import__("os").name != "nt":
        raise OSError("真实回收站删除仅支持 Windows")
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", wintypes.WORD),
                    ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", wintypes.LPVOID),
                    ("lpszProgressTitle", wintypes.LPCWSTR)]
    operation = SHFILEOPSTRUCTW(None, 3, str(path) + "\0\0", None, 0x40 | 0x10 | 0x400, False, None, None)
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if result or operation.fAnyOperationsAborted:
        raise OSError(f"回收站操作失败: {result}")


def plan_to_public(plan: CleanupPlan) -> dict[str, Any]:
    from disk_cleanup.cleaner.cleanup_plan import plan_to_dict

    return plan_to_dict(plan)

