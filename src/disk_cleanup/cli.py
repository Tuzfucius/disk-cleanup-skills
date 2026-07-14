from __future__ import annotations

import argparse
import hashlib
import json
import ntpath
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .config import ConfigError, diagnose_config, load_config
from .analyzer.candidate_builder import analyze_scan
from .indexer.database import import_nodes, import_wiztree_csv
from .indexer.queries import candidate_rows, extension_summary, largest_directories, largest_files, scan_summary, top_children
from .security.validation import validate_project
from .web.server import serve_forever
from .models import ScanMetadata
from .scanner.walk import walk_windows_tree
from .security.paths import canonical_local_path
from .tasks import cleanup_expired, create_task, finalize_task, load_task, task_lock, update_task
from .cleaner.cleanup_plan import create_cleanup_plan, plan_to_dict
from .cleaner.session import CleanupSession
from .cleaner.audit import prune_audit


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ConfigError, ValueError, OSError) as exc:
        print(f"错误: {exc}")
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="disk-cleanup", description="磁盘审计与清理 Skill 本地工具")
    parser.add_argument("--config", help="配置文件路径，默认读取 config.local.toml 或 config.example.toml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="只读扫描并生成分析报告")
    scan.add_argument("--target", required=True, help="本地绝对目录或盘符")
    scan.add_argument("--wiztree", help="WizTree64.exe 路径")
    scan.add_argument("--csv", help=argparse.SUPPRESS)
    scan.add_argument("--max-entries", type=int, default=5_000_000)
    scan.add_argument("--timeout-seconds", type=float, default=1800)
    scan.add_argument("--no-open", action="store_true")
    scan.add_argument("--no-report", action="store_true", help=argparse.SUPPRESS)
    scan.set_defaults(func=handle_scan)

    clean = subparsers.add_parser("clean", help="生成计划或执行已批准的回收站清理")
    clean.add_argument("--run-id", required=True)
    clean.add_argument("--candidate-id", action="append")
    clean.add_argument("--plan-hash")
    clean.add_argument("--approval-code")
    clean.set_defaults(func=handle_clean)

    return parser


def handle_scan(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    cleanup_expired(config.storage.workspace)
    target = canonical_local_path(args.target)
    task = create_task(config.storage.workspace, str(target))
    csv_path: Path | None = None
    try:
        provider = "scandir"
        truncated = False
        truncation_reason = None
        errors: list[tuple[str, str]] = []
        if args.csv:
            csv_path = Path(args.csv).resolve(strict=True)
            provider = "wiztree_csv"
            source_fingerprint = _sha256_file(csv_path)
            summary = import_wiztree_csv(csv_path, task.db_path)
        else:
            wiztree = Path(args.wiztree).resolve(strict=True) if args.wiztree else config.tools.wiztree_executable
            if wiztree.is_file():
                provider = "wiztree"
                csv_path = task.root / "wiztree-export.csv"
                _export_wiztree(wiztree, target, csv_path, args.timeout_seconds)
                source_fingerprint = _sha256_file(csv_path)
                summary = import_wiztree_csv(csv_path, task.db_path)
                csv_path.unlink(missing_ok=True)
            else:
                usage = shutil.disk_usage(target)
                walker = walk_windows_tree(
                    target, max_entries=args.max_entries, timeout_seconds=args.timeout_seconds
                )
                metadata = ScanMetadata(
                    source=str(target), generated_by="disk-cleanup scandir",
                    root_path=str(target), drive_capacity=usage.total,
                    free_space=usage.free, used_space=usage.used, reserved_space=0,
                )
                summary = import_nodes(metadata, walker, task.db_path)
                truncated = walker.stats.truncated
                truncation_reason = walker.stats.truncation_reason
                errors = walker.stats.errors
                source_fingerprint = hashlib.sha256(
                    f"{target}\0{summary.rows}\0{summary.total_file_allocated_bytes}".encode("utf-8")
                ).hexdigest()

        scan_data = scan_summary(task.db_path, summary.scan_id)
        _assert_matching_scan_root(str(target), str(scan_data.get("root_path") or ""))
        analysis = analyze_scan(task.db_path, summary.scan_id)
        if truncated:
            with sqlite3.connect(task.db_path) as conn:
                conn.execute(
                    """
                    UPDATE candidates
                    SET risk = 'review', default_selectable = 0,
                        evidence = evidence || ' 扫描结果不完整，目录不可执行。'
                    WHERE scan_id = ? AND node_id IN (
                        SELECT id FROM nodes WHERE scan_id = ? AND node_type = 'directory'
                    )
                    """,
                    (summary.scan_id, summary.scan_id),
                )
                conn.commit()
        rule_hash = _rule_pack_hash()
        update_task(
            task, state="SCANNED", target=str(target), scan_id=summary.scan_id,
            provider=provider, source_fingerprint=source_fingerprint,
            rule_pack_hash=rule_hash, truncated=truncated,
            truncation_reason=truncation_reason, scan_errors=len(errors),
            candidate_count=analysis.candidate_count,
        )
        report_url = None if args.no_report else _launch_report(
            task.db_path, summary.scan_id, task.run_id, open_browser=not args.no_open
        )
        payload = {
            "run_id": task.run_id, "expires_at": task.expires_at,
            "provider": provider,
            "completeness": {
                "truncated": truncated, "reason": truncation_reason,
                "errors": [{"path": path, "message": message} for path, message in errors[:20]],
            },
            "scan": scan_summary(task.db_path, summary.scan_id),
            "largest_directories": largest_directories(task.db_path, summary.scan_id, 10),
            "largest_files": largest_files(task.db_path, summary.scan_id, 10),
            "extension_summary": extension_summary(task.db_path, summary.scan_id, 10),
            "cleanup_candidates": candidate_rows(task.db_path, summary.scan_id, 100),
            "report_url": report_url,
            "notice": "候选字节数表示可移入回收站的数据；清空回收站前不代表已释放空间。",
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except Exception:
        finalize_task(config.storage.workspace, task.run_id)
        raise


def handle_clean(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    task = load_task(config.storage.workspace, args.run_id)
    meta = json.loads(task.metadata_path.read_text(encoding="utf-8"))
    audit_path = config.storage.workspace / "audit" / "cleanup.jsonl"
    prune_audit(path=audit_path, retain_days=config.logging.retain_days)
    if meta.get("rule_pack_hash") != _rule_pack_hash():
        update_task(task, state="NEEDS_REVIEW")
        raise ValueError("规则包自扫描后已变化，请重新扫描")
    with task_lock(task):
        if args.candidate_id:
            if args.plan_hash or args.approval_code:
                raise ValueError("生成计划与执行计划必须分成两次调用")
            session = CleanupSession(
                task.db_path, int(meta["scan_id"]), allowed_root=str(meta["target"]),
                run_id=task.run_id, expires_at=task.expires_at,
                protected_roots=(task.root, Path.cwd()), audit_path=audit_path,
                scan_fingerprint=str(meta.get("source_fingerprint", "")),
                rule_pack_hash=str(meta.get("rule_pack_hash", "")),
                scan_truncated=bool(meta.get("truncated", False)),
            )
            session.selection(args.candidate_id)
            result = session.generate_preview()
            update_task(task, state="PLANNED", plan_hash=result["plan"]["plan_hash"])
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if not args.plan_hash or not args.approval_code:
            raise ValueError("clean 必须提供 candidate-id，或同时提供 plan-hash 和 approval-code")
        session = CleanupSession(
            task.db_path, int(meta["scan_id"]), allowed_root=str(meta["target"]),
            run_id=task.run_id, expires_at=task.expires_at,
            protected_roots=(task.root, Path.cwd()), audit_path=audit_path,
            scan_fingerprint=str(meta.get("source_fingerprint", "")),
            rule_pack_hash=str(meta.get("rule_pack_hash", "")),
            scan_truncated=bool(meta.get("truncated", False)),
        )
        try:
            session.confirm(args.plan_hash, args.approval_code)
            update_task(task, state="APPROVED")
            update_task(task, state="EXECUTING")
            result = session.execute(args.plan_hash)
            update_task(task, state=result["state"], result=result["result"])
        except ValueError:
            update_task(task, state="NEEDS_REVIEW")
            raise
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0


def _export_wiztree(executable: Path, target: Path, output: Path, timeout_seconds: float) -> None:
    command = [
        str(executable), str(target), f"/export={output}", "/admin=0",
        "/exportfolders=1", "/exportfiles=1", "/sortby=2",
        "/exportdrivecapacity=1", "/exportmaxdepth=0",
    ]
    completed = subprocess.run(command, check=False, timeout=timeout_seconds)
    if completed.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        raise OSError(f"WizTree 导出失败，退出码: {completed.returncode}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _rule_pack_hash() -> str:
    digest = hashlib.sha256()
    rules_dir = Path(__file__).resolve().parents[2] / "rules"
    for path in sorted(rules_dir.glob("*.toml")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _assert_matching_scan_root(target: str, scanned: str) -> None:
    expected = ntpath.normcase(ntpath.normpath(target))
    actual = ntpath.normcase(ntpath.normpath(scanned))
    if not actual or actual != expected:
        raise ValueError(f"扫描结果根目录与请求目标不一致: {scanned!r} != {target!r}")


def _launch_report(db_path: Path, scan_id: int, run_id: str, *, open_browser: bool) -> str:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    token = secrets.token_urlsafe(24)
    command = [
        sys.executable, "-m", "disk_cleanup.web", "--db", str(db_path),
        "--scan-id", str(scan_id), "--port", str(port), "--token", token,
        "--run-id", run_id,
    ]
    if not open_browser:
        command.append("--no-open")
    kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    subprocess.Popen(command, **kwargs)
    return f"http://127.0.0.1:{port}/?token={token}"


def handle_diagnose(args: argparse.Namespace) -> int:
    diagnostic = diagnose_config(args.config)
    if args.json:
        print(json.dumps(to_jsonable(asdict(diagnostic)), ensure_ascii=False, indent=2))
    else:
        print(f"配置文件: {diagnostic.config.source_path}")
        print(f"工作目录: {diagnostic.config.storage.workspace}")
        print(f"扫描目标: {', '.join(diagnostic.config.scan.targets)}")
        for check in diagnostic.tool_checks:
            status = "OK" if check.ok else "MISSING"
            print(f"{check.name}: {status} - {check.path}")
    return 0 if diagnostic.ok else 1


def handle_task_audit(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    cleanup_expired(config.storage.workspace)
    task = create_task(config.storage.workspace, args.target)
    summary = import_wiztree_csv(Path(args.csv), task.db_path)
    analysis = analyze_scan(task.db_path, summary.scan_id)
    update_task(task, scan_id=summary.scan_id, rows=summary.rows, max_depth=summary.max_depth,
                candidate_count=analysis.candidate_count, estimated_reclaim_bytes=analysis.reclaimable_bytes)
    scan_data = scan_summary(task.db_path, summary.scan_id)
    public_scan = {
        key: scan_data.get(key)
        for key in (
            "id", "root_path", "drive_capacity", "free_space", "used_space",
            "reserved_space", "files", "folders", "max_depth",
            "file_allocated_bytes", "candidate_count", "reclaimable_bytes",
        )
    }
    audit_summary = {
        "run_id": task.run_id,
        "expires_at": task.expires_at,
        "scan": public_scan,
        "import": {
            "rows": summary.rows,
            "files": summary.files,
            "folders": summary.folders,
            "max_depth": summary.max_depth,
            "configured_max_depth": args.configured_max_depth,
            "truncated": args.configured_max_depth is not None and args.configured_max_depth > 0,
        },
        "largest_directories": largest_directories(task.db_path, summary.scan_id, 10),
        "largest_files": largest_files(task.db_path, summary.scan_id, 10),
        "extension_summary": extension_summary(task.db_path, summary.scan_id, 10),
        "cleanup_candidates": candidate_rows(task.db_path, summary.scan_id, 20),
        "estimated_reclaim_bytes": analysis.reclaimable_bytes,
        "notice": "estimated_reclaim_bytes 是候选估算值，不代表已释放空间。",
        "next": f"invoke-once.ps1 -Mode review -RunId {task.run_id}",
    }
    print(json.dumps(audit_summary, ensure_ascii=False, indent=2))
    return 0


def _task_and_meta(args: argparse.Namespace):
    config = load_config(args.config)
    task = load_task(config.storage.workspace, args.run_id)
    meta = json.loads(task.metadata_path.read_text(encoding="utf-8"))
    return config, task, meta


def handle_review(args: argparse.Namespace) -> int:
    _config, task, meta = _task_and_meta(args)
    rows = candidate_rows(task.db_path, int(meta["scan_id"]), min(max(args.limit, 1), 500))
    print(json.dumps({"run_id": task.run_id, "expires_at": task.expires_at, "candidates": rows}, ensure_ascii=False, indent=2))
    return 0


def handle_plan(args: argparse.Namespace) -> int:
    _config, task, meta = _task_and_meta(args)
    plan = create_cleanup_plan(task.db_path, int(meta["scan_id"]), args.candidate_id, run_id=task.run_id, expires_at=task.expires_at)
    payload = plan_to_dict(plan)
    (task.root / "plan.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    update_task(task, state="PREVIEWED", plan_hash=plan.plan_hash)
    payload["confirmation"] = f"DELETE {task.run_id[:8]}"
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def handle_execute(args: argparse.Namespace) -> int:
    _config, task, meta = _task_and_meta(args)
    with task_lock(task):
        meta = json.loads(task.metadata_path.read_text(encoding="utf-8"))
        if meta.get("state") != "PREVIEWED":
            raise ValueError("任务不在可执行状态，禁止重复或并发执行")
        plan_payload = json.loads((task.root / "plan.json").read_text(encoding="utf-8"))
        if plan_payload["plan_hash"] != args.plan_hash or meta.get("plan_hash") != args.plan_hash:
            raise ValueError("plan hash 不匹配")
        update_task(task, state="EXECUTING")
        candidate_ids = [item["candidate_id"] for item in plan_payload["actions"]]
        session = CleanupSession(task.db_path, int(meta["scan_id"]), allowed_root=str(meta["target"]),
                                 run_id=task.run_id, expires_at=task.expires_at, protected_roots=(task.root, Path.cwd()))
        session.selection(candidate_ids)
        preview = session.generate_preview()
        if preview["plan"]["plan_hash"] != args.plan_hash:
            update_task(task, state="NEEDS_REVIEW")
            raise ValueError("目标自计划生成后已变化，请重新生成计划")
        session.confirm(args.plan_hash)
        result = session.execute(args.plan_hash, args.confirmation)
        update_task(task, state=result["state"], result=result["result"])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def handle_finalize(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    task = load_task(config.storage.workspace, args.run_id)
    with task_lock(task):
        meta = json.loads(task.metadata_path.read_text(encoding="utf-8"))
        if meta.get("state") == "EXECUTING":
            raise ValueError("任务正在执行，禁止销毁")
    finalize_task(config.storage.workspace, args.run_id)
    print(json.dumps({"run_id": args.run_id, "state": "FINALIZED"}, ensure_ascii=False))
    return 0


def handle_audit_placeholder(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    csv_text = f"，CSV: {Path(args.csv)}" if args.csv else ""
    print(f"审计流程入口已就绪，配置: {config.source_path}{csv_text}")
    return 0


def handle_clean_placeholder(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    print(f"清理流程入口已就绪，配置: {config.source_path}")
    return 0


def handle_index(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    db_path = Path(args.db) if args.db else config.storage.workspace / config.storage.database_name
    summary = import_wiztree_csv(Path(args.csv), db_path)
    print(json.dumps(to_jsonable(asdict(summary)), ensure_ascii=False, indent=2))
    print(f"SQLite 索引: {db_path}")
    return 0


def handle_query(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    rows = {
        "top_children": top_children(db_path, args.scan_id, args.path, args.limit),
        "largest_files": largest_files(db_path, args.scan_id, args.limit),
        "extension_summary": extension_summary(db_path, args.scan_id, args.limit),
    }
    print(json.dumps(to_jsonable(rows), ensure_ascii=False, indent=2))
    return 0


def handle_analyze(args: argparse.Namespace) -> int:
    context_path = Path(args.context) if args.context else None
    summary = analyze_scan(
        Path(args.db),
        args.scan_id,
        context_path=context_path,
        max_candidates=args.max_candidates,
    )
    print(json.dumps(to_jsonable(asdict(summary)), ensure_ascii=False, indent=2))
    return 0


def handle_serve(args: argparse.Namespace) -> int:
    serve_forever(
        Path(args.db),
        args.scan_id,
        host=args.host,
        port=args.port,
        open_browser=not args.no_open,
    )
    return 0


def handle_validate(args: argparse.Namespace) -> int:
    result = validate_project()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]
    return value
