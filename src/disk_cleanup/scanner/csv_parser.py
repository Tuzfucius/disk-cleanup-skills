from __future__ import annotations

import codecs
import csv
import locale
from contextlib import contextmanager
from itertools import chain
from pathlib import Path
from typing import Iterable, Iterator

from disk_cleanup.models import ScanMetadata, WizTreeNode

CSV_FIELDS = (
    "文件名称",
    "大小",
    "分配",
    "修改时间",
    "属性",
    "文件",
    "文件夹",
    "DRIVECAPACITY",
    "FREESPACE",
    "USEDSPACE",
    "RESERVEDSPACE",
)

HEADER_ALIASES = (
    CSV_FIELDS,
    (
        "File Name", "Size", "Allocated", "Modified", "Attributes", "Files", "Folders",
        "DRIVECAPACITY", "FREESPACE", "USEDSPACE", "RESERVEDSPACE",
    ),
)

_ENCODING_SAMPLE_BYTES = 64 * 1024


@contextmanager
def stream_wiztree_csv(csv_path: Path) -> Iterator[tuple[ScanMetadata, Iterator[WizTreeNode]]]:
    """Open a WizTree export and yield metadata plus a lazy node iterator.

    The file remains open for the duration of the context. Only a bounded sample is
    read for encoding detection; rows are decoded and parsed as the caller consumes
    them.
    """
    encoding = detect_wiztree_encoding(csv_path)
    with csv_path.open("r", encoding=encoding, newline="") as handle:
        reader = csv.reader(handle)
        generated_by = next(reader, [""])[0]
        header = next(reader, [])
        validate_header(header)
        first_row = next((row for row in reader if row), None)
        metadata = build_metadata(csv_path, generated_by, first_row)
        rows = chain((first_row,), reader) if first_row is not None else iter(())
        yield metadata, (parse_row(row) for row in rows if row)


def read_wiztree_csv(csv_path: Path) -> tuple[ScanMetadata, list[WizTreeNode]]:
    """Compatibility helper for callers that intentionally need all nodes."""
    with stream_wiztree_csv(csv_path) as (metadata, nodes):
        return metadata, list(nodes)


def validate_header(header: Iterable[str]) -> None:
    actual = tuple(header)
    if not any(all(field in actual for field in alias) for alias in HEADER_ALIASES):
        raise ValueError(f"WizTree CSV 表头不受支持: {', '.join(actual)}")


def detect_wiztree_encoding(csv_path: Path) -> str:
    with csv_path.open("rb") as handle:
        sample = handle.read(_ENCODING_SAMPLE_BYTES)

    if sample.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    if sample.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if sample[:4].count(b"\x00") >= 2:
        return "utf-16"

    try:
        sample.decode("utf-8")
        return "utf-8-sig"
    except UnicodeDecodeError:
        preferred = locale.getpreferredencoding(False)
        # mbcs is available on Windows; the locale fallback keeps parser tests
        # usable on other platforms without changing Windows behaviour.
        try:
            codecs.lookup("mbcs")
        except LookupError:
            return preferred
        return "mbcs"


def decode_wiztree_csv(csv_path: Path) -> str:
    """Legacy full-text decoder retained for API compatibility."""
    return csv_path.read_text(encoding=detect_wiztree_encoding(csv_path))


def build_metadata(
    csv_path: Path,
    generated_by: str,
    root: list[str] | list[list[str]] | None,
) -> ScanMetadata:
    root = root or []
    if root and isinstance(root[0], list):
        root = root[0]
    return ScanMetadata(
        source=str(csv_path),
        generated_by=generated_by,
        root_path=root[0] if root else None,
        drive_capacity=parse_optional_int(root, 7),
        free_space=parse_optional_int(root, 8),
        used_space=parse_optional_int(root, 9),
        reserved_space=parse_optional_int(root, 10),
    )


def parse_row(row: list[str]) -> WizTreeNode:
    padded = row + [""] * (len(CSV_FIELDS) - len(row))
    full_path = padded[0]
    logical = parse_int(padded[1])
    allocated = parse_int(padded[2])
    node_type = "directory" if is_directory_path(full_path) else "file"
    name = basename(full_path)
    parent = parent_path(full_path, node_type)
    depth = path_depth(full_path)

    if node_type == "directory":
        allocated_bytes = 0
        subtree_allocated_bytes = allocated
    else:
        allocated_bytes = allocated
        subtree_allocated_bytes = allocated

    return WizTreeNode(
        full_path=full_path,
        name=name,
        parent_path=parent,
        node_type=node_type,
        logical_bytes=logical,
        allocated_bytes=allocated_bytes,
        subtree_allocated_bytes=subtree_allocated_bytes,
        modified_at=padded[3],
        attributes=padded[4],
        file_count=parse_int(padded[5]),
        folder_count=parse_int(padded[6]),
        depth=depth,
        extension=extension_for(full_path, node_type),
    )


def parse_int(value: str) -> int:
    return int(value) if value else 0


def parse_optional_int(row: list[str], index: int) -> int | None:
    if len(row) <= index or row[index] == "":
        return None
    return parse_int(row[index])


def is_directory_path(path: str) -> bool:
    return path.endswith("\\")


def path_depth(path: str) -> int:
    return max(0, len([part for part in path.split("\\") if part]) - 1)


def basename(path: str) -> str:
    stripped = path.rstrip("\\")
    if stripped.endswith(":"):
        return stripped + "\\"
    return stripped.rsplit("\\", 1)[-1]


def parent_path(path: str, node_type: str) -> str | None:
    del node_type  # Retained in the public signature for compatibility.
    stripped = path.rstrip("\\")
    if stripped.endswith(":"):
        return None
    if "\\" not in stripped:
        return None
    parent = stripped.rsplit("\\", 1)[0]
    if parent.endswith(":"):
        return parent + "\\"
    return parent + "\\"


def extension_for(path: str, node_type: str) -> str:
    if node_type == "directory":
        return ""
    name = basename(path)
    if "." not in name or name.endswith("."):
        return ""
    return "." + name.rsplit(".", 1)[-1].lower()
