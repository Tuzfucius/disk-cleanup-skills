from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

DEVICE_PREFIXES = ("\\\\?\\", "\\\\.\\", "\\\\")
RESERVED_ROOTS = {"windows", "program files", "program files (x86)", "programdata", "recovery", "system volume information"}
PROTECTED_SEGMENTS = {"onedrive", "dropbox", "google drive", ".ssh"}
PROTECTED_USER_FOLDERS = {"documents", "desktop", "pictures"}


@dataclass(frozen=True)
class HandleIdentity:
    volume_serial: int
    file_id: int
    modified_ns: int
    size_bytes: int
    final_path: Path


def canonical_local_path(value: str) -> Path:
    if not value or any(ord(ch) < 32 for ch in value) or '"' in value:
        raise ValueError("路径包含非法字符")
    if value.startswith(DEVICE_PREFIXES) or re.search(r":.+:", value):
        raise ValueError("不允许设备、UNC 或 ADS 路径")
    if re.fullmatch(r"[A-Za-z]:", value):
        value += "\\"
    if not Path(value).is_absolute():
        raise ValueError("路径必须是绝对路径")
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
    folded_parts = {part.casefold() for part in relative_parts}
    if folded_parts & PROTECTED_SEGMENTS:
        raise ValueError("目标属于云同步或凭据保护目录")
    if len(relative_parts) >= 3 and relative_parts[0].casefold() == "users" and relative_parts[2].casefold() in PROTECTED_USER_FOLDERS:
        raise ValueError("目标属于用户重要资料目录")
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


def assert_execution_target(
    path_value: str,
    allowed_root: str,
    protected_roots: tuple[Path, ...] = (),
    *,
    allow_directory: bool = False,
) -> tuple[Path, HandleIdentity]:
    path = assert_deletable(path_value, allowed_root, protected_roots)
    if not path.exists():
        raise ValueError("目标不存在")
    identity = handle_identity(path)
    expected = canonical_local_path(str(path))
    final = canonical_local_path(str(identity.final_path))
    if os.path.normcase(str(expected)) != os.path.normcase(str(final)):
        raise ValueError("句柄最终路径与计划路径不一致")
    assert_local_fixed_ntfs(path)
    if path.is_dir():
        if not allow_directory:
            raise ValueError("计划未授权清理目录")
        assert_no_descendant_reparse_points(path)
    elif not path.is_file():
        raise ValueError("只允许普通文件或受控目录")
    return path, identity


def assert_no_descendant_reparse_points(root: Path) -> None:
    pending = [root]
    while pending:
        current = pending.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                attrs = getattr(info, "st_file_attributes", 0)
                if entry.is_symlink() or attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise ValueError(f"目录包含 reparse point，拒绝清理: {entry.path}")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))


def directory_manifest(root: Path) -> tuple[str, int]:
    """Hash the exact descendant set approved for an executable directory plan."""
    digest = hashlib.sha256()
    count = 0
    pending = [root]
    while pending:
        current = pending.pop()
        with os.scandir(current) as entries:
            ordered = sorted(entries, key=lambda item: item.name.casefold())
        for entry in ordered:
            info = entry.stat(follow_symlinks=False)
            attrs = getattr(info, "st_file_attributes", 0)
            if entry.is_symlink() or attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError(f"目录包含 reparse point，拒绝清理: {entry.path}")
            entry_path = Path(entry.path)
            identity = handle_identity(entry_path)
            relative = str(entry_path.relative_to(root)).replace("/", "\\").casefold()
            kind = "directory" if entry.is_dir(follow_symlinks=False) else "file"
            record = (
                f"{relative}\0{kind}\0{identity.volume_serial}\0{identity.file_id}\0"
                f"{identity.modified_ns}\0{identity.size_bytes}\n"
            )
            digest.update(record.encode("utf-8"))
            count += 1
            if kind == "directory":
                pending.append(entry_path)
    return digest.hexdigest(), count


def assert_local_fixed_ntfs(path: Path) -> None:
    if os.name != "nt":
        raise ValueError("清理执行仅支持 Windows 本地固定 NTFS 卷")
    import ctypes

    root = path.anchor
    if ctypes.windll.kernel32.GetDriveTypeW(root) != 3:  # DRIVE_FIXED
        raise ValueError("清理只允许本地固定磁盘")
    filesystem = ctypes.create_unicode_buffer(32)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(
        root, None, 0, None, None, None, filesystem, len(filesystem)
    )
    if not ok or filesystem.value.casefold() != "ntfs":
        raise ValueError("清理只允许 NTFS 文件系统")


def file_identity(path: Path) -> tuple[int | None, int | None, int, int]:
    if os.name == "nt":
        identity = handle_identity(path)
        return identity.volume_serial, identity.file_id, identity.modified_ns, identity.size_bytes
    info = path.stat(follow_symlinks=False)
    return getattr(info, "st_dev", None), getattr(info, "st_ino", None), info.st_mtime_ns, info.st_size


def handle_identity(path: Path) -> HandleIdentity:
    if os.name != "nt":
        info = path.stat(follow_symlinks=False)
        return HandleIdentity(
            int(getattr(info, "st_dev", 0)), int(getattr(info, "st_ino", 0)),
            info.st_mtime_ns, info.st_size, path.resolve(strict=True),
        )
    import ctypes
    from ctypes import wintypes

    class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD), ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME), ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD), ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD), ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD), ("nFileIndexLow", wintypes.DWORD),
        ]

    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.restype = wintypes.HANDLE
    handle = create_file(str(path), 0x80, 0x1 | 0x2 | 0x4, None, 3, 0x02000000 | 0x00200000, None)
    invalid = ctypes.c_void_p(-1).value
    if handle == invalid:
        raise OSError(ctypes.get_last_error(), "无法打开目标句柄")
    try:
        details = BY_HANDLE_FILE_INFORMATION()
        if not ctypes.windll.kernel32.GetFileInformationByHandle(handle, ctypes.byref(details)):
            raise OSError(ctypes.get_last_error(), "无法读取目标文件身份")
        required = ctypes.windll.kernel32.GetFinalPathNameByHandleW(handle, None, 0, 0)
        if not required:
            raise OSError(ctypes.get_last_error(), "无法读取目标最终路径")
        buffer = ctypes.create_unicode_buffer(required + 1)
        if not ctypes.windll.kernel32.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0):
            raise OSError(ctypes.get_last_error(), "无法读取目标最终路径")
        final_path = buffer.value
        if final_path.startswith("\\\\?\\"):
            final_path = final_path[4:]
        modified_ticks = (details.ftLastWriteTime.dwHighDateTime << 32) | details.ftLastWriteTime.dwLowDateTime
        return HandleIdentity(
            details.dwVolumeSerialNumber,
            (details.nFileIndexHigh << 32) | details.nFileIndexLow,
            max(0, modified_ticks - 116444736000000000) * 100,
            (details.nFileSizeHigh << 32) | details.nFileSizeLow,
            Path(final_path),
        )
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)
