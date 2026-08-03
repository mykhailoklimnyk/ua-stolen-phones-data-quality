@echo off
REM Every 15 minutes: fold a new publication if one appeared, and rebuild the
REM report only then. The source updates more than once a day and the portal
REM keeps one file per day, so a revision replaced before we fetch it is gone.
REM
REM `daily` exits 3 when there was nothing new, 0 when it folded something.
REM That is what decides whether the rest of the chain runs — rebuilding the
REM report 96 times a day on unchanged data would say nothing and cost plenty.
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
set PY=.venv\Scripts\python.exe
set DB=data/wantedmt.duckdb

%PY% -m wantedmt.cli --db %DB% daily --source npu --recent 3
if errorlevel 3 (
  echo [%date% %time%] no new publication
  exit /b 0
)
if errorlevel 1 exit /b 1

echo [%date% %time%] new data folded, refreshing
%PY% -m wantedmt.cli --db %DB% normalize || exit /b 1
%PY% -m wantedmt.cli --db %DB% dq --out dq/reports || exit /b 1
%PY% -m wantedmt.cli --db %DB% export --out data/export
echo [%date% %time%] done
