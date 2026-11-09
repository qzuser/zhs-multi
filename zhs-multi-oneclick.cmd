@echo off
rem ============================================================
rem  zhs-multi one-click launcher  (ASCII only: cmd.exe parses
rem  this file in the OEM codepage, Chinese text here breaks it)
rem
rem  The pretty Chinese menu lives in <one-click>.exe next to this
rem  file. This .cmd is the fallback that always works.
rem
rem  Double-click  = menu
rem  With args     = pass through:  zhs-multi-oneclick.cmd status
rem ============================================================
setlocal
chcp 65001 >nul 2>&1
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
pushd "%~dp0"

if not exist "app\__main__.py" (
  echo [x] app\__main__.py not found.
  echo     Keep this file in the project root ^(next to app\^).
  pause
  popd
  exit /b 2
)

set "PY=%~dp0..\.venv-zhs\Scripts\python.exe"
if not exist "%PY%" (
  where py >nul 2>&1
  if not errorlevel 1 (
    set "PY=py -3"
  ) else (
    set "PY=python"
  )
  echo [i] venv not found, falling back to: %PY%
  echo     if it complains about missing zhs: pip install -e "..\zhs"
)

if not "%~1"=="" goto passthru

:menu
echo.
echo ================================================================
echo   zhs-multi  one-click
echo ----------------------------------------------------------------
echo   [1] brush now  (auto QR login, then pick course)
echo   [2] unattended daemon  (1 thread, auto-solve captcha)
echo   [3] status  (login / remaining / last round)     [default]
echo   [4] QR login
echo   [5] doctor  (deps / network / login)
echo   [6] dry-run only  (list tasks, change nothing)
echo   [7] solve captcha now
echo   [0] exit
echo ----------------------------------------------------------------
echo   tip: zhs-multi-oneclick.cmd run -c 1 -j 1   works too
echo ================================================================
choice /c 12345670 /n /t 10 /d 3 /m "  pick (10s timeout -> 3): "
set "P=%ERRORLEVEL%"

if "%P%"=="8" goto bye
set "ARGS="
if "%P%"=="2" set "ARGS=daemon -c 1 -j 1 --solve-captcha --every 120 --hours 6"
if "%P%"=="3" set "ARGS=status"
if "%P%"=="4" set "ARGS=login --terminal-qr"
if "%P%"=="5" set "ARGS=doctor"
if "%P%"=="6" set "ARGS=run -c all --dry-run"
if "%P%"=="7" set "ARGS=captcha"

"%PY%" -m app %ARGS%
set "RC=%ERRORLEVEL%"
echo.
echo   exit code %RC%  ^(0=ok 2=no task/no login 3=platform stop 1=usage^)
pause
popd
exit /b %RC%

:passthru
"%PY%" -m app %*
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%

:bye
popd
exit /b 0
