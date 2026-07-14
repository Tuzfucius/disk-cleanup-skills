from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def default_audit_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "disk-cleanup-skills" / "audit.jsonl"


def append_audit(event: str, *, path: Path | None = None, **fields: Any) -> None:
    destination = path or default_audit_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    line = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    # O_APPEND makes separate process writes indivisible for these small records.
    descriptor = os.open(destination, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, line.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def prune_audit(*, path: Path | None = None, retain_days: int = 30) -> int:
    destination = path or default_audit_path()
    if not destination.exists():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=retain_days)
    kept: list[str] = []
    removed = 0
    for line in destination.read_text(encoding="utf-8").splitlines():
        try:
            stamp = datetime.fromisoformat(json.loads(line)["timestamp"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            removed += 1
            continue
        if stamp >= cutoff:
            kept.append(line)
        else:
            removed += 1
    temporary = destination.with_suffix(".tmp")
    temporary.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    temporary.replace(destination)
    return removed
