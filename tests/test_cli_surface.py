from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from disk_cleanup.cli import _assert_matching_scan_root, _database_scan_fingerprint, _is_volume_root, build_parser, main


def test_public_cli_exposes_only_scan_and_clean() -> None:
    parser = build_parser()
    action = next(action for action in parser._actions if action.dest == "command")
    assert set(action.choices) == {"scan", "clean"}


def test_scan_root_mismatch_is_rejected() -> None:
    _assert_matching_scan_root("C:\\", "c:\\")
    with pytest.raises(ValueError, match="根目录"):
        _assert_matching_scan_root("C:\\", "D:\\")


def test_volume_root_detection_distinguishes_subdirectory() -> None:
    assert _is_volume_root(Path("C:\\"))
    assert not _is_volume_root(Path("C:\\Users"))


def test_scan_fingerprint_binds_imported_node_content(tmp_path: Path) -> None:
    db_path = tmp_path / "scan.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE nodes (
                id INTEGER PRIMARY KEY, scan_id INTEGER, full_path TEXT,
                node_type TEXT, logical_bytes INTEGER, allocated_bytes INTEGER,
                subtree_allocated_bytes INTEGER, modified_at TEXT, attributes TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO nodes VALUES (1, 7, 'C:\\cache.bin', 'file', 4, 4096, 4096, '2026-01-01', 'A')"
        )
    before = _database_scan_fingerprint(db_path, 7)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE nodes SET modified_at = '2026-01-02' WHERE id = 1")
    after = _database_scan_fingerprint(db_path, 7)
    assert before != after


def test_scan_then_clean_creates_approval_plan(tmp_path: Path, capsys) -> None:
    target = tmp_path / "scan-root"
    cache = target / "project" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "module.pyc").write_bytes(b"cache")
    workspace = tmp_path / "workspace"
    workspace_toml = str(workspace).replace("\\", "\\\\")
    config = tmp_path / "config.toml"
    config.write_text(
        f'''config_version = 1
[tools]
wiztree_executable = "missing.exe"
[scan]
targets = ["C:"]
retain_scan_count = 1
[storage]
workspace = "{workspace_toml}"
database_name = "index.sqlite3"
[logging]
level = "INFO"
retain_days = 30
''',
        encoding="utf-8",
    )
    assert main(["--config", str(config), "scan", "--target", str(target), "--no-report"]) == 0
    scan = json.loads(capsys.readouterr().out)
    executable = [item for item in scan["cleanup_candidates"] if item["risk"] == "safe_cache"]
    assert executable
    assert main([
        "--config", str(config), "clean", "--run-id", scan["run_id"],
        "--candidate-id", executable[0]["candidate_id"],
    ]) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["state"] == "PLANNED"
    assert planned["plan"]["approval_code"].startswith("RECYCLE ")
