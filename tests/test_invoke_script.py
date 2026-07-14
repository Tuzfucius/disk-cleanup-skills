from __future__ import annotations

from pathlib import Path


def test_powershell_exposes_only_scan_and_clean_modes() -> None:
    script = (Path(__file__).resolve().parents[1] / "scripts" / "invoke-once.ps1").read_text(encoding="utf-8")
    assert '[ValidateSet("scan", "clean")]' in script
    assert '"--approval-code", $ApprovalCode' in script
    assert '"scan", "--target", $Target' in script
