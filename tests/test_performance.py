from __future__ import annotations

import csv
import os
import tracemalloc
from pathlib import Path

import pytest

from disk_cleanup.indexer.database import import_wiztree_csv


@pytest.mark.skipif(
    os.environ.get("DISK_CLEANUP_RUN_PERF") != "1",
    reason="set DISK_CLEANUP_RUN_PERF=1 for million-row acceptance tests",
)
@pytest.mark.parametrize("row_count", [1_000_000, 5_000_000])
def test_million_row_import_stays_below_512_mib(tmp_path: Path, row_count: int) -> None:
    csv_path = tmp_path / f"wiztree-{row_count}.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["WizTree synthetic performance fixture"])
        writer.writerow([
            "File Name", "Size", "Allocated", "Modified", "Attributes", "Files", "Folders",
            "DRIVECAPACITY", "FREESPACE", "USEDSPACE", "RESERVEDSPACE",
        ])
        writer.writerow(["C:\\", "0", "0", "", "", str(row_count), "0", "1", "1", "0", "0"])
        for index in range(row_count):
            writer.writerow([f"C:\\cache\\item-{index}.tmp", "1", "4096", "", "", "0", "0"])

    tracemalloc.start()
    summary = import_wiztree_csv(csv_path, tmp_path / "index.sqlite3")
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert summary.rows == row_count + 1
    assert peak <= 512 * 1024 * 1024
