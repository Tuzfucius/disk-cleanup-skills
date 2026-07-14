from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from disk_cleanup.analyzer.rule_engine import Rule, load_rules, protected_reason
from disk_cleanup.models import AnalysisSummary, Candidate

LARGE_FILE_THRESHOLD = 500 * 1024 * 1024


def analyze_scan(
    db_path: Path,
    scan_id: int,
    *,
    context_path: Path | None = None,
    max_candidates: int = 300,
) -> AnalysisSummary:
    rules, protected_paths = load_rules()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        ensure_candidate_table(conn)
        conn.execute("DELETE FROM candidates WHERE scan_id = ?", (scan_id,))
        candidates = build_candidates(conn, scan_id, rules, protected_paths, max_candidates)
        insert_candidates(conn, scan_id, candidates)
        conn.commit()

    if context_path:
        from disk_cleanup.analyzer.agent_context import write_agent_context

        write_agent_context(db_path, scan_id, context_path)

    return AnalysisSummary(
        scan_id=scan_id,
        candidate_count=len(candidates),
        reclaimable_bytes=sum(candidate.reclaimable_bytes for candidate in candidates),
        context_path=context_path,
    )


def ensure_candidate_table(conn: sqlite3.Connection) -> None:
    from disk_cleanup.indexer.database import create_schema

    create_schema(conn)


def build_candidates(
    conn: sqlite3.Connection,
    scan_id: int,
    rules: list[Rule],
    protected_paths: list,
    max_candidates: int,
) -> list[Candidate]:
    nodes = conn.execute(
        """
        SELECT id, full_path, name, node_type, allocated_bytes, subtree_allocated_bytes, modified_at, extension
        FROM nodes
        WHERE scan_id = ?
        ORDER BY subtree_allocated_bytes DESC
        """,
        (scan_id,),
    )
    candidates: list[Candidate] = []
    seen_nodes: set[int] = set()

    while len(candidates) < max_candidates:
        batch = nodes.fetchmany(2048)
        if not batch:
            break
        for node in batch:
            if len(candidates) >= max_candidates:
                break
            path = str(node["full_path"])
            if protected_reason(path, protected_paths):
                continue
            for rule in rules:
                if rule.path_regex.search(path):
                    candidates.append(candidate_from_rule(scan_id, node, rule))
                    seen_nodes.add(int(node["id"]))
                    break

    if len(candidates) < max_candidates:
        large_files = conn.execute(
            """
            SELECT id, full_path, name, node_type, allocated_bytes, subtree_allocated_bytes, modified_at, extension
            FROM nodes
            WHERE scan_id = ? AND node_type = 'file' AND allocated_bytes >= ?
            ORDER BY allocated_bytes DESC
            """,
            (scan_id, LARGE_FILE_THRESHOLD),
        )
        while len(candidates) < max_candidates:
            batch = large_files.fetchmany(512)
            if not batch:
                break
            for node in batch:
                if len(candidates) >= max_candidates:
                    break
                if int(node["id"]) in seen_nodes:
                    continue
                if protected_reason(str(node["full_path"]), protected_paths):
                    continue
                candidates.append(large_file_candidate(scan_id, node))

    return candidates


def stable_candidate_id(scan_id: int, node: sqlite3.Row, rule_id: str) -> str:
    material = f"{scan_id}\0{node['full_path']}\0{node['node_type']}\0{rule_id}".encode("utf-8")
    return "C" + hashlib.sha256(material).hexdigest()[:12].upper()


def candidate_from_rule(scan_id: int, node: sqlite3.Row, rule: Rule) -> Candidate:
    reclaimable = int(node["subtree_allocated_bytes"])
    return Candidate(
        candidate_id=stable_candidate_id(scan_id, node, rule.id),
        node_id=int(node["id"]),
        title=str(node["name"]),
        category=rule.category,
        reclaimable_bytes=reclaimable,
        risk=rule.risk,
        confidence=rule.confidence,
        recommended_action="recycle",
        backend="file",
        default_selectable=rule.default_selectable,
        evidence=f"{rule.evidence} 路径: {node['full_path']}",
    )


def large_file_candidate(scan_id: int, node: sqlite3.Row) -> Candidate:
    return Candidate(
        candidate_id=stable_candidate_id(scan_id, node, "large-file-review"),
        node_id=int(node["id"]),
        title=str(node["name"]),
        category="large_file",
        reclaimable_bytes=int(node["allocated_bytes"]),
        risk="review",
        confidence=0.55,
        recommended_action="manual_review",
        backend="file",
        default_selectable=False,
        evidence=f"单文件超过 500 MiB，需要人工确认用途。路径: {node['full_path']}",
    )


def insert_candidates(conn: sqlite3.Connection, scan_id: int, candidates: list[Candidate]) -> None:
    conn.executemany(
        """
        INSERT INTO candidates (
            scan_id, candidate_id, node_id, title, category, reclaimable_bytes,
            risk, confidence, recommended_action, backend, default_selectable, evidence
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                scan_id,
                candidate.candidate_id,
                candidate.node_id,
                candidate.title,
                candidate.category,
                candidate.reclaimable_bytes,
                candidate.risk,
                candidate.confidence,
                candidate.recommended_action,
                candidate.backend,
                int(candidate.default_selectable),
                candidate.evidence,
            )
            for candidate in candidates
        ],
    )

