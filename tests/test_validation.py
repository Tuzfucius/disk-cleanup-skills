from __future__ import annotations

import json
from pathlib import Path

from disk_cleanup.security.validation import validate_project


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_project_validation_passes() -> None:
    result = validate_project(PROJECT_ROOT)
    assert result["ok"] is True
    check_names = {check["name"] for check in result["checks"]}
    assert "disk-cleanup-skills frontmatter" in check_names
    assert "README.md" in check_names
    assert "references/audit.md" in check_names
    assert "references/clean.md" in check_names
    assert "scripts/invoke-once.ps1" in check_names


def test_schema_files_are_valid_json() -> None:
    schema_dir = PROJECT_ROOT / "schemas"
    names = {path.name for path in schema_dir.glob("*.json")}
    assert names == {
        "agent-context.schema.json",
        "cleanup-plan.schema.json",
        "config.schema.json",
        "scan-result.schema.json",
    }

    for path in schema_dir.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["$schema"] == "https://json-schema.org/draft/2020-12/schema"
