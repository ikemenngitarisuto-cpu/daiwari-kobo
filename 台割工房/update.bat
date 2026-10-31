@echo off
setlocal
cd /d "%~dp0"
title Daiwari Kobo update
echo.
echo  ==========================================
echo    Daiwari Kobo  update
echo  ==========================================
echo.
where git >nul 2>nul
if errorlevel 1 (
  echo  [!] Git was not found on this PC.
  echo      Install Git for Windows once, for example:
  echo        winget install --id Git.Git -e
  echo      Or use GitHub Desktop and press "Pull origin".
  echo.
  pause
  exit /b 1
)
git rev-parse --is-inside-work-tree >nul 2>nul
if errorlevel 1 (
  echo  [!] This folder was not downloaded with git, so it cannot update itself.
  echo      Get it once with:
  echo        git clone https://github.com/ikemenngitarisuto-cpu/daiwari-kobo.git
  echo      or download the ZIP from GitHub again.
  echo.
  pause
  exit /b 1
)
for /f "delims=" %%H in ('git rev-parse HEAD') do set "OLD=%%H"
echo  Downloading the latest version ...
git pull --ff-only
if errorlevel 1 (
  echo.
  echo  [!] The update stopped. Files in this folder may have been edited by hand.
  echo      Nothing was changed. Download the ZIP from GitHub again if this keeps happening.
  echo.
  pause
  exit /b 1
)
for /f "delims=" %%H in ('git rev-parse HEAD') do set "NEW=%%H"
echo.
if "%OLD%"=="%NEW%" (
  echo  Already up to date.
) else (
  echo  Updated.
  echo  Close every Daiwari Kobo window, wait a few seconds, then open your folder again.
)
call :showver
echo.
pause
exit /b 0

:showver
set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  where python >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY exit /b 0
for %%F in ("%~dp0*.pyw") do %PY% "%%~fF" --version
exit /b 0
