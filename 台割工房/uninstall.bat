@echo off
setlocal
cd /d "%~dp0"
title Daiwari Kobo uninstall
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
  pause
  exit /b 1
)
set "APP="
for %%F in ("%~dp0*.pyw") do set "APP=%%~fF"
if not defined APP (
  echo  [!] The app file - .pyw - was not found next to uninstall.bat.
  pause
  exit /b 1
)
echo  This removes the file association, the SendTo entry and the desktop shortcut.
echo  Your folders, their .daiwari files and backups are kept.
echo.
choice /m "  Continue"
if errorlevel 2 exit /b 0
%PY% "%APP%" --uninstall
echo.
pause
