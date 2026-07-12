from __future__ import annotations

import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def tracked_files() -> list[Path]:
    repo_root = Path(
        subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
        ).strip()
    )
    names = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=repo_root,
        text=True,
        encoding="utf-8",
    ).splitlines()
    return [repo_root / name for name in names]


def test_tracked_files_do_not_include_private_machine_markers() -> None:
    forbidden = [
        "95" + "210",
        "WizTree_" + "20260708100800",
        "E:" + "\\",
        "D:" + "\\wiztree",
        "D:" + "\\BleachBit",
        "0" + "所谓程序",
        "Pytorch" + "Learning",
        "BEGIN " + "OPENSSH PRIVATE KEY",
        "BEGIN " + "RSA PRIVATE KEY",
    ]

    leaks: list[str] = []
    for path in tracked_files():
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for marker in forbidden:
            if marker in text:
                leaks.append(f"{path}: {marker}")

    assert leaks == []


def test_tracked_text_has_no_private_user_paths_or_email() -> None:
    import re

    patterns = (
        re.compile(r'(?i)[A-Z]:\\Users\\(?!ExampleUser(?:\\|["\r\n]))(?!["\r\n])[^\\\r\n]+'),
        re.compile(r'(?i)/Users/(?!ExampleUser(?:/|["\r\n]))(?!["\r\n])[^/\r\n]+'),
        re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    )
    violations: list[str] = []
    for path in tracked_files():
        if path == Path(__file__).resolve():
            continue
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in patterns:
            if pattern.search(text):
                violations.append(f"{path}: {pattern.pattern}")
    assert violations == []


def test_local_artifacts_are_not_tracked() -> None:
    blocked_parts = [
        "/.disk-cleanup-workspace/",
        "/.pytest_tmp/",
        "/.pytest_cache/",
        "/.playwright-cli/",
        "/__pycache__/",
    ]
    blocked_names = {"/config.local.toml"}

    violations = [
        path
        for path in (str(item).replace("\\", "/") for item in tracked_files())
        if any(part in f"/{path}" for part in blocked_parts)
        or any(f"/{path}".endswith(name) for name in blocked_names)
    ]

    assert violations == []
