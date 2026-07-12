from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from disk_cleanup.tasks import create_task, finalize_task, load_task


def test_task_can_resume_and_finalize(tmp_path: Path) -> None:
    task = create_task(tmp_path, "C:\\")
    resumed = load_task(tmp_path, task.run_id)
    assert resumed.run_id == task.run_id
    finalize_task(tmp_path, task.run_id)
    assert not task.root.exists()


def test_expired_task_is_removed(tmp_path: Path) -> None:
    task = create_task(tmp_path, "C:\\")
    payload = json.loads(task.metadata_path.read_text(encoding="utf-8"))
    payload["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    task.metadata_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="过期"):
        load_task(tmp_path, task.run_id)
    assert not task.root.exists()


def test_invalid_run_id_cannot_escape_workspace(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        finalize_task(tmp_path, "..")
