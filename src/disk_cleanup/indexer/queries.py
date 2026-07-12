from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def top_children(db_path: Path, scan_id: int, path: str, limit: int = 50) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        parent = conn.execute(
            "SELECT id FROM nodes WHERE scan_id = ? AND full_path = ?",
            (scan_id, path),
        ).fetchone()
        if parent is None:
            return []
        rows = conn.execute(
            """
            SELECT id, name, full_path, node_type, allocated_bytes, subtree_allocated_bytes,
                   modified_at, file_count, folder_count, depth, extension
            FROM nodes
            WHERE scan_id = ? AND parent_id = ?
            ORDER BY subtree_allocated_bytes DESC, name ASC
            LIMIT ?
            """,
            (scan_id, parent["id"], limit),
        ).fetchall()
    return [dict(row) for row in rows]


def children_by_node_id(db_path: Path, scan_id: int, node_id: int | None, limit: int = 100) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if node_id is None:
            parent_clause = "parent_id IS NULL AND depth = 0"
            params: tuple[Any, ...] = (scan_id, limit)
        else:
            parent_clause = "parent_id = ?"
            params = (scan_id, node_id, limit)
        rows = conn.execute(
            f"""
            SELECT id, name, full_path, node_type, allocated_bytes, subtree_allocated_bytes,
                   modified_at, file_count, folder_count, depth, extension
            FROM nodes
            WHERE scan_id = ? AND {parent_clause}
            ORDER BY subtree_allocated_bytes DESC, name ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def largest_files(db_path: Path, scan_id: int, limit: int = 100) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, name, full_path, allocated_bytes, logical_bytes, modified_at, extension
            FROM nodes
            WHERE scan_id = ? AND node_type = 'file'
            ORDER BY allocated_bytes DESC, name ASC
            LIMIT ?
            """,
            (scan_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def largest_directories(db_path: Path, scan_id: int, limit: int = 100) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, name, full_path, subtree_allocated_bytes, modified_at, file_count, folder_count
            FROM nodes
            WHERE scan_id = ? AND node_type = 'directory'
            ORDER BY subtree_allocated_bytes DESC, name ASC
            LIMIT ?
            """,
            (scan_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def extension_summary(db_path: Path, scan_id: int, limit: int = 50) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                CASE WHEN extension = '' THEN '[无扩展名]' ELSE extension END AS extension,
                COUNT(*) AS file_count,
                SUM(allocated_bytes) AS allocated_bytes
            FROM nodes
            WHERE scan_id = ? AND node_type = 'file'
            GROUP BY extension
            ORDER BY allocated_bytes DESC
            LIMIT ?
            """,
            (scan_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def scan_summary(db_path: Path, scan_id: int) -> dict[str, Any]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        scan = conn.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
        counts = conn.execute(
            """
            SELECT
                SUM(CASE WHEN node_type = 'file' THEN 1 ELSE 0 END) AS files,
                SUM(CASE WHEN node_type = 'directory' THEN 1 ELSE 0 END) AS folders,
                MAX(depth) AS max_depth,
                SUM(CASE WHEN node_type = 'file' THEN allocated_bytes ELSE 0 END) AS file_allocated_bytes
            FROM nodes
            WHERE scan_id = ?
            """,
            (scan_id,),
        ).fetchone()
        candidates = conn.execute(
            """
            SELECT COUNT(*) AS candidate_count, COALESCE(SUM(reclaimable_bytes), 0) AS reclaimable_bytes
            FROM candidates
            WHERE scan_id = ?
            """,
            (scan_id,),
        ).fetchone()
    if scan is None:
        return {}
    result = dict(scan)
    result.update(dict(counts))
    result.update(dict(candidates))
    return result


def candidate_rows(db_path: Path, scan_id: int, limit: int = 200) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT c.*, n.full_path, n.node_type, n.modified_at
            FROM candidates c
            JOIN nodes n ON n.id = c.node_id
            WHERE c.scan_id = ?
            ORDER BY
                CASE c.risk WHEN 'low' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END ASC,
                c.reclaimable_bytes DESC
            LIMIT ?
            """,
            (scan_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]
