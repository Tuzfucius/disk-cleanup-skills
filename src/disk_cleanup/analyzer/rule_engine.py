from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Rule:
    id: str
    category: str
    path_regex: re.Pattern[str]
    risk: str
    confidence: float
    backend: str
    action: str
    default_selectable: bool
    evidence: str


@dataclass(frozen=True)
class ProtectedPath:
    pattern: re.Pattern[str]
    reason: str


def load_rules(rules_dir: Path | None = None) -> tuple[list[Rule], list[ProtectedPath]]:
    base = rules_dir or PACKAGE_ROOT / "rules"
    rules: list[Rule] = []
    for filename in ("known-caches.toml", "development-artifacts.toml"):
        path = base / filename
        if path.exists():
            rules.extend(parse_rule_file(path))
    protected = parse_protected_paths(base / "protected-paths.toml")
    return rules, protected


def parse_rule_file(path: Path) -> list[Rule]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return [rule_from_raw(raw) for raw in data.get("rule", [])]


def parse_protected_paths(path: Path) -> list[ProtectedPath]:
    data = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return [
        ProtectedPath(pattern=re.compile(raw["pattern"]), reason=str(raw.get("reason", "")))
        for raw in data.get("path", [])
    ]


def rule_from_raw(raw: dict[str, Any]) -> Rule:
    return Rule(
        id=str(raw["id"]),
        category=str(raw["category"]),
        path_regex=re.compile(str(raw["path_regex"])),
        risk=str(raw.get("risk", "medium")),
        confidence=float(raw.get("confidence", 0.7)),
        backend=str(raw.get("backend", "file")),
        action=str(raw.get("action", "review")),
        default_selectable=bool(raw.get("default_selectable", False)),
        evidence=str(raw.get("evidence", "")),
    )


def protected_reason(path: str, protected_paths: list[ProtectedPath]) -> str | None:
    for protected in protected_paths:
        if protected.pattern.search(path):
            return protected.reason
    return None

