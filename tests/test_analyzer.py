from __future__ import annotations

import sqlite3
from pathlib import Path

from disk_cleanup.analyzer.candidate_builder import analyze_scan
from disk_cleanup.indexer.database import import_wiztree_csv
from disk_cleanup.indexer.queries import candidate_rows


def test_analyze_scan_generates_candidates_and_context(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "sample-wiztree.csv"
    db_path = tmp_path / "index.sqlite3"
    context_path = tmp_path / "agent-context.json"
    summary = import_wiztree_csv(fixture, db_path)

    analysis = analyze_scan(db_path, summary.scan_id, context_path=context_path)
    rows = candidate_rows(db_path, summary.scan_id)

    assert analysis.candidate_count >= 1
    assert context_path.exists()
    assert any(row["category"] == "large_file" for row in rows) is False


def test_protected_windows_path_is_not_candidate(tmp_path: Path) -> None:
    db_path = tmp_path / "index.sqlite3"
    fixture = tmp_path / "sample.csv"
    fixture.write_text(
        "\n".join(
            [
                "生成由 WizTree 4.28 2026/7/8 10:08:09",
                "文件名称,大小,分配,修改时间,属性,文件,文件夹,DRIVECAPACITY,FREESPACE,USEDSPACE,RESERVEDSPACE",
                '"C:\\",1000,1000,2026/01/01 00:00:00,6,1,1,1000,0,1000,0',
                '"C:\\Windows\\Temp\\",900,900,2026/01/01 00:00:00,0,1,0',
                '"C:\\Windows\\Temp\\cache.bin",900,900,2026/01/01 00:00:00,32,0,0',
                    '"C:\\Users\\ExampleUser\\AppData\\Local\\Temp\\",800,800,2026/01/01 00:00:00,0,1,0',
                    '"C:\\Users\\ExampleUser\\AppData\\Local\\Temp\\cache.tmp",800,800,2026/01/01 00:00:00,32,0,0',
                ]
        ),
        encoding="utf-8",
    )
    summary = import_wiztree_csv(fixture, db_path)

    analyze_scan(db_path, summary.scan_id)
    rows = candidate_rows(db_path, summary.scan_id)

    assert rows
    assert all(not row["full_path"].startswith("C:\\Windows") for row in rows)
