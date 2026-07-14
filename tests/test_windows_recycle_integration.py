from __future__ import annotations

import os
from pathlib import Path

import pytest

from disk_cleanup.cleaner.recycle import WindowsIFileOperationBackend


@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("DISK_CLEANUP_RUN_RECYCLE_INTEGRATION") != "1",
    reason="set DISK_CLEANUP_RUN_RECYCLE_INTEGRATION=1 for the real Recycle Bin test",
)
def test_ifileoperation_moves_opt_in_fixture_to_recycle_bin(tmp_path: Path) -> None:
    target = tmp_path / "disk-cleanup-integration-fixture.tmp"
    target.write_bytes(b"explicit opt-in integration fixture")

    WindowsIFileOperationBackend().recycle(target)

    assert not target.exists()
