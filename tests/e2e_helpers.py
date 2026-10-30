"""画面の試験の小道具：本体を --no-window で起動して、Playwright の Chromium で開く"""
import contextlib
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@contextlib.contextmanager
def running_app(folder, app_path=None, env=None):
    app_path = Path(app_path or ROOT / "src" / "daiwari_kobo.py")
    e = dict(os.environ)
    e.update(env or {})
    e.setdefault("DAIWARI_APPDATA", str(Path(folder).parent / "appdata"))
    proc = subprocess.Popen([sys.executable, str(app_path), str(folder), "--no-window"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=e)
    url = None
    t0 = time.time()
    while time.time() - t0 < 30:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                raise RuntimeError("起動できません:\n" + proc.stderr.read())
            continue
        if line.startswith("http://127.0.0.1:"):
            url = line.strip()
            break
    if not url:
        proc.kill()
        raise RuntimeError("URL が出ません")
    try:
        yield url, proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def launch_browser(p):
    return p.chromium.launch(args=["--lang=ja-JP", "--font-render-hinting=none"])


def wait_ready(page, n_cards=1, timeout=20000):
    page.wait_for_function(f"document.querySelectorAll('#board .card').length >= {n_cards}", timeout=timeout)
