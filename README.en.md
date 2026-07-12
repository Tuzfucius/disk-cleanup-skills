# disk-cleanup-skills

The `disk-cleanup-skills` project is a two-stage Windows disk audit and safe cleanup skill. The first stage indexes disk usage with WizTree and produces reviewable candidates. The second stage creates an immutable plan from candidate IDs belonging to the same task and, after explicit user confirmation, moves approved files to the Windows Recycle Bin.

## Safety Boundaries

- Auditing and cleanup are strictly separated. Auditing never modifies the file system.
- The cleanup entry point accepts only `run_id`, `candidate_id`, `plan_hash`, and a confirmation phrase. It does not accept arbitrary paths or commands.
- Before cleanup, the root path, protected directories, reparse points, file identity, and modification time are validated again.
- The current executor recycles one file at a time and does not recursively delete directories.
- A failed Recycle Bin operation is never downgraded to permanent deletion.
- The Web page is for review only. Actual cleanup can only be executed through this project's CLI.
- The project does not run BleachBit cleaner and does not use `Remove-Item`, `rd`, or Shell COM as fallbacks.

Even with these protections, validate the workflow with a test directory first. Do not select important personal files, project directories, or application data on the first run.

## Requirements

- Windows 10/11
- Python 3.11 or later
- 64-bit WizTree
- PowerShell 5.1 or later

WizTree is not included. Obtain it through an official channel and follow its own license terms.

## Quick Start

Clone this repository and open PowerShell in its directory. No machine-specific configuration file is required. You can provide the WizTree path through an argument or environment variable:

```powershell
$env:DISK_CLEAN_WIZTREE = "C:\Tools\WizTree\WizTree64.exe"
```

Validate the skill:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 -Mode validate
```

### Stage 1: Audit

Scan a local drive and create a task that expires after 24 hours:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 -Mode audit -Target "C:"
```

For a portable or non-standard installation, provide the executable path explicitly:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 -Mode audit -Target "C:" -WizTreePath "C:\Tools\WizTree\WizTree64.exe"
```

The default `ExportMaxDepth` is `0`, which means export depth is unlimited. A full-drive scan may take several minutes. The script waits for WizTree to finish exporting the CSV, then reports directory, file, extension, and cleanup-candidate summaries.

You can also import an existing WizTree CSV:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 -Mode audit -Target "C:" -CsvPath "C:\path\to\wiztree-export.csv"
```

The command returns a `run_id`. Runtime data is stored under `LOCALAPPDATA\DiskCleanupSkill\runs` and is removed after expiration.

Review candidates:

```powershell
.\scripts\invoke-once.ps1 -Mode review -RunId "<run_id>"
```

### Stage 2: Plan and Cleanup

Select candidates only from the audit result:

```powershell
.\scripts\invoke-once.ps1 -Mode plan -RunId "<run_id>" -CandidateId "C0001","C0002"
```

Review every exact path, risk, and `plan_hash` in the output. Then use the confirmation phrase printed by the command:

```powershell
.\scripts\invoke-once.ps1 -Mode execute -RunId "<run_id>" -PlanHash "<plan_hash>" -Confirmation "DELETE <short-id>"
```

Possible result states:

- `RECYCLED`: the original path no longer exists and the Recycle Bin operation completed.
- `BLOCKED`: a safety validation rejected the operation.
- `FAILED`: the Windows Recycle Bin operation failed.
- `UNKNOWN`: the result could not be verified reliably and must not be treated as success.

Remove task data when finished:

```powershell
.\scripts\invoke-once.ps1 -Mode finalize -RunId "<run_id>"
```

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
python -m disk_cleanup validate
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
