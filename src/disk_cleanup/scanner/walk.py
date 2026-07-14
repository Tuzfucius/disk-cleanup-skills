from __future__ import annotations

import os
import stat
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from disk_cleanup.models import WizTreeNode
from disk_cleanup.scanner.csv_parser import extension_for

_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


@dataclass
class ScandirStats:
    entries: int = 0
    files: int = 0
    directories: int = 0
    skipped_reparse_points: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    truncated: bool = False
    truncation_reason: str | None = None


class ScandirWalk:
    """Single-pass, non-following filesystem walk with observable scan status."""

    def __init__(
        self,
        root: Path,
        *,
        max_entries: int | None = None,
        timeout_seconds: float | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> None:
        root = root.absolute()
        root_stat = root.lstat()
        if root.is_symlink() or _is_reparse(root_stat):
            raise ValueError(f"扫描目标不能是重解析点: {root}")
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError(f"扫描目标不是目录: {root}")
        if max_entries is not None and max_entries < 1:
            raise ValueError("max_entries 必须大于零")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于零")
        self.root = root
        self.max_entries = max_entries
        self.deadline = time.monotonic() + timeout_seconds if timeout_seconds else None
        self.cancel_check = cancel_check
        self.stats = ScandirStats()
        self._started = False

    def __iter__(self) -> Iterator[WizTreeNode]:
        if self._started:
            raise RuntimeError("ScandirWalk 只能迭代一次")
        self._started = True
        yield from self._walk_directory(self.root, parent_path=None, depth=0)

    def _walk_directory(
        self,
        path: Path,
        *,
        parent_path: str | None,
        depth: int,
    ) -> Iterator[WizTreeNode]:
        logical_bytes = allocated_bytes = file_count = folder_count = 0
        try:
            entries = os.scandir(path)
        except OSError as exc:
            self.stats.errors.append((str(path), str(exc)))
            entries = None

        if entries is not None:
            with entries:
                for entry in entries:
                    if self._should_stop():
                        break
                    try:
                        entry_stat = entry.stat(follow_symlinks=False)
                        if entry.is_symlink() or _is_reparse(entry_stat):
                            self.stats.skipped_reparse_points += 1
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            child_summary = yield from self._walk_directory(
                                Path(entry.path),
                                parent_path=_directory_path(path),
                                depth=depth + 1,
                            )
                            child_logical, child_allocated, child_files, child_folders = child_summary
                            logical_bytes += child_logical
                            allocated_bytes += child_allocated
                            file_count += child_files
                            folder_count += child_folders + 1
                        elif entry.is_file(follow_symlinks=False):
                            allocated = _allocated_size(entry_stat)
                            node = WizTreeNode(
                                full_path=str(Path(entry.path)),
                                name=entry.name,
                                parent_path=_directory_path(path),
                                node_type="file",
                                logical_bytes=entry_stat.st_size,
                                allocated_bytes=allocated,
                                subtree_allocated_bytes=allocated,
                                modified_at=str(entry_stat.st_mtime_ns),
                                attributes=str(getattr(entry_stat, "st_file_attributes", "")),
                                file_count=0,
                                folder_count=0,
                                depth=depth + 1,
                                extension=extension_for(entry.name, "file"),
                            )
                            self.stats.entries += 1
                            self.stats.files += 1
                            logical_bytes += entry_stat.st_size
                            allocated_bytes += allocated
                            file_count += 1
                            yield node
                    except OSError as exc:
                        self.stats.errors.append((entry.path, str(exc)))

        directory_node = WizTreeNode(
            full_path=_directory_path(path),
            name=_directory_name(path),
            parent_path=parent_path,
            node_type="directory",
            logical_bytes=logical_bytes,
            allocated_bytes=0,
            subtree_allocated_bytes=allocated_bytes,
            modified_at="",
            attributes="",
            file_count=file_count,
            folder_count=folder_count,
            depth=depth,
            extension="",
        )
        if self.max_entries is None or self.stats.entries < self.max_entries:
            self.stats.entries += 1
            self.stats.directories += 1
            yield directory_node
        else:
            self.stats.truncated = True
            self.stats.truncation_reason = "entry_budget"
        return logical_bytes, allocated_bytes, file_count, folder_count

    def _should_stop(self) -> bool:
        reason: str | None = None
        if self.max_entries is not None and self.stats.entries >= self.max_entries:
            reason = "entry_budget"
        elif self.deadline is not None and time.monotonic() >= self.deadline:
            reason = "timeout"
        elif self.cancel_check is not None and self.cancel_check():
            reason = "cancelled"
        if reason is not None:
            self.stats.truncated = True
            self.stats.truncation_reason = reason
            return True
        return False


def walk_windows_tree(
    root: Path,
    *,
    max_entries: int | None = None,
    timeout_seconds: float | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> ScandirWalk:
    """Build the Windows fallback scanner without starting filesystem traversal."""
    return ScandirWalk(
        root,
        max_entries=max_entries,
        timeout_seconds=timeout_seconds,
        cancel_check=cancel_check,
    )


def _is_reparse(value: os.stat_result) -> bool:
    return bool(getattr(value, "st_file_attributes", 0) & _REPARSE_POINT)


def _allocated_size(value: os.stat_result) -> int:
    blocks = getattr(value, "st_blocks", None)
    return blocks * 512 if blocks is not None else value.st_size


def _directory_path(path: Path) -> str:
    value = str(path)
    return value if value.endswith(("\\", "/")) else value + os.sep


def _directory_name(path: Path) -> str:
    return path.name or path.anchor
