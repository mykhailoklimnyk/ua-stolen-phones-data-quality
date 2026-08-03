@echo off
REM Daily run on Windows until the pipeline moves to the Fedora host.
REM Sequence matters: fold, normalise, check, then publish. A blocking quality
REM failure stops before the report is written — a stale report beats a wrong one.
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
set PY=.venv\Scripts\python.exe

echo [%date% %time%] fold newest snapshots
%PY% -m wantedmt.cli --db data/wantedmt.duckdb daily --source npu --recent 3 || exit /b 1

echo [%date% %time%] normalise
%PY% -m wantedmt.cli --db data/wantedmt.duckdb normalize || exit /b 1

echo [%date% %time%] data quality
%PY% -m wantedmt.cli --db data/wantedmt.duckdb dq --out dq/reports || exit /b 1

echo [%date% %time%] export
%PY% -m wantedmt.cli --db data/wantedmt.duckdb export --out data/export

echo [%date% %time%] done
