# disk-cleanup-skills

The `disk-cleanup-skills` project exposes only `scan` and `clean` on Windows. Scanning is read-only. Cleaning creates an immutable plan from candidate IDs and moves approved files or controlled directories to the Windows Recycle Bin only after approval in a later user turn.

## Safety Boundaries

- Auditing and cleanup are strictly separated. Auditing never modifies the file system.
- The cleanup entry point accepts only `run_id`, `candidate_id`, `plan_hash`, and a confirmation phrase. It does not accept arbitrary paths or commands.
- Before cleanup, the root path, protected directories, reparse points, file identity, and modification time are validated again.
- The executor supports files and controlled directories after reparse-point validation.
- A failed Recycle Bin operation is never downgraded to permanent deletion.
- The Web page is for review only. Actual cleanup can only be executed through this project's CLI.
- The project does not run BleachBit cleaner and does not use `Remove-Item`, `rd`, or Shell COM as fallbacks.

Even with these protections, validate the workflow with a test directory first. Do not select important personal files, project directories, or application data on the first run.

## Requirements

- Windows 10/11
- Python 3.11 or later
- 64-bit WizTree (optional, recommended for full-drive scans)
- PowerShell 5.1 or later

WizTree is not included. The skill falls back to a slower read-only streaming walk; obtain WizTree from an official channel when full-drive speed matters.

## Quick Start

Clone this repository and open PowerShell in its directory. No machine-specific configuration file is required. You can provide the WizTree path through an argument or environment variable:

```powershell
$env:DISK_CLEAN_WIZTREE = "C:\Tools\WizTree\WizTree64.exe"
```

### Stage 1: Scan

Scan a local drive and create a task that expires after 24 hours:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 -Mode scan -Target "C:"
```

For a portable or non-standard installation, provide the executable path explicitly:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 -Mode scan -Target "C:" -WizTreePath "C:\Tools\WizTree\WizTree64.exe"
```

The default `ExportMaxDepth` is `0`, which means export depth is unlimited. A full-drive scan may take several minutes. The script waits for WizTree to finish exporting the CSV, then reports directory, file, extension, and cleanup-candidate summaries.

You can also import an existing WizTree CSV:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 -Mode scan -Target "C:" -CsvPath "C:\path\to\wiztree-export.csv"
```

The command returns a `run_id`. Runtime data is stored under `LOCALAPPDATA\DiskCleanupSkill\runs` and is removed after expiration.

Candidates are shown in both command output and the read-only local HTML report.

### Stage 2: Plan and Cleanup

Select candidates only from the audit result:

```powershell
.\scripts\invoke-once.ps1 -Mode clean -RunId "<run_id>" -CandidateId "C0123456789AB","CABCDEF012345"
```

Review every exact path, risk, and `plan_hash` in the output. Then use the confirmation phrase printed by the command:

```powershell
.\scripts\invoke-once.ps1 -Mode clean -RunId "<run_id>" -PlanHash "<plan_hash>" -ApprovalCode "RECYCLE <code>"
```

Possible result states:

- `RECYCLED`: the original path no longer exists and the Recycle Bin operation completed.
- `BLOCKED`: a safety validation rejected the operation.
- `FAILED`: the Windows Recycle Bin operation failed.
- `UNKNOWN`: the result could not be verified reliably and must not be treated as success.

Task data expires after 24 hours. Approval codes expire after 10 minutes and are single-use.

## Local Configuration and Privacy

`config.local.toml` is intended for local overrides and is excluded by `.gitignore`. Do not commit real WizTree CSV files, SQLite indexes, scan reports, cleanup plans, usernames, absolute installation paths, personal directories, API keys, tokens, screenshots, browser history, terminal logs, caches, or build artifacts.

Before submitting changes, run:

```powershell
git status --ignored --short
git grep -n -I -E "Users\\|[A-Z]:\\|api[_-]?key|password|secret|token"
```

Examples and tests must use fictional usernames, placeholder paths, and synthetic data.

## Development

```powershell
$env:PYTHONPATH = "src"
python -m pytest tests --basetemp .pytest_tmp
python -m compileall -q src tests
```

Project structure:

- `SKILL.md`: Skill metadata and non-bypassable rules.
- `agents/`: Codex UI metadata.
- `references/`: Audit and cleanup workflow references.
- `scripts/`: PowerShell entry points.
- `src/disk_cleanup/`: Indexing, analysis, task, Web, and secure execution code.
- `rules/`: Candidate detection and protected-path rules.
- `schemas/`: Public JSON data structures.
- `tests/`: Synthetic fixtures and automated tests.

## License

This project is licensed under the MIT License. See `LICENSE`. Third-party tools and trademarks remain the property of their respective owners.
