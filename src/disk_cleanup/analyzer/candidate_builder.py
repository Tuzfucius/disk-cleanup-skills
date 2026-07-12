from __future__ import annotations

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
    ).fetchall()
    candidates: list[Candidate] = []
    seen_nodes: set[int] = set()

    for node in nodes:
        if len(candidates) >= max_candidates:
            break
        path = str(node["full_path"])
        if protected_reason(path, protected_paths):
            continue
        for rule in rules:
            if rule.path_regex.search(path) and (rule.backend != "file" or node["node_type"] == "file"):
                candidates.append(candidate_from_rule(len(candidates) + 1, node, rule))
                seen_nodes.add(int(node["id"]))
                break

    for node in nodes:
        if len(candidates) >= max_candidates:
            break
        if int(node["id"]) in seen_nodes:
            continue
        path = str(node["full_path"])
        if protected_reason(path, protected_paths):
            continue
        if node["node_type"] == "file" and int(node["allocated_bytes"]) >= LARGE_FILE_THRESHOLD:
            candidates.append(large_file_candidate(len(candidates) + 1, node))

    return candidates


def candidate_from_rule(index: int, node: sqlite3.Row, rule: Rule) -> Candidate:
    reclaimable = int(node["subtree_allocated_bytes"])
    return Candidate(
        candidate_id=f"C{index:04d}",
        node_id=int(node["id"]),
        title=str(node["name"]),
        category=rule.category,
        reclaimable_bytes=reclaimable,
        risk=rule.risk,
        confidence=rule.confidence,
        recommended_action=rule.action,
        backend=rule.backend,
        default_selectable=rule.default_selectable,
        evidence=f"{rule.evidence} 路径: {node['full_path']}",
    )


def large_file_candidate(index: int, node: sqlite3.Row) -> Candidate:
    return Candidate(
        candidate_id=f"C{index:04d}",
        node_id=int(node["id"]),
        title=str(node["name"]),
        category="large_file",
        reclaimable_bytes=int(node["allocated_bytes"]),
        risk="medium",
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

