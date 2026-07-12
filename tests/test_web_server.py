from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from disk_cleanup.analyzer.candidate_builder import analyze_scan
from disk_cleanup.indexer.database import import_wiztree_csv
from disk_cleanup.web.server import create_server, start_in_thread


def test_audit_server_api_requires_token(tmp_path: Path) -> None:
    db_path = prepare_db(tmp_path)
    server = create_server(db_path, 1, token="test-token")
    thread = start_in_thread(server)
    try:
        base = server.url.split("?")[0].rstrip("/")
        try:
            urlopen(f"{base}/api/summary", timeout=5)
            raise AssertionError("request should be unauthorized")
        except HTTPError as exc:
            assert exc.code == 401

        with urlopen(f"{base}/api/summary?token=test-token", timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
        assert data["scan"]["files"] == 4
        assert data["scan"]["candidate_count"] >= 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_audit_server_serves_html_and_tree(tmp_path: Path) -> None:
    db_path = prepare_db(tmp_path)
    server = create_server(db_path, 1, token="test-token")
    thread = start_in_thread(server)
    try:
        with urlopen(server.url, timeout=5) as response:
            html = response.read().decode("utf-8")
        assert "<main" in html
        assert "磁盘审计" in html

        base = server.url.split("?")[0].rstrip("/")
        with urlopen(f"{base}/api/tree/children?token=test-token", timeout=5) as response:
            rows = json.loads(response.read().decode("utf-8"))
        assert rows[0]["full_path"] == "C:\\"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def prepare_db(tmp_path: Path) -> Path:
    fixture = Path(__file__).parent / "fixtures" / "sample-wiztree.csv"
    db_path = tmp_path / "index.sqlite3"
    summary = import_wiztree_csv(fixture, db_path)
    analyze_scan(db_path, summary.scan_id)
    return db_path
