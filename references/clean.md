# Disk Clean Workflow

Use this workflow only after a completed scan. It performs controlled moves to the Windows Recycle Bin.

## Required Two-Turn Flow

1. Load the existing `run_id`; never substitute a path supplied during stage two.
2. Review candidates and let the user select `candidate_id` values.
3. Generate an immutable plan and show every exact path, item kind, estimated bytes, risk, and protection result.
4. Stop. A planning request or vague instruction is not approval to execute.
5. In a later user turn, require the matching `plan_hash` and generated `RECYCLE <code>` approval code. The code is single-use and expires after 10 minutes.
6. Execute through the bundled CLI only. Never use permanent deletion or a shell fallback.
7. Report every item as `RECYCLED`, `BLOCKED`, `FAILED`, or `UNKNOWN`. Describe bytes as moved to the Recycle Bin, not released disk space.

## Command

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 `
  -Mode clean `
  -RunId "<run_id>" `
  -CandidateId "<candidate_id>"
```

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 `
  -Mode clean `
  -RunId "<run_id>" `
  -PlanHash "<plan_hash>" `
  -ApprovalCode "RECYCLE <code>"
```
