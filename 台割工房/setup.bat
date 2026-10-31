@echo off
setlocal
cd /d "%~dp0"
title Daiwari Kobo setup
echo.
echo  ==========================================
echo    Daiwari Kobo  setup
echo  ==========================================
echo.
set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  where python >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY if exist "C:\Python314\python.exe" set "PY=C:\Python314\python.exe"
if not defined PY (
  echo  [!] Python was not found.
  echo      Install Python 3.10 or later from https://www.python.org/
  echo      and turn on "Add python.exe to PATH" in the installer.
  echo.
  pause
  exit /b 1
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
  echo  [!] Python 3.10 or later is required.
  echo.
  pause
  exit /b 1
)
%PY% -c "import PIL" >nul 2>nul
if errorlevel 1 (
  echo  Installing Pillow ...
  %PY% -m pip install --upgrade pillow
  if errorlevel 1 (
    echo  [!] Pillow could not be installed. Check the internet connection and try again.
    echo.
    pause
    exit /b 1
  )
)
set "APP="
for %%F in ("%~dp0*.pyw") do set "APP=%%~fF"
if not defined APP (
  echo  [!] The app file - .pyw - was not found next to setup.bat.
  echo.
  pause
  exit /b 1
)
%PY% "%APP%" --setup
echo.
pause
