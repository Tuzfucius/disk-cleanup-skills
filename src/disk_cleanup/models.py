from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ToolConfig:
    wiztree_executable: Path


@dataclass(frozen=True)
class ScanConfig:
    targets: tuple[str, ...]
    request_admin: bool
    include_files: bool
    include_folders: bool
    sort_by: str
    retain_scan_count: int


@dataclass(frozen=True)
class StorageConfig:
    workspace: Path
    database_name: str


@dataclass(frozen=True)
class CleanupConfig:
    file_delete_mode: str
    require_preview: bool
    verify_after_cleanup: bool


@dataclass(frozen=True)
class LoggingConfig:
    level: str
    retain_days: int


@dataclass(frozen=True)
class AppConfig:
    config_version: int
    tools: ToolConfig
    scan: ScanConfig
    storage: StorageConfig
    cleanup: CleanupConfig
    logging: LoggingConfig
    source_path: Path


@dataclass(frozen=True)
class ToolCheck:
    name: str
    path: Path
    exists: bool
    is_file: bool

    @property
    def ok(self) -> bool:
        return self.exists and self.is_file


@dataclass(frozen=True)
class ConfigDiagnostic:
    config: AppConfig
    tool_checks: tuple[ToolCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.tool_checks)


@dataclass(frozen=True)
class WizTreeNode:
    full_path: str
    name: str
    parent_path: str | None
    node_type: str
    logical_bytes: int
    allocated_bytes: int
    subtree_allocated_bytes: int
    modified_at: str
    attributes: str
    file_count: int
    folder_count: int
    depth: int
    extension: str


@dataclass(frozen=True)
class ScanMetadata:
    source: str
    generated_by: str
    root_path: str | None
    drive_capacity: int | None
    free_space: int | None
    used_space: int | None
    reserved_space: int | None


@dataclass(frozen=True)
class ImportSummary:
    scan_id: int
    rows: int
    files: int
    folders: int
    max_depth: int
    total_file_allocated_bytes: int


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    node_id: int
    title: str
    category: str
    reclaimable_bytes: int
    risk: str
    confidence: float
    recommended_action: str
    backend: str
    default_selectable: bool
    evidence: str


@dataclass(frozen=True)
class AnalysisSummary:
    scan_id: int
    candidate_count: int
    reclaimable_bytes: int
    context_path: Path | None


@dataclass(frozen=True)
class CleanupAction:
    candidate_id: str
    node_id: int
    backend: str
    action_type: str
    action_value: str
    path: str
    risk: str
    expected_reclaim_bytes: int
    volume_serial: int | None = None
    file_id: int | None = None
    modified_ns: int | None = None


@dataclass(frozen=True)
class CleanupPlan:
    plan_id: str
    scan_id: int
    created_at: str
    expected_reclaim_bytes: int
    actions: tuple[CleanupAction, ...]
    plan_hash: str
    run_id: str = ""
    expires_at: str = ""
