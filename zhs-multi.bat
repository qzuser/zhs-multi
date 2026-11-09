@echo off
rem ==========================================================
rem  zhs-multi launcher
rem  Runs "python -m app" with the venv next to this folder.
rem  NOTE: keep this file ASCII-only, cmd.exe cannot parse
rem        UTF-8 Chinese comments (GBK codepage).
rem ==========================================================
setlocal
chcp 65001 >nul 2>&1
set PYTHONIOENCODING=utf-8
pushd "%~dp0"

set "PY=%~dp0..\.venv-zhs\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

if "%PY%"=="python" (
  echo [zhs-multi] ..\.venv-zhs not found, falling back to system python
  echo [zhs-multi] if it complains about missing zhs: pip install -e "..\zhs"
)

"%PY%" -m app %*

set "RC=%ERRORLEVEL%"
popd
exit /b %RC%
