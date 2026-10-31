#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配布物を作る：リポジトリ直下の「台割工房」フォルダ（そのまま使える形）と release/台割工房.zip

  python build.py            # 説明書（PDF）があれば一緒に入れる
  python build.py --manual   # 説明書の PDF も作り直す（Playwright が要る）

「台割工房」フォルダごとリポジトリに入れておくので、git clone したフォルダの setup.bat を1回、
あとは update.bat（git pull）で最新になります。
"""
from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src" / "daiwari_kobo.py"
UI = ROOT / "src" / "ui"
APP = "台割工房"
OUT = ROOT / APP                # そのまま使える形（リポジトリに入れる）
RELEASE = ROOT / "release"      # git を使わない人向けの ZIP
REPO_URL = "https://github.com/ikemenngitarisuto-cpu/daiwari-kobo.git"
UI_NAMES = ("index.html", "style.css", "app.js", "icon.svg")
MARK = re.compile(r"^UI_FILES = None  # @@UI_BUNDLE@@.*$", re.M)

SETUP_BAT = r"""@echo off
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
"""

UNINSTALL_BAT = r"""@echo off
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
"""


UPDATE_BAT = r"""@echo off
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
  echo        git clone @REPO_URL@
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
""".replace("@REPO_URL@", REPO_URL)


def ascii_crlf(text: str) -> bytes:
    data = text.replace("\r\n", "\n").replace("\n", "\r\n").encode("ascii")   # 日本語が入っていたら止まる
    return data


def bundle() -> str:
    src = SRC.read_text(encoding="utf-8")
    files = {n: (UI / n).read_text(encoding="utf-8") for n in UI_NAMES}
    body = "UI_FILES = {\n" + "".join(f"    {n!r}: {files[n]!r},\n" for n in UI_NAMES) + "}"
    if not MARK.search(src):
        raise SystemExit("本体に埋め込みの印（UI_FILES = None  # @@UI_BUNDLE@@）がありません")
    out = MARK.sub(lambda m: body, src, count=1)
    head = "# このファイルは build.py が src/daiwari_kobo.py と src/ui/ から作りました。直すときは元のほうを。\n"
    lines = out.split("\n", 2)
    out = "\n".join(lines[:2]) + "\n" + head + lines[2]
    ast.parse(out)
    return out


def version() -> str:
    m = re.search(r'^VERSION = "([^"]+)"', SRC.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else "?"


def build(make_manual=False):
    ver = version()
    out = OUT
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / f"{APP}.pyw").write_text(bundle(), encoding="utf-8", newline="\n")
    (out / "setup.bat").write_bytes(ascii_crlf(SETUP_BAT))
    (out / "uninstall.bat").write_bytes(ascii_crlf(UNINSTALL_BAT))
    (out / "update.bat").write_bytes(ascii_crlf(UPDATE_BAT))
    icon = ROOT / "assets" / f"{APP}.ico"
    if not icon.exists():
        subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")], check=True)
    shutil.copy2(icon, out / f"{APP}.ico")
    readme = (ROOT / "docs" / "はじめにお読みください.txt").read_text(encoding="utf-8").replace("{VERSION}", ver)
    (out / "はじめにお読みください.txt").write_bytes(("\ufeff" + readme).replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
    if make_manual:
        subprocess.run([sys.executable, str(ROOT / "tools" / "make_manual.py")], check=True)
    pdf = ROOT / "docs" / "取扱説明書.pdf"
    if pdf.exists():
        shutil.copy2(pdf, out / "取扱説明書.pdf")
    else:
        print("！ docs/取扱説明書.pdf がまだ無いので、入れずに作ります（python build.py --manual）")
    RELEASE.mkdir(exist_ok=True)
    zpath = RELEASE / f"{APP}.zip"
    zpath.unlink(missing_ok=True)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(out.rglob("*")):
            if f.is_file():
                z.write(f, f"{APP}/{f.relative_to(out).as_posix()}")
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            if not info.filename.isascii():
                assert info.flag_bits & 0x800, f"UTF-8 の印がありません: {info.filename}"
    print(f"{APP} {ver} を作りました: {zpath}")
    for f in sorted(out.iterdir()):
        print(f"  {f.name:28s} {f.stat().st_size:>10,d} bytes")
    return zpath


if __name__ == "__main__":
    build(make_manual="--manual" in sys.argv)
