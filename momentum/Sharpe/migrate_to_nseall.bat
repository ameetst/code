@echo off
setlocal enabledelayedexpansion

:: ============================================================
::  N750 -> NSEAll Migration (hard cutover)
::
::  Copies live N750 holdings, trade history and equity curve
::  into the NSEAll file set, then points dashboard_config.json
::  at the NSEAll universe. Every file this script overwrites is
::  backed up first. N750 source files are only read, never
::  modified.
:: ============================================================

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

echo.
echo ============================================================
echo  N750 to NSEAll Migration
echo ============================================================
echo Working directory: %CD%
echo.

:: --- Pre-flight: required tools ---
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] python not found on PATH. Aborting.
    goto :end
)

:: --- Pre-flight: required files ---
if not exist "N750_positions_ledger.json" (
    echo [ERROR] N750_positions_ledger.json not found. Aborting.
    goto :end
)
if not exist "N750_tradelog.json" (
    echo [ERROR] N750_tradelog.json not found. Aborting.
    goto :end
)
if not exist "N750_equity_history.json" (
    echo [ERROR] N750_equity_history.json not found. Aborting.
    goto :end
)
if not exist "NSEAll_positions_ledger.json" (
    echo [ERROR] NSEAll_positions_ledger.json not found. Aborting.
    goto :end
)
if not exist "NSEAll_tradelog.json" (
    echo [ERROR] NSEAll_tradelog.json not found. Aborting.
    goto :end
)
if not exist "NSEAll_equity_history.json" (
    echo [ERROR] NSEAll_equity_history.json not found. Aborting.
    goto :end
)
if not exist "dashboard_config.json" (
    echo [ERROR] dashboard_config.json not found. Aborting.
    goto :end
)

:: --- Guard: don't silently clobber a non-empty NSEAll ledger on a re-run ---
python -c "import json,sys; d=json.load(open('NSEAll_positions_ledger.json')); sys.exit(1 if d else 0)"
if errorlevel 1 (
    echo [WARNING] NSEAll_positions_ledger.json already contains open positions.
    echo           Running this migration will OVERWRITE them with the current
    echo           N750 holdings.
    set /p CONFIRM="Continue anyway? (y/n): "
    if /i not "!CONFIRM!"=="y" (
        echo Aborted by user. No files were changed.
        goto :end
    )
)

:: --- Timestamp for the backup folder (locale-independent, via PowerShell) ---
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "Get-Date -Format 'yyyyMMdd_HHmmss'"`) do set "STAMP=%%I"
if not defined STAMP (
    echo [ERROR] Could not generate a timestamp. Aborting.
    goto :end
)
set "BACKUP_DIR=checkpoints\migration_to_nseall_%STAMP%"

:: --- Step 1: Backup everything this script reads from or writes to ---
echo [1/4] Backing up existing files to %BACKUP_DIR% ...
mkdir "%BACKUP_DIR%" 2>nul
copy /y "dashboard_config.json"        "%BACKUP_DIR%\" >nul || goto :copyerror
copy /y "NSEAll_positions_ledger.json" "%BACKUP_DIR%\" >nul || goto :copyerror
copy /y "NSEAll_tradelog.json"         "%BACKUP_DIR%\" >nul || goto :copyerror
copy /y "NSEAll_equity_history.json"   "%BACKUP_DIR%\" >nul || goto :copyerror
copy /y "N750_positions_ledger.json"   "%BACKUP_DIR%\" >nul || goto :copyerror
copy /y "N750_tradelog.json"           "%BACKUP_DIR%\" >nul || goto :copyerror
copy /y "N750_equity_history.json"     "%BACKUP_DIR%\" >nul || goto :copyerror
echo       done.

:: --- Step 2: Seed NSEAll ledger from N750 (preserves entry_date / entry_price) ---
echo [2/4] Copying open positions: N750 -^> NSEAll ...
copy /y "N750_positions_ledger.json" "NSEAll_positions_ledger.json" >nul || goto :copyerror
echo       done.

:: --- Step 3: Carry trade history and equity curve forward for continuous P&L ---
echo [3/4] Carrying trade history and equity curve forward ...
copy /y "N750_tradelog.json"       "NSEAll_tradelog.json" >nul || goto :copyerror
copy /y "N750_equity_history.json" "NSEAll_equity_history.json" >nul || goto :copyerror
echo       done.

:: --- Step 4: Flip the dashboard's active universe ---
echo [4/4] Switching active universe to NSEAll in dashboard_config.json ...
python -c "import json; p='dashboard_config.json'; d=json.load(open(p)); d['file']='NSEAll_updated.xlsx'; json.dump(d, open(p, 'w'), indent=2)"
if errorlevel 1 (
    echo [ERROR] Failed to update dashboard_config.json. Restore it from %BACKUP_DIR% if needed.
    goto :end
)
echo       done.

echo.
echo ============================================================
echo  Migration complete.
echo ============================================================
echo   - NSEAll_positions_ledger.json now holds your 20 live N750 positions,
echo     original entry dates/prices preserved (28-day hold lock intact).
echo   - NSEAll_tradelog.json now carries the full N750 trade history forward.
echo   - NSEAll_equity_history.json now carries the N750 equity curve forward
echo     (the 2 earlier NSEAll dry-run stub rows were overwritten).
echo   - dashboard_config.json now points at NSEAll_updated.xlsx.
echo.
echo   N750 source files were NOT modified, only read.
echo   Backup of everything overwritten: %BACKUP_DIR%
echo.
echo   Going forward, run rebalances against NSEAll, not N750:
echo       python Sharpe.py NSEAll
echo.
echo   To roll back: copy the files out of
echo   %BACKUP_DIR% back over their current versions.
echo ============================================================
goto :end

:copyerror
echo.
echo [ERROR] A file copy failed ^(target may be open in Excel/another program^).
echo         No further steps were taken. Check %BACKUP_DIR% for backups
echo         of anything already copied, and close any programs that may
echo         have these files open before re-running.

:end
endlocal
pause
