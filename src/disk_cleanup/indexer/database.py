from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from disk_cleanup.models import ImportSummary, ScanMetadata, WizTreeNode
from disk_cleanup.scanner.csv_parser import stream_wiztree_csv

DEFAULT_BATCH_SIZE = 2_000


def import_wiztree_csv(
    csv_path: Path,
    db_path: Path,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> ImportSummary:
    if batch_size < 1:
        raise ValueError("batch_size 必须大于零")
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with stream_wiztree_csv(csv_path) as (metadata, nodes):
        with sqlite3.connect(db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            create_schema(conn, with_node_indexes=False)
            scan_id = insert_scan(conn, metadata)
            summary_values = insert_nodes(conn, scan_id, nodes, batch_size=batch_size)
            create_path_index(conn)
            backfill_parent_ids(conn, scan_id)
            create_node_indexes(conn)
            conn.commit()

    rows, files, folders, max_depth, total_allocated = summary_values
    return ImportSummary(
        scan_id=scan_id,
        rows=rows,
        files=files,
        folders=folders,
        max_depth=max_depth,
        total_file_allocated_bytes=total_allocated,
    )


def create_schema(conn: sqlite3.Connection, *, with_node_indexes: bool = True) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            generated_by TEXT NOT NULL,
            root_path TEXT,
            drive_capacity INTEGER,
            free_space INTEGER,
            used_space INTEGER,
            reserved_space INTEGER,
            status TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id INTEGER NOT NULL,
            parent_id INTEGER,
            parent_path TEXT,
            name TEXT NOT NULL,
            full_path TEXT NOT NULL,
            node_type TEXT NOT NULL CHECK (node_type IN ('file', 'directory')),
            logical_bytes INTEGER NOT NULL,
            allocated_bytes INTEGER NOT NULL,
            subtree_allocated_bytes INTEGER NOT NULL,
            modified_at TEXT,
            attributes TEXT,
            file_count INTEGER NOT NULL DEFAULT 0,
            folder_count INTEGER NOT NULL DEFAULT 0,
            depth INTEGER NOT NULL,
            extension TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(scan_id) REFERENCES scans(id) ON DELETE CASCADE,
            FOREIGN KEY(parent_id) REFERENCES nodes(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id INTEGER NOT NULL,
            candidate_id TEXT NOT NULL,
            node_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            reclaimable_bytes INTEGER NOT NULL,
            risk TEXT NOT NULL,
            confidence REAL NOT NULL,
            recommended_action TEXT NOT NULL,
            backend TEXT NOT NULL,
            default_selectable INTEGER NOT NULL DEFAULT 0,
            evidence TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(scan_id) REFERENCES scans(id) ON DELETE CASCADE,
            FOREIGN KEY(node_id) REFERENCES nodes(id) ON DELETE CASCADE
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_candidates_scan_candidate
            ON candidates(scan_id, candidate_id);
        CREATE INDEX IF NOT EXISTS idx_candidates_scan_category
            ON candidates(scan_id, category, reclaimable_bytes DESC);
        """
    )
    _ensure_parent_path_column(conn)
    if with_node_indexes:
        create_node_indexes(conn)


def _ensure_parent_path_column(conn: sqlite3.Connection) -> None:
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(nodes)")}
    if "parent_path" not in columns:
        conn.execute("ALTER TABLE nodes ADD COLUMN parent_path TEXT")


def create_node_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_nodes_scan_path ON nodes(scan_id, full_path);
        CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(scan_id, parent_id);
        CREATE INDEX IF NOT EXISTS idx_nodes_type_size
            ON nodes(scan_id, node_type, subtree_allocated_bytes DESC);
        CREATE INDEX IF NOT EXISTS idx_nodes_ext ON nodes(scan_id, extension);
        """
    )


def create_path_index(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_nodes_scan_path ON nodes(scan_id, full_path)"
    )


def insert_scan(conn: sqlite3.Connection, metadata: ScanMetadata) -> int:
    cursor = conn.execute(
        """
        INSERT INTO scans (
            source, generated_by, root_path, drive_capacity, free_space,
            used_space, reserved_space, status, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            metadata.source,
            metadata.generated_by,
            metadata.root_path,
            metadata.drive_capacity,
            metadata.free_space,
            metadata.used_space,
            metadata.reserved_space,
            "imported",
            json.dumps(metadata.__dict__, ensure_ascii=False),
        ),
    )
    return int(cursor.lastrowid)


def insert_nodes(
    conn: sqlite3.Connection,
    scan_id: int,
    nodes: Iterable[WizTreeNode],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> tuple[int, int, int, int, int]:
    statement = """
        INSERT INTO nodes (
            scan_id, parent_id, parent_path, name, full_path, node_type,
            logical_bytes, allocated_bytes, subtree_allocated_bytes, modified_at,
            attributes, file_count, folder_count, depth, extension
        )
        VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    rows = files = folders = total_file_allocated = 0
    max_depth = 0
    batch: list[tuple[object, ...]] = []

    for node in nodes:
        rows += 1
        max_depth = max(max_depth, node.depth)
        if node.node_type == "file":
            files += 1
            total_file_allocated += node.allocated_bytes
        else:
            folders += 1
        batch.append(_node_values(scan_id, node))
        if len(batch) >= batch_size:
            conn.executemany(statement, batch)
            batch.clear()
    if batch:
        conn.executemany(statement, batch)

    return rows, files, folders, max_depth, total_file_allocated


def _node_values(scan_id: int, node: WizTreeNode) -> tuple[object, ...]:
    return (
        scan_id,
        node.parent_path,
        node.name,
        node.full_path,
        node.node_type,
        node.logical_bytes,
        node.allocated_bytes,
        node.subtree_allocated_bytes,
        node.modified_at,
        node.attributes,
        node.file_count,
        node.folder_count,
        node.depth,
        node.extension,
    )


def backfill_parent_ids(conn: sqlite3.Connection, scan_id: int) -> None:
    conn.execute(
        """
        UPDATE nodes AS child
        SET parent_id = (
            SELECT parent.id
            FROM nodes AS parent
            WHERE parent.scan_id = child.scan_id
              AND parent.full_path = child.parent_path
        )
        WHERE child.scan_id = ? AND child.parent_path IS NOT NULL
        """,
        (scan_id,),
    )
