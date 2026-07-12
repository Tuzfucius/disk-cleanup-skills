from __future__ import annotations

from pathlib import Path

import pytest

from disk_cleanup.security.paths import assert_deletable


def test_rejects_root_and_system_paths() -> None:
    with pytest.raises(ValueError):
        assert_deletable("C:\\", "C:\\")
    with pytest.raises(ValueError):
        assert_deletable("C:\\Windows\\Temp", "C:\\")


def test_rejects_unc_device_and_ads() -> None:
    for value in ("\\\\server\\share\\x", "\\\\?\\C:\\x", "C:\\x:file"):
        with pytest.raises(ValueError):
            assert_deletable(value, "C:\\")
