from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from disk_cleanup.analyzer.candidate_builder import analyze_scan
from disk_cleanup.cleaner.cleanup_plan import CleanupPlanError, create_cleanup_plan
from disk_cleanup.cleaner.cleanup_plan import plan_to_dict
from disk_cleanup.cleaner.session import CleanupSession, CleanupSessionError
from disk_cleanup.indexer.database import import_wiztree_csv
from disk_cleanup.indexer.queries import candidate_rows
from disk_cleanup.web.server import create_server, start_in_thread


class FakeRecycleBackend:
    def __init__(self) -> None:
        self.paths: list[Path] = []

    def recycle(self, path: Path) -> None:
        self.paths.append(path)
        path.rename(path.with_name(path.name + ".recycled"))


def test_cleanup_session_requires_preview_and_confirm(tmp_path: Path) -> None:
    db_path, scan_id, candidate_id, allowed_root = prepare_candidates(tmp_path)
    session = CleanupSession(db_path=db_path, scan_id=scan_id, allowed_root=str(allowed_root), run_id="a" * 32)

    session.selection([candidate_id])
    try:
        session.execute("bad")
        raise AssertionError("execute should fail before preview")
    except CleanupSessionError:
        pass

    preview = session.generate_preview()
    plan_hash = preview["plan"]["plan_hash"]
    approval_code = preview["plan"]["approval_code"]
    try:
        session.execute(plan_hash)
        raise AssertionError("execute should fail before confirm")
    except CleanupSessionError:
        pass

    try:
        session.confirm(plan_hash, "RECYCLE WRONG")
        raise AssertionError("execute should require exact confirmation")
    except CleanupSessionError:
        pass
    session.confirm(plan_hash, approval_code)
    backend = FakeRecycleBackend()
    result = session.execute(plan_hash, backend=backend)

    assert result["state"] == "COMPLETED"
    assert result["result"]["execution_status"] == "COMPLETED"
    assert result["result"]["actions"][0]["execution_status"] == "RECYCLED"
    assert backend.paths


def test_cleanup_plan_rejects_unknown_candidate(tmp_path: Path) -> None:
    db_path, scan_id, _candidate_id, _root = prepare_candidates(tmp_path)
    try:
        create_cleanup_plan(db_path, scan_id, ["CFFFFFFFFFFFF"])
        raise AssertionError("unknown candidate should fail")
    except CleanupPlanError as exc:
        assert "未知候选项" in str(exc)


def test_cleanup_plan_matches_public_schema(tmp_path: Path) -> None:
    db_path, scan_id, candidate_id, _root = prepare_candidates(tmp_path)
    plan = create_cleanup_plan(db_path, scan_id, [candidate_id], run_id="a" * 32)
    payload = plan_to_dict(plan)
    schema = json.loads((Path(__file__).resolve().parents[1] / "schemas" / "cleanup-plan.schema.json").read_text(encoding="utf-8"))

    assert_matches_schema_subset(payload, schema)


def test_cleanup_api_rejects_path_injection(tmp_path: Path) -> None:
    db_path, _scan_id, _candidate_id, _root = prepare_candidates(tmp_path)
    server = create_server(db_path, 1, token="test-token")
    thread = start_in_thread(server)
    try:
        base = server.url.split("?")[0].rstrip("/")
        request = Request(
            f"{base}/api/selection?token=test-token",
            data=json.dumps({"candidate_ids": ["C0001"], "path": "C:\\Windows"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urlopen(request, timeout=5)
            raise AssertionError("path injection should fail")
        except HTTPError as exc:
            assert exc.code == 405
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def prepare_candidates(tmp_path: Path) -> tuple[Path, int, str, Path]:
    fixture = Path(__file__).parent / "fixtures" / "sample-wiztree.csv"
    db_path = tmp_path / "index.sqlite3"
    summary = import_wiztree_csv(fixture, db_path)
    analyze_scan(db_path, summary.scan_id)
    candidate_id = candidate_rows(db_path, summary.scan_id, 1)[0]["candidate_id"]
    target = tmp_path / "scan-root"
    target.mkdir()
    cache_file = target / "cache.bin"
    cache_file.write_bytes(b"safe recycle test")
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE nodes SET full_path = ?, node_type = 'file' WHERE id = (SELECT node_id FROM candidates WHERE scan_id = ? AND candidate_id = ?)",
            (str(cache_file), summary.scan_id, candidate_id),
        )
        conn.execute(
            "UPDATE candidates SET risk = 'safe_cache' WHERE scan_id = ? AND candidate_id = ?",
            (summary.scan_id, candidate_id),
        )
    return db_path, summary.scan_id, candidate_id, target


def assert_matches_schema_subset(value, schema: dict) -> None:
    schema_type = schema.get("type")
    if schema_type == "object":
        assert isinstance(value, dict)
        assert set(value) >= set(schema.get("required", []))
        if schema.get("additionalProperties") is False:
            assert set(value) <= set(schema.get("properties", {}))
        for key, child_schema in schema.get("properties", {}).items():
            if key in value:
                assert_matches_schema_subset(value[key], child_schema)
    elif schema_type == "array":
        assert isinstance(value, list)
        assert len(value) >= schema.get("minItems", 0)
        for item in value:
            assert_matches_schema_subset(item, schema["items"])
    elif schema_type == "string":
        assert isinstance(value, str)
        assert len(value) >= schema.get("minLength", 0)
        if "enum" in schema:
            assert value in schema["enum"]
    elif schema_type == "integer":
        assert isinstance(value, int)
        assert value >= schema.get("minimum", value)
