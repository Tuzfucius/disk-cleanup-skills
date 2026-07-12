from __future__ import annotations

from pathlib import Path


def test_powershell_51_process_argument_fallback_is_present() -> None:
    script = (Path(__file__).resolve().parents[1] / "scripts" / "invoke-once.ps1").read_text(encoding="utf-8")
    assert "$null -ne $startInfo.ArgumentList" in script
    assert "$startInfo.Arguments =" in script
    assert "[int]$ExportMaxDepth = 0" in script
