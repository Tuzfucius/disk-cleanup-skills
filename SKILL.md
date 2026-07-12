---
name: disk-cleanup-skills
description: Two-stage Windows disk audit and controlled recycle-bin cleanup skill. Use when Codex needs to scan with WizTree, analyze disk usage, create reviewable cleanup candidates, preserve an audit task by run_id, require an immutable plan and explicit confirmation, move approved paths to the Windows Recycle Bin, verify outcomes, and expire task data safely.
---

# disk-cleanup-skills

Use this skill for a strict two-stage workflow: audit first, then delete only explicitly approved candidates from that audit.

## Core Rules

- Do not read a full WizTree CSV into model context. Import it into SQLite and inspect summarized results.
- Allow browser requests to submit only `candidate_id` values.
- Do not accept arbitrary paths, shell commands, or BleachBit cleaner names from the browser.
- Do not execute cleanup before preview and explicit user confirmation.
- Execute deletion only through `disk_cleanup execute`; never fall back to `Remove-Item`, `rd`, Shell COM, arbitrary PowerShell, or arbitrary cleaner commands.
- Move approved targets to the Windows Recycle Bin only. Never permanently delete and never downgrade when recycling fails.
- Do not require the user to install the skill, run setup, or create persistent local config before use.
- Prefer the absolute path to `scripts/invoke-once.ps1`. Audit returns a `run_id`; task state expires after 24 hours or is removed by `finalize`.
- If the user does not provide a CSV, let `scripts/invoke-once.ps1` call WizTree command-line export into the temporary workspace.

- When the user provides a WizTree executable path, always pass it with -WizTreePath; do not search the whole disk.
- Wait for the bundled script to finish and use its scan, largest_directories, largest_files, extension_summary, and cleanup_candidates output for the analysis.
- If the script fails, report the stage and error. Do not copy the workflow into ad hoc PowerShell commands.

## Workflow References

- Read `references/audit.md` when the user only needs disk audit, space analysis, directory drill-down, or cleanup suggestions.
- Read `references/clean.md` when the user wants an interactive cleanup loop with selection, preview, confirmation, execution, and verification.

## Common Commands

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 -Mode validate
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 -Mode audit -Target "C:"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 -Mode audit -CsvPath "C:\path\to\wiztree-export.csv"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 -Mode review -RunId "<run_id>"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 -Mode plan -RunId "<run_id>" -CandidateId C0001,C0002
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 -Mode execute -RunId "<run_id>" -PlanHash "<hash>" -Confirmation "DELETE <short-id>"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 -Mode finalize -RunId "<run_id>"
```

Never combine audit and deletion in one unreviewed step. Report `BLOCKED`, `FAILED`, and `UNKNOWN` honestly; only `RECYCLED` means verified removal from the original path.
