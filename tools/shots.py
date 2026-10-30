#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""取扱説明書の画面写真を撮る（試験用の架空の絵で）

  python tools/shots.py docs/img
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

import make_sample  # noqa: E402
from e2e_helpers import launch_browser, running_app, wait_ready  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

VIEW = {"width": 1440, "height": 900}
SCENES = ["教室・朝", "屋上・放課後", "駅のホーム", "夜の公園", "商店街", "海辺", "部室", "雨の交差点"]


def api(page, op, args):
    return page.evaluate("([op, args]) => window.__dk.api(op, args).then(d => d.toast || '')", [op, args])


def shot(page, out: Path, name, clip=None):
    page.evaluate("document.querySelector('#toasts').replaceChildren()")
    page.wait_for_timeout(450)
    kw = {"path": str(out / f"{name}.jpg"), "type": "jpeg", "quality": 88}
    if clip:
        kw["clip"] = clip
    page.screenshot(**kw)
    print("  撮りました:", name)


def prepare(page):
    st = page.evaluate("window.__dk.S.st")
    pages = st["pages"]
    api(page, "title", {"value": "放課後の屋上"})
    plan = ["done"] * 9 + ["text"] * 6 + ["fix"] + ["art"] * 5 + ["todo"] * 30
    for p, s in zip(pages, plan):
        if p["status"] != s:
            api(page, "status", {"ids": [p["id"]], "status": s})
    for i, p in enumerate(pages[:22]):
        lines = make_sample.LINES
        a, b = lines[(i * 3) % len(lines)], lines[(i * 3 + 1) % len(lines)]
        api(page, "text", {"id": p["id"], "field": "lines", "value": f"{a[0]}「{a[1]}」\n{b[0]}「{b[1]}」"})
        if i % 4 == 0:
            api(page, "text", {"id": p["id"], "field": "scene", "value": SCENES[(i // 4) % len(SCENES)]})
        if i % 5 == 2:
            api(page, "text", {"id": p["id"], "field": "memo", "value": make_sample.NOTES[i % len(make_sample.NOTES)]})
    api(page, "text", {"id": pages[15]["id"], "field": "fix", "value": "右手の指が6本。生成し直す"})
    api(page, "blank", {"ids": [pages[18]["id"]], "where": "after", "wait": True})
    api(page, "blank", {"ids": [pages[24]["id"]], "where": "after"})


def main(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp())
    folder = tmp / "放課後の屋上"
    make_sample.make(folder, 40, v2=False, size=(560, 819))
    for d in ("_除外",):
        shutil.rmtree(folder / d, ignore_errors=True)
    with running_app(folder) as (url, proc):
        with sync_playwright() as p:
            b = launch_browser(p)
            page = b.new_page(viewport=VIEW, bypass_csp=True)
            page.goto(url)
            wait_ready(page, 40)
            prepare(page)
            page.wait_for_timeout(800)
            # 空白と待ち受け（絵が来る前に撮る）
            wid = page.evaluate("window.__dk.S.st.pages.find(p => p.wait).id")
            page.evaluate("id => window.__dk.cards.get(id).el.scrollIntoView({block: 'center'})", wid)
            page.locator(f"#board .card[data-id={wid}]").click()
            shot(page, out, "04_blank")
            # 新しく来た絵（待ち受けの空白へ入る）
            shutil.copy(folder / "未使用" / "候補1.png", folder / "041.png")
            page.wait_for_function("window.__dk.S.st.counts.new >= 1", timeout=15000)
            page.wait_for_timeout(1500)
            # 1. 台割の画面
            page.locator("#board .card").nth(3).click()
            page.evaluate("document.querySelector('#board-wrap').scrollTop = 0")
            shot(page, out, "01_board")
            # 2. 右クリック
            page.locator("#board .card").nth(7).click(button="right")
            page.hover("#menu .mi >> text=外す")
            shot(page, out, "02_menu")
            page.keyboard.press("Escape")
            # 3. 複数選択
            page.locator("#board .card").nth(9).click()
            page.locator("#board .card").nth(12).click(modifiers=["Shift"])
            shot(page, out, "03_multi")
            # 5. 読む
            page.locator("#board .card").nth(3).click()
            page.keyboard.press("Enter")
            page.wait_for_selector("#reader .r-pane img")
            shot(page, out, "05_read")
            page.click("#r-mode button[data-m=spread]")
            shot(page, out, "06_read_spread")
            page.click("#r-mode button[data-m=compare]")
            shot(page, out, "07_read_compare")
            page.keyboard.press("Escape")
            # 6. 見開きの一覧
            page.click("#spread")
            page.wait_for_timeout(600)
            page.evaluate("document.querySelector('#board-wrap').scrollTop = 0")
            shot(page, out, "08_board_spread")
            page.click("#spread")
            # 7. 設定（隔離した絵）
            page.locator("#board .card").nth(30).click()
            page.keyboard.press("Shift+X")
            page.wait_for_timeout(600)
            page.click("#btn-settings")
            page.click(".m-tabs button[data-tab=quar]")
            shot(page, out, "09_settings_quar")
            page.keyboard.press("Escape")
            # 8. 書き出し
            page.click("#btn-export")
            page.fill("#ex-prefix", "放課後の屋上_")
            shot(page, out, "10_export")
            page.keyboard.press("Escape")
            # 9. 似た絵の確認（名前も絵も変えた）
            from PIL import Image
            Image.open(folder / "023.png").convert("RGB").save(folder / "描き直し_023.jpg", quality=90)
            (folder / "023.png").unlink()
            page.wait_for_function("window.__dk.S.st.suggestions.length >= 1", timeout=20000)
            page.wait_for_timeout(600)
            shot(page, out, "11_suggest", clip={"x": 0, "y": 0, "width": VIEW["width"], "height": 330})
            # 10. キー操作
            page.keyboard.press("?")
            shot(page, out, "12_help")
            page.keyboard.press("Escape")
            b.close()
    # 引っ越しのお知らせ（v2 のデータつきフォルダ）
    folder2 = tmp / "以前の作品"
    make_sample.make(folder2, 24, v2=True, size=(400, 585))
    with running_app(folder2) as (url, proc):
        with sync_playwright() as p:
            b = launch_browser(p)
            page = b.new_page(viewport=VIEW, bypass_csp=True)
            page.goto(url)
            wait_ready(page, 20)
            page.wait_for_timeout(1500)
            shot(page, out, "13_migrate", clip={"x": 0, "y": 0, "width": VIEW["width"], "height": 520})
            b.close()
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "img")
