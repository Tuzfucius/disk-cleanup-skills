from __future__ import annotations

from pathlib import Path

from disk_cleanup.config import diagnose_config, load_config


def test_load_config_from_explicit_path(tmp_path: Path) -> None:
    wiztree = tmp_path / "WizTree64.exe"
    wiztree.write_text("", encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
config_version = 1

[tools]
wiztree_executable = "{str(wiztree).replace("\\", "\\\\")}"

[scan]
targets = ["C:"]
request_admin = true
include_files = true
include_folders = true
sort_by = "allocated"
retain_scan_count = 5

[storage]
workspace = "{str(tmp_path).replace("\\", "\\\\")}"
database_name = "index.sqlite3"

[cleanup]
file_delete_mode = "recycle_bin"
require_preview = true
verify_after_cleanup = true
""",
        encoding="utf-8",
    )

    config = load_config(config_path)
    diagnostic = diagnose_config(config_path)

    assert config.tools.wiztree_executable == wiztree
    assert config.scan.targets == ("C:",)
    assert diagnostic.ok

