# Disk Clean Workflow

Use this workflow only after a completed audit. It performs controlled, recoverable deletion through the Windows Recycle Bin.

## One-Shot Steps

1. Load the existing `run_id`; never substitute a path supplied during stage two.
2. Review candidates and let the user select `candidate_id` values.
3. Generate an immutable plan and show every exact path, estimated size, risk, and protection result.
4. Require the matching `plan_hash` and displayed `DELETE <short-id>` phrase.
5. Execute through the bundled CLI only. Do not use shell deletion as a fallback.
6. Report every item as `RECYCLED`, `BLOCKED`, `FAILED`, or `UNKNOWN` and verify the original path.
7. Finalize the task when the user is finished.

## Command

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 `
  -Mode serve `
  -Target "C:"
```

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 `
  -Mode serve `
  -CsvPath "C:\path\to\wiztree-export.csv"
```
