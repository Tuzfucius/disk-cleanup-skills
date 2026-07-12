from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .config import ConfigError, diagnose_config, load_config
from .analyzer.candidate_builder import analyze_scan
from .indexer.database import import_wiztree_csv
from .indexer.queries import candidate_rows, extension_summary, largest_directories, largest_files, scan_summary, top_children
from .security.validation import validate_project
from .web.server import serve_forever
from .tasks import cleanup_expired, create_task, finalize_task, load_task, task_lock, update_task
from .cleaner.cleanup_plan import create_cleanup_plan, plan_to_dict
from .cleaner.session import CleanupSession


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

    diagnose = subparsers.add_parser("diagnose", help="检查配置和外部工具路径")
    diagnose.add_argument("--json", action="store_true", help="以 JSON 输出诊断结果")
    diagnose.set_defaults(func=handle_diagnose)

    audit = subparsers.add_parser("audit", help="第一阶段：导入扫描并创建可续接任务")
    audit.add_argument("--csv", required=True, help="WizTree CSV")
    audit.add_argument("--target", required=True, help="扫描根目录")
    audit.add_argument("--configured-max-depth", type=int, default=None, help="WizTree 导出深度；0 表示不限")
    audit.set_defaults(func=handle_task_audit)

    review = subparsers.add_parser("review", help="查看任务候选")
    review.add_argument("--run-id", required=True)
    review.add_argument("--limit", type=int, default=100)
    review.set_defaults(func=handle_review)

    plan = subparsers.add_parser("plan", help="生成不可变删除计划")
    plan.add_argument("--run-id", required=True)
    plan.add_argument("--candidate-id", action="append", required=True)
    plan.set_defaults(func=handle_plan)

    execute = subparsers.add_parser("execute", help="确认并执行回收站删除")
    execute.add_argument("--run-id", required=True)
    execute.add_argument("--plan-hash", required=True)
    execute.add_argument("--confirmation", required=True)
    execute.set_defaults(func=handle_execute)

    finalize = subparsers.add_parser("finalize", help="销毁任务数据")
    finalize.add_argument("--run-id", required=True)
    finalize.set_defaults(func=handle_finalize)

    index = subparsers.add_parser("index", help="导入 WizTree CSV 并建立 SQLite 索引")
    index.add_argument("--csv", required=True, help="WizTree CSV 路径")
    index.add_argument("--db", help="SQLite 输出路径；默认写入配置 workspace/database_name")
    index.set_defaults(func=handle_index)

    query = subparsers.add_parser("query", help="查询已建立的 SQLite 索引")
    query.add_argument("--db", required=True, help="SQLite 索引路径")
    query.add_argument("--scan-id", type=int, default=1, help="扫描 ID")
    query.add_argument("--path", default="C:\\", help="目录路径")
    query.add_argument("--limit", type=int, default=10, help="返回数量")
    query.set_defaults(func=handle_query)

    analyze = subparsers.add_parser("analyze", help="按规则生成候选项和 agent-context")
    analyze.add_argument("--db", required=True, help="SQLite 索引路径")
    analyze.add_argument("--scan-id", type=int, default=1, help="扫描 ID")
    analyze.add_argument("--context", help="agent-context.json 输出路径")
    analyze.add_argument("--max-candidates", type=int, default=300, help="最多生成候选项数量")
    analyze.set_defaults(func=handle_analyze)

    serve = subparsers.add_parser("serve", help="启动本地只读 HTML 审计界面")
    serve.add_argument("--db", required=True, help="SQLite 索引路径")
    serve.add_argument("--scan-id", type=int, default=1, help="扫描 ID")
    serve.add_argument("--host", default="127.0.0.1", help="监听地址")
    serve.add_argument("--port", type=int, default=0, help="监听端口，0 表示随机端口")
    serve.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    serve.set_defaults(func=handle_serve)

    validate = subparsers.add_parser("validate", help="验证 Skill 目录、schema 和配置模板")
    validate.set_defaults(func=handle_validate)

    return parser


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
