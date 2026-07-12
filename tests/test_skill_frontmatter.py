from __future__ import annotations

from pathlib import Path


def test_skill_frontmatter_has_required_fields() -> None:
    root = Path(__file__).resolve().parents[1]
    skill_paths = {
        "disk-cleanup-skills": root / "SKILL.md",
    }
    for skill_name, skill_path in skill_paths.items():
        text = skill_path.read_text(encoding="utf-8")

        assert text.startswith("---\n")
        frontmatter = text.split("---", 2)[1]
        assert f"name: {skill_name}" in frontmatter
        assert "description:" in frontmatter
