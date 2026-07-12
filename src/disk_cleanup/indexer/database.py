from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from disk_cleanup.models import ImportSummary, ScanMetadata, WizTreeNode
from disk_cleanup.scanner.csv_parser import read_wiztree_csv


def import_wiztree_csv(csv_path: Path, db_path: Path) -> ImportSummary:
    metadata, nodes = read_wiztree_csv(csv_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        create_schema(conn)
        scan_id = insert_scan(conn, metadata)
        insert_nodes(conn, scan_id, nodes)
        conn.commit()

    return ImportSummary(
        scan_id=scan_id,
        rows=len(nodes),
        files=sum(1 for node in nodes if node.node_type == "file"),
        folders=sum(1 for node in nodes if node.node_type == "directory"),
        max_depth=max((node.depth for node in nodes), default=0),
        total_file_allocated_bytes=sum(node.allocated_bytes for node in nodes if node.node_type == "file"),
    )


def create_schema(conn: sqlite3.Connection) -> None:
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

        CREATE UNIQUE INDEX IF NOT EXISTS idx_nodes_scan_path ON nodes(scan_id, full_path);
        CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(scan_id, parent_id);
        CREATE INDEX IF NOT EXISTS idx_nodes_type_size ON nodes(scan_id, node_type, subtree_allocated_bytes DESC);
        CREATE INDEX IF NOT EXISTS idx_nodes_ext ON nodes(scan_id, extension);

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


def insert_nodes(conn: sqlite3.Connection, scan_id: int, nodes: list[WizTreeNode]) -> None:
    path_to_id: dict[str, int] = {}
    for node in sorted(nodes, key=lambda item: (item.depth, item.node_type != "directory", item.full_path.lower())):
        parent_id = path_to_id.get(node.parent_path or "")
        cursor = conn.execute(
            """
            INSERT INTO nodes (
                scan_id, parent_id, name, full_path, node_type, logical_bytes,
                allocated_bytes, subtree_allocated_bytes, modified_at, attributes,
                file_count, folder_count, depth, extension
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scan_id,
                parent_id,
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
            ),
        )
        path_to_id[node.full_path] = int(cursor.lastrowid)
