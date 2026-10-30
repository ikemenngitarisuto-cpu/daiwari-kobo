#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""取扱説明書（PDF）を作る：docs/manual.html と docs/img/ → docs/取扱説明書.pdf"""
from __future__ import annotations

import datetime as dt
import re
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def main():
    ver = re.search(r'^VERSION = "([^"]+)"', (ROOT / "src" / "daiwari_kobo.py").read_text(encoding="utf-8"), re.M).group(1)
    shutil.copy2(ROOT / "assets" / "icon.png", DOCS / "img" / "icon.png")
    today = dt.date.today()
    html = (DOCS / "manual.html").read_text(encoding="utf-8")
    html = html.replace("{{VERSION}}", ver).replace("{{DATE}}", f"{today.year}年{today.month}月")
    tmp = DOCS / "_manual_render.html"
    tmp.write_text(html, encoding="utf-8")
    out = DOCS / "取扱説明書.pdf"
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page()
            page.goto(tmp.as_uri())
            page.wait_for_load_state("networkidle")
            page.pdf(path=str(out), format="A4", print_background=True, prefer_css_page_size=True)
            b.close()
    finally:
        tmp.unlink(missing_ok=True)
    print(out, f"{out.stat().st_size:,d} bytes")


if __name__ == "__main__":
    main()
