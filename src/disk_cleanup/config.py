from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from .models import (
    AppConfig,
    ConfigDiagnostic,
    LoggingConfig,
    ScanConfig,
    StorageConfig,
    ToolCheck,
    ToolConfig,
)

PACKAGE_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(ValueError):
    """Raised when configuration is missing or invalid."""


def default_config_path() -> Path:
    local_path = PACKAGE_ROOT / "config.local.toml"
    if local_path.exists():
        return local_path
    return PACKAGE_ROOT / "config.example.toml"


def expand_path(value: str) -> Path:
    return Path(os.path.expandvars(value)).expanduser()


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = Path(path) if path else default_config_path()
    if not config_path.exists():
        raise ConfigError(f"配置文件不存在: {config_path}")

    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    raw = apply_env_overrides(raw)
    return parse_config(raw, config_path)


def apply_env_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    data = dict(raw)
    tools = dict(data.get("tools", {}))
    scan = dict(data.get("scan", {}))
    storage = dict(data.get("storage", {}))

    if os.environ.get("DISK_CLEAN_WIZTREE"):
        tools["wiztree_executable"] = os.environ["DISK_CLEAN_WIZTREE"]
    if os.environ.get("DISK_CLEAN_TARGETS"):
        scan["targets"] = [
            part.strip()
            for part in os.environ["DISK_CLEAN_TARGETS"].split(";")
            if part.strip()
        ]
    if os.environ.get("DISK_CLEAN_WORKSPACE"):
        storage["workspace"] = os.environ["DISK_CLEAN_WORKSPACE"]

    data["tools"] = tools
    data["scan"] = scan
    data["storage"] = storage
    return data


def parse_config(raw: dict[str, Any], source_path: Path) -> AppConfig:
    tools = raw.get("tools", {})
    scan = raw.get("scan", {})
    storage = raw.get("storage", {})
    logging = raw.get("logging", {})

    try:
        return AppConfig(
            config_version=int(raw.get("config_version", 1)),
            tools=ToolConfig(
                wiztree_executable=expand_path(str(tools["wiztree_executable"])),
            ),
            scan=ScanConfig(
                targets=tuple(scan.get("targets", ["C:"])),
                retain_scan_count=int(scan.get("retain_scan_count", 5)),
            ),
            storage=StorageConfig(
                workspace=expand_path(str(storage.get("workspace", "%LOCALAPPDATA%\\DiskCleanupSkill"))),
                database_name=str(storage.get("database_name", "index.sqlite3")),
            ),
            logging=LoggingConfig(
                level=str(logging.get("level", "INFO")),
                retain_days=int(logging.get("retain_days", 30)),
            ),
            source_path=source_path,
        )
    except KeyError as exc:
        raise ConfigError(f"配置缺少必需字段: {exc.args[0]}") from exc


def diagnose_config(path: str | Path | None = None) -> ConfigDiagnostic:
    config = load_config(path)
    checks = (
        check_tool("WizTree", config.tools.wiztree_executable),
    )
    return ConfigDiagnostic(config=config, tool_checks=checks)


def check_tool(name: str, path: Path) -> ToolCheck:
    return ToolCheck(name=name, path=path, exists=path.exists(), is_file=path.is_file())

