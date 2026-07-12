from __future__ import annotations

import os
import re
import stat
from pathlib import Path

DEVICE_PREFIXES = ("\\\\?\\", "\\\\.\\", "\\\\")
RESERVED_ROOTS = {"windows", "program files", "program files (x86)", "programdata", "recovery", "system volume information"}


def canonical_local_path(value: str) -> Path:
    if not value or any(ord(ch) < 32 for ch in value) or '"' in value:
        raise ValueError("路径包含非法字符")
    if value.startswith(DEVICE_PREFIXES) or re.search(r":.+:", value):
        raise ValueError("不允许设备、UNC 或 ADS 路径")
    if re.fullmatch(r"[A-Za-z]:", value):
        value += "\\"
    path = Path(os.path.abspath(value))
    if not path.drive:
        raise ValueError("路径必须位于本地卷")
    return path


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return path != root
    except ValueError:
        return False


def assert_deletable(path_value: str, allowed_root: str, protected_roots: tuple[Path, ...] = ()) -> Path:
    path = canonical_local_path(path_value)
    root = canonical_local_path(allowed_root)
    if not is_within(path, root):
        raise ValueError("目标不在扫描根目录内，或目标就是扫描根目录")
    if str(path).replace("/", "\\").casefold().endswith("\\appdata\\local\\temp"):
        raise ValueError("只允许选择临时目录中的具体子项，不能删除 Temp 根目录")
    relative_parts = path.relative_to(Path(path.anchor)).parts
    if relative_parts and relative_parts[0].casefold() in RESERVED_ROOTS:
        raise ValueError("目标属于受保护的系统或应用根目录")
    for protected in protected_roots:
        protected = protected.resolve()
        if path == protected or is_within(path, protected):
            raise ValueError("目标属于当前任务或项目保护目录")
    current = path
    while True:
        if current.exists():
            attrs = current.lstat().st_file_attributes if hasattr(current.lstat(), "st_file_attributes") else 0
            if current.is_symlink() or attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError("目标或祖先是 reparse point")
        if current == root or current.parent == current:
            break
        current = current.parent
    return path


def file_identity(path: Path) -> tuple[int | None, int | None, int, int]:
    info = path.stat(follow_symlinks=False)
    return getattr(info, "st_dev", None), getattr(info, "st_ino", None), info.st_mtime_ns, info.st_size
