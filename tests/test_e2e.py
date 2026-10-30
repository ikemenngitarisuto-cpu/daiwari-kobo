"""画面の試験（Playwright の Chromium で、本体を本当に動かして確かめる）"""
import os
import shutil
import time
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")

from e2e_helpers import launch_browser, running_app, wait_ready  # noqa: E402

APP = os.environ.get("DAIWARI_APP")   # dist の .pyw で試すときに指定


@pytest.fixture(scope="module")
def ui(tmp_path_factory):
    import make_sample
    base = tmp_path_factory.mktemp("e2e")
    folder = base / "作品"
    make_sample.make(folder, 24, v2=True, size=(240, 351))
    errors = []
    with running_app(folder, app_path=APP) as (url, proc):
        with pw.sync_playwright() as p:
            b = launch_browser(p)
            page = b.new_page(viewport={"width": 1400, "height": 900}, bypass_csp=True)   # 試験の待ち合わせ用
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.goto(url)
            wait_ready(page, 20)
            yield page, folder, errors
            b.close()


def state(page):
    return page.evaluate("window.__dk.S.st")


def wait_for(page, js, timeout=8000):
    page.wait_for_function(js, timeout=timeout)


def card(page, n):
    return page.locator("#board .card").nth(n)


def test_board_shows_migrated_pages(ui):
    page, folder, errors = ui
    st = state(page)
    assert len(st["pages"]) == 24
    assert page.title().endswith("台割工房")
    assert page.locator("#notices .notice").count() >= 2
    assert not errors


def test_status_key_and_undo(ui):
    page, _, _ = ui
    card(page, 2).click()
    page.keyboard.press("5")
    wait_for(page, "window.__dk.S.st.pages[2].status === 'fix'")
    page.keyboard.press("Control+z")
    wait_for(page, "window.__dk.S.st.pages[2].status !== 'fix'")


def test_type_lines_and_pagedown_keeps_field(ui):
    page, _, _ = ui
    card(page, 0).click()
    page.fill("#detail textarea[data-field=lines]", "サキ「試験です」")
    page.keyboard.press("PageDown")
    wait_for(page, "window.__dk.S.st.pages[0].lines === 'サキ「試験です」'")
    assert page.evaluate("document.activeElement.dataset.field") == "lines"
    assert page.evaluate("window.__dk.S.primary") == state(page)["pages"][1]["id"]
    page.keyboard.press("Escape")


def test_insert_blank_and_fill_with_picker(ui):
    page, _, _ = ui
    card(page, 4).click()
    page.keyboard.press("i")
    wait_for(page, "window.__dk.S.st.pages[5].kind === 'blank'")
    n = len(state(page)["pages"])
    page.locator("#detail [data-act=fill]").click()
    page.locator(".modal .tile").first.click()
    wait_for(page, f"window.__dk.S.st.pages.length === {n - 1} && window.__dk.S.st.pages[5].kind === 'image'")


def test_context_menu_quarantine_and_toast_undo(ui):
    page, folder, _ = ui
    target = state(page)["pages"][7]
    name = target["raw"]
    card(page, 7).click(button="right")
    page.hover("#menu .mi >> text=外す")
    page.click(".menu.sub .mi >> text=別フォルダへ隔離")
    wait_for(page, f"!window.__dk.S.st.pages.some(p => p.raw === {name!r})")
    assert (folder / "未使用" / name).exists()
    page.click(".toast button >> text=元に戻す")
    wait_for(page, f"window.__dk.S.st.pages.some(p => p.raw === {name!r})")
    assert (folder / name).exists()


def test_drag_to_reorder(ui):
    page, _, _ = ui
    before = [p["id"] for p in state(page)["pages"]]
    dst = card(page, 0)
    bb = dst.bounding_box()
    card(page, 3).drag_to(dst, target_position={"x": 6, "y": bb["height"] / 2})
    wait_for(page, f"window.__dk.S.st.pages[0].id === {before[3]!r}")
    page.keyboard.press("Control+z")
    wait_for(page, f"window.__dk.S.st.pages[0].id === {before[0]!r}")


def test_multi_select_move_to_end(ui):
    page, _, _ = ui
    ids = [p["id"] for p in state(page)["pages"]]
    card(page, 1).click()
    card(page, 2).click(modifiers=["Shift"])
    assert page.locator("#detail .d-pno").inner_text().startswith("2")
    page.click("#detail [data-move=end]")
    wait_for(page, f"window.__dk.S.st.pages.slice(-2).map(p => p.id).join() === {','.join(ids[1:3])!r}")
    page.keyboard.press("Control+z")
    wait_for(page, f"window.__dk.S.st.pages[1].id === {ids[1]!r}")


def test_reader_modes_and_keys(ui):
    page, _, _ = ui
    card(page, 2).click()
    page.keyboard.press("Enter")
    page.wait_for_selector("#reader:not([hidden]) .r-pane img")
    page.keyboard.press("ArrowRight")
    wait_for(page, "window.__dk.S.primary === window.__dk.S.st.pages[3].id")
    page.click("#r-mode button[data-m=spread]")
    page.keyboard.press("ArrowLeft")        # 見開きでは ← が次
    wait_for(page, "window.__dk.S.st.pages.findIndex(p => p.id === window.__dk.S.primary) >= 4")
    assert page.locator("#r-stage .r-pane").count() in (1, 2)
    page.click("#r-mode button[data-m=compare]")
    page.keyboard.press("Escape")
    assert page.locator("#reader").is_hidden()


def test_search_filter(ui):
    page, _, _ = ui
    page.keyboard.press("/")
    page.keyboard.type("待ってってば")
    wait_for(page, "window.__dk.S.match && window.__dk.S.match.size >= 1")
    assert page.locator("#board .card.dim").count() > 0
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")
    wait_for(page, "window.__dk.S.match === null")


def test_memo_split_notice(ui):
    page, _, _ = ui
    page.click("#notices [data-id=memo_split] [data-act=accept]")
    wait_for(page, "!window.__dk.S.st.notices.some(n => n.id === 'memo_split')")


def test_live_folder_changes(ui):
    page, folder, _ = ui
    shutil.copy(folder / "012.png", folder / "台詞入り" / "012.png")
    shutil.copy(folder / "_除外" / "ボツ2.png", folder / "届いた絵.png")
    wait_for(page, "window.__dk.S.st.pages.some(p => p.raw === '届いた絵.png' && p.new)", timeout=15000)
    wait_for(page, "window.__dk.S.st.pages.some(p => p.raw === '012.png' && p.fin === '台詞入り/012.png')", timeout=15000)


def test_settings_and_export(ui):
    page, folder, _ = ui
    page.click("#btn-settings")
    page.click(".m-tabs button[data-tab=backup]")
    page.wait_for_selector("#bk")
    page.keyboard.press("Escape")
    page.click("#btn-export")
    page.click(".m-foot [data-go]")
    page.wait_for_selector(".m-foot [data-go] >> text=フォルダを開く", timeout=20000)
    outs = list((folder / "書き出し").iterdir())
    assert outs and (outs[0] / "台本.txt").exists()
    page.keyboard.press("Escape")


def test_no_console_errors(ui):
    _, _, errors = ui
    assert not errors, errors
