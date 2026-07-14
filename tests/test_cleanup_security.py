from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from disk_cleanup.cleaner.cleanup_plan import (
    CleanupPlanError,
    consume_approval,
    create_cleanup_plan,
    load_persisted_plan,
)
from disk_cleanup.cleaner.executor import execute_plan
from disk_cleanup.cleaner.session import CleanupSession, CleanupSessionError


class FakeRecycleBackend:
    def __init__(self, error: OSError | None = None) -> None:
        self.paths: list[Path] = []
        self.error = error

    def recycle(self, path: Path) -> None:
        self.paths.append(path)
        if self.error:
            raise self.error


def build_database(root: Path, risks: tuple[str, ...] = ("safe_cache",)) -> tuple[Path, list[str]]:
    db_path = root / "index.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE nodes (id INTEGER PRIMARY KEY, full_path TEXT, node_type TEXT);
            CREATE TABLE candidates (
                scan_id INTEGER, candidate_id TEXT, node_id INTEGER, backend TEXT,
                recommended_action TEXT, risk TEXT, reclaimable_bytes INTEGER
            );
            """
        )
        ids = []
        for index, risk in enumerate(risks, 1):
            candidate_id = f"C{index:012X}"
            target = root / f"cache-{index}.bin"
            target.write_bytes(b"cache")
            conn.execute("INSERT INTO nodes VALUES (?, ?, 'file')", (index, str(target)))
            conn.execute(
                "INSERT INTO candidates VALUES (1, ?, ?, 'file', 'recycle', ?, 5)",
                (candidate_id, index, risk),
            )
            ids.append(candidate_id)
    return db_path, ids


def test_plan_is_persisted_and_approval_is_single_use(tmp_path: Path) -> None:
    db_path, ids = build_database(tmp_path)
    plan = create_cleanup_plan(db_path, 1, ids, run_id="a" * 32)
    assert load_persisted_plan(tmp_path, plan.plan_hash).actions == plan.actions
    consume_approval(tmp_path, plan.plan_hash, plan.approval_code)
    with pytest.raises(CleanupPlanError, match="已使用"):
        consume_approval(tmp_path, plan.plan_hash, plan.approval_code)


def test_approval_expires_after_ten_minutes(tmp_path: Path) -> None:
    db_path, ids = build_database(tmp_path)
    now = datetime.now(timezone.utc)
    plan = create_cleanup_plan(db_path, 1, ids, now=now)
    with pytest.raises(CleanupPlanError, match="已过期"):
        consume_approval(tmp_path, plan.plan_hash, plan.approval_code, now=now + timedelta(minutes=11))


def test_risk_batches_are_separate_and_review_is_blocked(tmp_path: Path) -> None:
    db_path, ids = build_database(tmp_path, ("safe_cache", "safe_redownload", "review"))
    with pytest.raises(CleanupPlanError, match="独立审批批次"):
        create_cleanup_plan(db_path, 1, ids[:2])
    with pytest.raises(CleanupPlanError, match="不能进入"):
        create_cleanup_plan(db_path, 1, [ids[2]])


def test_truncated_scan_cannot_plan_directory(tmp_path: Path) -> None:
    db_path, ids = build_database(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE nodes SET node_type = 'directory' WHERE id = 1")
    with pytest.raises(CleanupPlanError, match="扫描结果不完整"):
        create_cleanup_plan(db_path, 1, ids, scan_truncated=True)


def test_tampered_immutable_plan_is_rejected(tmp_path: Path) -> None:
    db_path, ids = build_database(tmp_path)
    plan = create_cleanup_plan(db_path, 1, ids)
    plan_path = tmp_path / "plans" / f"{plan.plan_hash}.json"
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    payload["expected_reclaim_bytes"] = 999
    plan_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CleanupPlanError, match="内容哈希"):
        consume_approval(tmp_path, plan.plan_hash, plan.approval_code)


def test_fake_backend_and_failure_never_permanently_delete(tmp_path: Path) -> None:
    db_path, ids = build_database(tmp_path)
    plan = create_cleanup_plan(db_path, 1, ids, persist=False)
    target = Path(plan.actions[0].path)
    fake = FakeRecycleBackend(OSError("recycle unavailable"))
    result = execute_plan(plan, allowed_root=str(tmp_path), backend=fake, audit_path=tmp_path / "audit.jsonl")
    assert result["execution_status"] == "PARTIAL"
    assert result["bytes_moved_to_recycle_bin"] == 0
    assert target.exists()
    assert len(fake.paths) == 1
    assert fake.paths[0].parent == target.parent


def test_directory_content_added_after_approval_is_blocked(tmp_path: Path) -> None:
    db_path, ids = build_database(tmp_path)
    target = tmp_path / "cache-1.bin"
    target.unlink()
    target.mkdir()
    (target / "approved.tmp").write_bytes(b"approved")
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE nodes SET node_type = 'directory' WHERE id = 1")
    plan = create_cleanup_plan(db_path, 1, ids, persist=False)
    (target / "injected.txt").write_bytes(b"not approved")
    fake = FakeRecycleBackend()
    result = execute_plan(plan, allowed_root=str(tmp_path), backend=fake, audit_path=tmp_path / "audit.jsonl")
    assert result["actions"][0]["execution_status"] == "BLOCKED"
    assert target.exists()
    assert fake.paths == []


def test_unexpected_backend_failure_rolls_back_without_deleting(tmp_path: Path) -> None:
    class CrashingBackend:
        def recycle(self, path: Path) -> None:
            raise RuntimeError(f"unexpected failure: {path}")

    db_path, ids = build_database(tmp_path)
    plan = create_cleanup_plan(db_path, 1, ids, persist=False)
    result = execute_plan(plan, allowed_root=str(tmp_path), backend=CrashingBackend())
    assert result["execution_status"] == "PARTIAL"
    assert result["bytes_moved_to_recycle_bin"] == 0
    assert Path(plan.actions[0].path).exists()


def test_session_crash_recovery_moves_plan_to_needs_review(tmp_path: Path, monkeypatch) -> None:
    db_path, ids = build_database(tmp_path)
    session = CleanupSession(db_path, 1, allowed_root=str(tmp_path), run_id="a" * 32)
    session.selection(ids)
    preview = session.generate_preview()
    plan = preview["plan"]
    session.confirm(plan["plan_hash"], plan["approval_code"])

    def crash(*args, **kwargs):
        raise RuntimeError("simulated process failure")

    monkeypatch.setattr("disk_cleanup.cleaner.session.execute_plan", crash)
    with pytest.raises(CleanupSessionError, match="simulated process failure"):
        session.execute(plan["plan_hash"])

    state_path = tmp_path / "plans" / f"{plan['plan_hash']}.state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert session.state == "NEEDS_REVIEW"
    assert state["state"] == "NEEDS_REVIEW"
