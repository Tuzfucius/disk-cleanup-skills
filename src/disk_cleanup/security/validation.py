from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[3]


def validate_project(root: Path | None = None) -> dict[str, Any]:
    base = root or PACKAGE_ROOT
    checks: list[dict[str, Any]] = []
    checks.extend(validate_skill_frontmatter(base))
    checks.extend(validate_json_schemas(base))
    checks.append(check_exists(base / "README.md", "README.md"))
    checks.append(check_exists(base / "references" / "audit.md", "references/audit.md"))
    checks.append(check_exists(base / "references" / "clean.md", "references/clean.md"))
    checks.append(check_exists(base / "scripts" / "invoke-once.ps1", "scripts/invoke-once.ps1"))
    checks.append(check_exists(base / "config.example.toml", "config.example.toml"))
    return {"ok": all(check["ok"] for check in checks), "checks": checks}


def validate_skill_frontmatter(base: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    skill_paths = {
        "disk-cleanup-skills": base / "SKILL.md",
    }
    for skill_name, path in skill_paths.items():
        if not path.exists():
            checks.append({"name": f"{skill_name} SKILL.md", "ok": False, "message": "文件不存在"})
            continue

        text = path.read_text(encoding="utf-8")
        parts = text.split("---", 2)
        frontmatter = parts[1] if len(parts) > 2 else ""
        ok = text.startswith("---\n") and f"name: {skill_name}" in frontmatter and "description:" in frontmatter
        checks.append(
            {
                "name": f"{skill_name} frontmatter",
                "ok": ok,
                "message": "frontmatter 合法" if ok else "frontmatter 缺少必需字段",
            }
        )
    return checks


def validate_json_schemas(base: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for path in sorted((base / "schemas").glob("*.json")):
        try:
            json.loads(path.read_text(encoding="utf-8"))
            checks.append({"name": path.name, "ok": True, "message": "JSON 可解析"})
        except json.JSONDecodeError as exc:
            checks.append({"name": path.name, "ok": False, "message": str(exc)})
    return checks


def check_exists(path: Path, name: str) -> dict[str, Any]:
    return {"name": name, "ok": path.exists(), "message": "存在" if path.exists() else "缺失"}
