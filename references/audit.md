# Windows Disk Scan Workflow

Use this workflow for read-only disk auditing. Do not delete, move, uninstall, or clean files during this stage.

## Steps

1. Resolve the absolute Skill directory and bundled PowerShell script.
2. If the user supplied a WizTree executable path, pass it through -WizTreePath. Do not recursively search drives for the executable.
3. Run `scan` with either `-Target` or an existing `-CsvPath`. If WizTree is unavailable, allow the bundled read-only fallback scanner to run.
4. Wait for the script to finish. WizTree CSV export may take several minutes for a full drive.
5. Read the compact JSON sections scan, largest_directories, largest_files, extension_summary, and cleanup_candidates. Never read the complete CSV into model context.
6. State the provider, filesystem, scan fingerprint, and whether a time, entry, or depth budget caused truncation.
7. Retain the returned `run_id` for `clean`. Its SQLite index expires after 24 hours.
8. Treat protected and incomplete-scan directory candidates as report-only.

## Commands

~~~powershell
.\scripts\invoke-once.ps1 -Mode scan -Target "C:" -WizTreePath "C:\Tools\WizTree\WizTree64.exe"
~~~

~~~powershell
.\scripts\invoke-once.ps1 -Mode scan -Target "C:" -CsvPath "C:\path\to\wiztree-export.csv"
~~~

If execution fails, report the stage and exact error. Do not reproduce the workflow using ad hoc PowerShell commands.
