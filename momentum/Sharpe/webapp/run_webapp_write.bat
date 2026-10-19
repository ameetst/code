@echo off
rem Starts the webapp WITH WRITES ENABLED (SHARPE_READ_ONLY=0): trade add/edit/delete, cash
rem ledger entries, config saves, and the daily regime-history record all write to the live
rem data files. Use only one UI for writes at a time -- close the Streamlit dashboard
rem tabs first (there is no cross-process lock). For a safe look-only session use
rem run_webapp.bat instead.
rem To rehearse against a copy of the data first:  set SHARPE_DATA_DIR=C:\path\to\copy
set SHARPE_READ_ONLY=0
echo *** WRITE MODE: changes are saved to the live data files ***
call "%~dp0run_webapp.bat"
