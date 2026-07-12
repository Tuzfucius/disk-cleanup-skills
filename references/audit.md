# Disk Audit Workflow

Use this workflow for read-only disk auditing. Do not delete, move, uninstall, or clean files during this stage.

## Steps

1. Resolve the absolute Skill directory and bundled PowerShell script.
2. If the user supplied a WizTree executable path, pass it through -WizTreePath. Do not recursively search drives for the executable.
3. Run audit with either -Target or an existing -CsvPath.
4. Wait for the script to finish. WizTree CSV export may take several minutes for a full drive.
5. Read the compact JSON sections scan, largest_directories, largest_files, extension_summary, and cleanup_candidates. Never read the complete CSV into model context.
6. State whether configured_max_depth caused truncation. The default value 0 means unlimited.
7. Retain the returned run_id for the second stage. Its SQLite index is stored under local application data for 24 hours.
8. Finalize the run when the user no longer needs review or cleanup.

## Commands

~~~powershell
.\scripts\invoke-once.ps1 -Mode audit -Target "C:" -WizTreePath "C:\Tools\WizTree\WizTree64.exe"
~~~

~~~powershell
.\scripts\invoke-once.ps1 -Mode audit -Target "C:" -CsvPath "C:\path\to\wiztree-export.csv"
~~~

If execution fails, report the stage and exact error. Do not reproduce the workflow using ad hoc PowerShell commands.
