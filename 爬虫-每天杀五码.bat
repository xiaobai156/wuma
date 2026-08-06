@echo off
chcp 65001 >nul
set "PY_CMD="
py -3 --version >nul 2>nul
if not errorlevel 1 set "PY_CMD=py -3"
if not defined PY_CMD (
  python --version >nul 2>nul
  if not errorlevel 1 set "PY_CMD=python"
)
if not defined PY_CMD (
  echo Cannot find Python. Please install Python and add it to PATH.
  pause
  exit /b 1
)
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  %PY_CMD% run_crawler_prompt.py
) else (
  %PY_CMD% run_crawler_prompt.py
)
set "RUN_CODE=%ERRORLEVEL%"
echo.
pause
exit /b %RUN_CODE%
