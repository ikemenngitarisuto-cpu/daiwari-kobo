#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配布物を作る：dist/台割工房/（本体1ファイル・setup.bat・アイコン・説明書）と dist/台割工房.zip

  python build.py            # 説明書（PDF）があれば一緒に入れる
  python build.py --manual   # 説明書の PDF も作り直す（Playwright が要る）
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
DIST = ROOT / "dist"
APP = "台割工房"
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
    out = DIST / APP
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / f"{APP}.pyw").write_text(bundle(), encoding="utf-8", newline="\n")
    (out / "setup.bat").write_bytes(ascii_crlf(SETUP_BAT))
    (out / "uninstall.bat").write_bytes(ascii_crlf(UNINSTALL_BAT))
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
    zpath = DIST / f"{APP}.zip"
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
