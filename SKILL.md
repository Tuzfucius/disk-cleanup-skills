---
name: disk-cleanup-skills
description: Read-only Windows disk scanning and explicitly approved Recycle Bin cleanup. Use when Codex needs to analyze local disk usage, produce reviewable cleanup candidates, or recycle selected low- and medium-risk user files or directories through an immutable plan.
---

# disk-cleanup-skills

Expose only two workflows: `scan` and `clean`. Scanning is read-only. Cleaning may recycle only items from the matching scan.

## Core Rules

- Run the bundled script by absolute path. Never reproduce cleanup with ad hoc shell commands.
- Import large scans into SQLite; never place a complete WizTree CSV in model context.
- Accept only `run_id`, `candidate_id`, `plan_hash`, and the generated approval code during cleanup. Never accept a path, wildcard, command, or cleaner name.
- Treat planning and execution as separate user turns. After showing the exact immutable plan, stop and obtain fresh explicit approval from the user.
- Recycle through the bundled Windows executor only. Never use permanent deletion or fall back when the Recycle Bin operation fails.
- Never execute protected, administrator, cloud-sync, credential, or high-risk candidates.
- Report `RECYCLED`, `BLOCKED`, `FAILED`, and `UNKNOWN` honestly. Moving an item to the Recycle Bin does not mean disk space has already been released.
- Task state expires after 24 hours. Approval codes expire after 10 minutes and are single-use.

## Workflow References

- Read `references/audit.md` when the user needs scanning, analysis, directory drill-down, or cleanup suggestions.
- Read `references/clean.md` when the user wants an interactive cleanup loop with selection, preview, confirmation, execution, and verification.

## Common Commands

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 -Mode scan -Target "C:"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 -Mode scan -CsvPath "C:\path\to\wiztree-export.csv"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 -Mode clean -RunId "<run_id>" -CandidateId C0123456789AB
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 -Mode clean -RunId "<run_id>" -PlanHash "<hash>" -ApprovalCode "RECYCLE <code>"
```
