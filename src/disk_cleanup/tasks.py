from __future__ import annotations

import json
import re
import shutil
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")


@dataclass(frozen=True)
class TaskRun:
    run_id: str
    root: Path
    db_path: Path
    metadata_path: Path
    expires_at: str


def task_root(workspace: Path) -> Path:
    return workspace.resolve() / "runs"


def create_task(workspace: Path, target: str, ttl_hours: int = 24) -> TaskRun:
    run_id = uuid.uuid4().hex
    root = task_root(workspace) / run_id
    root.mkdir(parents=True, exist_ok=False)
    expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    payload = {
        "run_id": run_id,
        "target": target,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires.isoformat(),
        "state": "AUDITED",
    }
    metadata = root / "task.json"
    metadata.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return TaskRun(run_id, root, root / "index.sqlite3", metadata, payload["expires_at"])


def load_task(workspace: Path, run_id: str) -> TaskRun:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("run_id 格式无效")
    base = task_root(workspace)
    root = (base / run_id).resolve()
    if root.parent != base.resolve() or not root.is_dir() or root.is_symlink():
        raise ValueError("任务不存在")
    metadata = root / "task.json"
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    if datetime.fromisoformat(payload["expires_at"]) <= datetime.now(timezone.utc):
        finalize_task(workspace, run_id)
        raise ValueError("任务已过期，请重新扫描")
    return TaskRun(run_id, root, root / "index.sqlite3", metadata, payload["expires_at"])


def update_task(task: TaskRun, **changes: object) -> dict[str, object]:
    payload = json.loads(task.metadata_path.read_text(encoding="utf-8"))
    payload.update(changes)
    temporary = task.metadata_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(task.metadata_path)
    return payload


def finalize_task(workspace: Path, run_id: str) -> None:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("run_id 格式无效")
    base = task_root(workspace).resolve()
    root = (base / run_id).resolve()
    if root.parent != base or root.name != run_id or root.is_symlink():
        raise ValueError("拒绝清理任务目录之外的路径")
    if root.exists():
        shutil.rmtree(root)


@contextmanager
def task_lock(task: TaskRun):
    lock_path = task.root / ".lock"
    handle = lock_path.open("a+b")
    try:
        if __import__("os").name == "nt":
            import msvcrt
            handle.seek(0)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        yield
    except OSError as exc:
        raise ValueError("任务正在由另一个进程处理") from exc
    finally:
        if __import__("os").name == "nt":
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        handle.close()


def cleanup_expired(workspace: Path) -> int:
    base = task_root(workspace)
    if not base.exists():
        return 0
    removed = 0
    for child in base.iterdir():
        if not child.is_dir() or not RUN_ID_RE.fullmatch(child.name):
            continue
        try:
            load_task(workspace, child.name)
        except (ValueError, OSError, json.JSONDecodeError):
            if child.exists():
                finalize_task(workspace, child.name)
            removed += 1
    return removed
