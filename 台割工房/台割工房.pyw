#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# このファイルは build.py が src/daiwari_kobo.py と src/ui/ から作りました。直すときは元のほうを。
"""台割工房 v3 — 生成画像マンガの台割（ページの順番・進行・セリフ）を管理する

使い方
  画像フォルダの「台割.daiwari」をダブルクリックすると、専用ウィンドウで開きます。
  初めてのフォルダは、右クリック →「送る」→「台割工房」。
  デスクトップの「台割工房」にフォルダを落としても開けます。

作り
  ファイル操作はこの Python が受け持ち、画面は Edge のアプリ窓に出します。
  画面（HTML/CSS/JS）もこのファイルの中に入っています（build.py が埋め込みます）。
  自分のPCの中（127.0.0.1）でだけ待ち受け、起動のたびに合言葉（トークン）を変えます。
  画像ファイルを消す機能は持ちません。
"""
from __future__ import annotations

import base64
import copy
import ctypes
import datetime as dt
import hashlib
import io
import json
import logging
import logging.handlers
import os
import re
import secrets
import shutil
import struct
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_NAME = "台割工房"
APP_ID = "DaiwariKobo"
VERSION = "3.0.1"
PROJECT_FILE = "台割.daiwari"     # 各画像フォルダに置く入口（中身は JSON）
DATA_DIR = ".daiwari"            # サムネイル・バックアップ・記録の置き場
LEGACY_FILE = ".daiwari.json"    # 現行版（v2）の管理データ
LEGACY_QDIR = "_除外"            # 現行版（v2）の隔離フォルダ
IS_WIN = os.name == "nt"

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}

STATUSES = (("todo", "未着手"), ("art", "画像のみ"), ("text", "セリフ入"),
            ("done", "完成"), ("fix", "要修正"))
STATUS_IDS = tuple(k for k, _ in STATUSES)
STATUS_LABEL = dict(STATUSES)
TEXT_FIELDS = ("scene", "lines", "memo", "fix")
FIELD_LABEL = {"scene": "シーン名", "lines": "セリフ", "memo": "メモ", "fix": "修正指示"}
DEFAULT_SUFFIXES = ("_s", "_serif", "_fin", "_t", "_text", "_done",
                    "-s", "-serif", "-fin", "_セリフ", "_台詞", "_完")

DEFAULT_SETTINGS = {
    "spread": False,          # 見開きで並べる（確認用・右綴じ）
    "cover_single": True,     # 見開きのとき1ページ目を単独にする
    "thumb_size": 168,        # 一覧のサムネイルの幅
    "view": "fin",            # 一覧で見せる絵（fin=完成を優先 / raw=原画を優先）
    "fin_dir": "台詞入り",    # 台詞工房のフォルダ作業の保存先
    "quarantine_dir": "未使用",  # 隔離フォルダ（番付工房と同じ）
    "fin_suffixes": list(DEFAULT_SUFFIXES),
    "use_vsave": True,        # 名前_V2 / _V3 を完成として拾う
    "auto_pair": True,        # 完成を自動でページに組み込む
    "auto_status": True,      # 完成が来たら進行を「セリフ入」にする
    "external_cmd": "",       # 外部アプリ（空ならWindowsの既定のアプリ）
    "chars_warn": 120,        # 1ページの字数の目安（第2段で使う）
    "export_prefix": "",
    "export_pad": 3,
    "export_start": 1,
    "export_use": "fin",      # fin=完成を優先 / raw=原画 / finonly=完成だけ
    "export_blank": "skip",   # skip=番号を飛ばす / close=詰める
    "export_strip": True,     # 書き出す絵からメタデータを消す
    "export_script": True,    # 台本.txt も書き出す
    "export_memo": True,      # 台本にメモと修正指示も入れる
}

POLL_SEC = 1.5            # フォルダを見に行く間隔
QUIET_SEC = 1.2           # 変化が落ち着くまで待つ時間
UNDO_LIMIT = 200          # 元に戻せる手数
BACKUP_KEEP = 20          # 直近のバックアップの数
BACKUP_DAYS = 7           # 日ごとのバックアップの日数
BACKUP_EVERY = 120        # 直近のバックアップを取る最短の間隔（秒）
THUMB_MAX = 512           # サムネイルの長い辺
THUMB_BG = (19, 23, 30)   # 透明部分の下地（画面の机の色）
LOOK_NEAR = 10            # 似た絵とみなす明暗模様の差（64ビット中）
TEXT_MAX = 20000          # 1つの欄に入れられる文字数

UI_FILES = {
    'index.html': '<!doctype html>\n<html lang="ja">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n<meta name="dk-token" content="{{TOKEN}}">\n<meta name="dk-version" content="{{VERSION}}">\n<title>台割工房</title>\n<link rel="icon" href="/icon.svg" type="image/svg+xml">\n<link rel="stylesheet" href="/style.css">\n</head>\n<body>\n<div id="app">\n  <header class="top">\n    <div class="brand"><img src="/icon.svg" alt=""><span>台割工房</span></div>\n    <input id="title" class="title-input" placeholder="作品タイトル" maxlength="200" spellcheck="false" autocomplete="off">\n    <button id="folder" class="folder" title="このフォルダをエクスプローラで開く"></button>\n    <nav class="tabs" id="tabs">\n      <button data-view="board" class="on" title="順番・進行・空白を管理する">台割</button>\n      <button data-view="read" title="原寸で送る（Enter）">読む</button>\n    </nav>\n    <div class="top-right">\n      <div class="totals" id="totals"></div>\n      <button id="undo" class="icon-btn" title="元に戻す（Ctrl+Z）" disabled>\n        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/></svg>\n      </button>\n      <button id="redo" class="icon-btn" title="やり直し（Ctrl+Y）" disabled>\n        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/></svg>\n      </button>\n      <button id="btn-export" class="btn" title="台割の順に、別のフォルダへ連番でコピーする">\n        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></svg>書き出し\n      </button>\n      <button id="btn-settings" class="icon-btn" title="設定・隔離した絵・バックアップ">\n        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>\n      </button>\n      <button id="btn-help" class="icon-btn" title="キー操作の一覧（?）">?</button>\n    </div>\n  </header>\n\n  <div class="strip-wrap">\n    <div id="strip" class="strip" title="進行ストリップ（クリックでそのページへ）"></div>\n    <div id="strip-info" class="strip-info"></div>\n  </div>\n\n  <div id="notices" class="notices"></div>\n\n  <div class="toolbar" id="toolbar">\n    <div class="chips" id="chips"></div>\n    <div class="tool-right">\n      <div class="search">\n        <input id="search" placeholder="検索（/）セリフ・名前・メモ" spellcheck="false" autocomplete="off">\n        <span id="search-count"></span>\n        <button id="search-prev" title="前の一致">▲</button>\n        <button id="search-next" title="次の一致（Enter）">▼</button>\n      </div>\n      <label class="toggle" title="絞り込みに合うページだけを出す"><input type="checkbox" id="only-match">一致だけ</label>\n      <div class="seg" id="view-seg" title="一覧で見せる絵（V で切り替え）"><button data-v="fin">完成</button><button data-v="raw">原画</button></div>\n      <label class="toggle" title="右綴じの見開きで並べる（確認用）"><input type="checkbox" id="spread">見開き</label>\n      <input type="range" id="zoom" min="100" max="360" step="4" title="サムネイルの大きさ（Ctrl+ホイール）">\n    </div>\n  </div>\n\n  <main class="main" id="main">\n    <section id="board-wrap" class="board-wrap">\n      <div id="board" class="board"></div>\n      <div id="drop-mark" class="drop-mark"></div>\n    </section>\n    <aside id="detail" class="detail"></aside>\n  </main>\n</div>\n\n<section id="reader" class="reader" hidden>\n  <div class="r-bar">\n    <button class="btn" id="r-back" title="台割へ戻る（Esc）">← 台割</button>\n    <div class="r-pno" id="r-pno"></div>\n    <div class="r-name" id="r-name"></div>\n    <div class="seg" id="r-which"><button data-v="raw">原画</button><button data-v="fin">完成</button></div>\n    <div class="seg" id="r-mode"><button data-m="single">1枚</button><button data-m="spread">見開き</button><button data-m="compare">見比べ</button></div>\n    <div class="spacer"></div>\n    <div class="statusbar" id="r-status"></div>\n    <button class="btn warn" id="r-exclude" title="台割から外す。ファイルはそのまま（X）">除外</button>\n    <button class="btn danger" id="r-quar" title="「未使用」フォルダへ移す（Shift+X）">隔離</button>\n  </div>\n  <div class="r-stage" id="r-stage"></div>\n  <div class="r-hint" id="r-hint"></div>\n</section>\n\n<div id="menu" class="menu" hidden></div>\n<div id="modal-root"></div>\n<div id="toasts" class="toasts"></div>\n<div id="offline" class="offline" hidden>\n  <div class="box">\n    <b>台割工房の本体とつながりません</b><br>\n    ウィンドウを閉じて、フォルダの「台割.daiwari」を開き直してください。<br>\n    <span style="color:var(--txt-faint);font-size:12px">ここまでの入力は保存されています。</span><br>\n    <button class="btn" id="offline-retry" style="margin-top:10px">もう一度つなぐ</button>\n  </div>\n</div>\n<script src="/app.js"></script>\n</body>\n</html>\n',
    'style.css': '/* 台割工房 — 暗い机の上の台割表。色は進行状況だけが持ち、選んだページだけ紙のように光る */\n:root {\n  --ink-950: #07080b;\n  --ink-900: #0a0c10;\n  --ink-850: #0d1015;\n  --ink-800: #12161d;\n  --ink-750: #171c24;\n  --ink-700: #1d232c;\n  --ink-650: #232a34;\n  --ink-600: #28303b;\n  --ink-500: #38424f;\n  --ink-400: #4a5564;\n  --txt: #e4e8f0;\n  --txt-dim: #8d97a9;\n  --txt-faint: #5a6376;\n  --paper: #efe8d8;\n  --paper-soft: rgba(239, 232, 216, .10);\n  --paper-line: rgba(239, 232, 216, .55);\n  --s-todo: #4d5665;\n  --s-art: #6d8dd6;\n  --s-text: #c6a052;\n  --s-done: #4dab78;\n  --s-fix: #d55f4e;\n  --warn: #c6a052;\n  --danger: #d55f4e;\n  --tw: 168px;\n  --detail-w: 372px;\n  --font: "Yu Gothic UI", "Hiragino Sans", "Noto Sans JP", "Noto Sans CJK JP", "Meiryo", system-ui, sans-serif;\n  --mono: ui-monospace, "Cascadia Mono", "Consolas", "Noto Sans Mono", monospace;\n  color-scheme: dark;\n}\n\n* { box-sizing: border-box; }\nhtml, body { height: 100%; margin: 0; }\nbody {\n  background: var(--ink-900);\n  color: var(--txt);\n  font: 13px/1.5 var(--font);\n  overflow: hidden;\n  -webkit-font-smoothing: antialiased;\n  user-select: none;\n}\nbutton, input, textarea, select { font: inherit; color: inherit; }\nbutton { cursor: pointer; }\ninput[type="text"], input[type="number"], input:not([type]), textarea, select {\n  background: var(--ink-850);\n  border: 1px solid var(--ink-600);\n  border-radius: 6px;\n  padding: 6px 9px;\n  outline: none;\n  user-select: text;\n  transition: border-color .12s, background .12s;\n}\ninput:focus, textarea:focus, select:focus { border-color: var(--paper-line); background: var(--ink-900); }\ntextarea { resize: none; line-height: 1.65; }\n::selection { background: rgba(239, 232, 216, .28); }\n::-webkit-scrollbar { width: 11px; height: 11px; }\n::-webkit-scrollbar-thumb { background: var(--ink-600); border-radius: 8px; border: 3px solid transparent; background-clip: content-box; }\n::-webkit-scrollbar-thumb:hover { background-color: var(--ink-500); }\n::-webkit-scrollbar-track { background: transparent; }\n[hidden] { display: none !important; }\n.mono { font-family: var(--mono); }\n\n#app { height: 100%; display: grid; grid-template-rows: auto auto auto auto 1fr; }\n\n/* ───── 上の帯 ───── */\n.top {\n  display: flex; align-items: center; gap: 12px;\n  height: 52px; padding: 0 14px;\n  background: linear-gradient(var(--ink-850), var(--ink-900));\n  border-bottom: 1px solid var(--ink-700);\n}\n.brand { display: flex; align-items: center; gap: 8px; color: var(--txt-dim); font-weight: 600; letter-spacing: .08em; white-space: nowrap; }\n.brand img { width: 22px; height: 22px; }\n.title-input {\n  flex: 0 1 340px; min-width: 120px;\n  background: transparent !important; border: 1px solid transparent !important;\n  font-size: 16px; font-weight: 600; letter-spacing: .02em; padding: 5px 8px;\n}\n.title-input:hover { border-color: var(--ink-600) !important; }\n.title-input:focus { border-color: var(--paper-line) !important; background: var(--ink-900) !important; }\n.folder {\n  max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;\n  background: none; border: none; color: var(--txt-faint); padding: 4px 6px; border-radius: 6px;\n}\n.folder:hover { color: var(--txt-dim); background: var(--ink-800); }\n.tabs { display: flex; gap: 2px; background: var(--ink-800); padding: 3px; border-radius: 9px; border: 1px solid var(--ink-700); margin-left: 6px; }\n.tabs button { background: none; border: none; padding: 4px 16px; border-radius: 7px; color: var(--txt-dim); font-weight: 600; letter-spacing: .1em; }\n.tabs button.on { background: var(--ink-650); color: var(--txt); box-shadow: inset 0 0 0 1px var(--ink-500); }\n.top-right { margin-left: auto; display: flex; align-items: center; gap: 6px; }\n.totals { color: var(--txt-dim); font-size: 12px; margin-right: 8px; white-space: nowrap; }\n.totals b { color: var(--txt); font-family: var(--mono); font-weight: 600; }\n.totals .pct { color: var(--s-done); }\n.icon-btn, .btn {\n  height: 32px; min-width: 32px; display: inline-flex; align-items: center; justify-content: center; gap: 6px;\n  background: var(--ink-800); border: 1px solid var(--ink-600); border-radius: 7px; color: var(--txt-dim);\n  padding: 0 10px; transition: background .12s, color .12s, border-color .12s;\n}\n.icon-btn { padding: 0; width: 32px; }\n.icon-btn:hover:not(:disabled), .btn:hover:not(:disabled) { background: var(--ink-700); color: var(--txt); border-color: var(--ink-500); }\n.icon-btn:disabled, .btn:disabled { opacity: .35; cursor: default; }\n.icon-btn svg { width: 17px; height: 17px; }\n.btn.primary { background: var(--paper); color: #1a1c20; border-color: var(--paper); font-weight: 700; }\n.btn.primary:hover:not(:disabled) { background: #fff8e8; color: #111; }\n.btn.warn { color: var(--warn); border-color: rgba(198, 160, 82, .45); }\n.btn.danger { color: var(--danger); border-color: rgba(213, 95, 78, .5); }\n.btn.ghost { background: none; border-color: transparent; }\n.btn.small { height: 26px; font-size: 12px; padding: 0 8px; }\n\n/* ───── 進行ストリップ ───── */\n.strip-wrap { display: flex; align-items: center; gap: 12px; padding: 7px 14px 6px; background: var(--ink-900); border-bottom: 1px solid var(--ink-800); }\n.strip { flex: 1; display: flex; align-items: stretch; height: 16px; gap: 1px; cursor: pointer; }\n.strip i {\n  flex: 1 1 0; min-width: 1px; border-radius: 1px; background: var(--s-todo); position: relative;\n  transition: transform .1s;\n}\n.strip i.st-art { background: var(--s-art); }\n.strip i.st-text { background: var(--s-text); }\n.strip i.st-done { background: var(--s-done); }\n.strip i.st-fix { background: var(--s-fix); }\n.strip i.blank { background: transparent; box-shadow: inset 0 0 0 1px var(--ink-500); }\n.strip i.missing { background: repeating-linear-gradient(45deg, var(--ink-500) 0 2px, transparent 2px 4px); }\n.strip i.gap { margin-left: 3px; }\n.strip i.sel { box-shadow: 0 0 0 1px var(--paper); z-index: 1; }\n.strip i.primary { transform: scaleY(1.3); box-shadow: 0 0 0 1px var(--paper), 0 0 8px rgba(239, 232, 216, .6); z-index: 2; }\n.strip i.dim { opacity: .25; }\n.strip-info { color: var(--txt-dim); font-size: 12px; white-space: nowrap; min-width: 160px; text-align: right; }\n.strip-info .busy::before {\n  content: ""; display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px;\n  background: var(--s-text); animation: pulse 1s ease-in-out infinite;\n}\n@keyframes pulse { 50% { opacity: .25; } }\n\n/* ───── お知らせ ───── */\n.notices { display: flex; flex-direction: column; gap: 1px; background: var(--ink-800); }\n.notice {\n  display: flex; align-items: center; gap: 12px; padding: 8px 14px;\n  background: var(--ink-850); border-left: 3px solid var(--s-art); font-size: 12.5px;\n}\n.notice.warn, .notice.ask { border-left-color: var(--warn); }\n.notice.error { border-left-color: var(--danger); }\n.notice .txt { flex: 1; color: var(--txt); }\n.notice .acts { display: flex; gap: 6px; flex-shrink: 0; }\n.notice img { height: 46px; border-radius: 3px; border: 1px solid var(--ink-600); }\n.notice .x { background: none; border: none; color: var(--txt-faint); font-size: 16px; padding: 2px 6px; }\n.notice .x:hover { color: var(--txt); }\n\n/* ───── 道具の列 ───── */\n.toolbar {\n  display: flex; align-items: center; gap: 10px; padding: 8px 14px;\n  border-bottom: 1px solid var(--ink-800); background: var(--ink-900); flex-wrap: wrap;\n}\n.chips { display: flex; gap: 5px; flex-wrap: wrap; }\n.chip {\n  display: inline-flex; align-items: center; gap: 6px; height: 26px; padding: 0 10px;\n  background: var(--ink-800); border: 1px solid var(--ink-700); border-radius: 13px; color: var(--txt-dim); font-size: 12px;\n}\n.chip:hover { border-color: var(--ink-500); color: var(--txt); }\n.chip.on { background: var(--ink-650); border-color: var(--paper-line); color: var(--txt); }\n.chip i { width: 8px; height: 8px; border-radius: 50%; background: var(--txt-faint); }\n.chip i.blank { background: transparent; box-shadow: inset 0 0 0 1.5px var(--txt-dim); }\n.chip i.new { background: var(--paper); }\n.chip i.missing { background: repeating-linear-gradient(45deg, var(--txt-dim) 0 1.5px, transparent 1.5px 3px); }\n.chip i.wait { background: transparent; box-shadow: inset 0 0 0 1.5px var(--paper); }\n.chip b { font-family: var(--mono); font-weight: 500; color: var(--txt); }\n.tool-right { margin-left: auto; display: flex; align-items: center; gap: 10px; }\n.search { display: flex; align-items: center; gap: 2px; background: var(--ink-850); border: 1px solid var(--ink-600); border-radius: 7px; padding-right: 4px; }\n.search:focus-within { border-color: var(--paper-line); }\n.search input { border: none !important; background: transparent !important; width: 180px; padding: 5px 9px; }\n.search span { color: var(--txt-faint); font-size: 11px; font-family: var(--mono); min-width: 34px; text-align: right; }\n.search button { background: none; border: none; color: var(--txt-faint); font-size: 10px; padding: 3px 4px; border-radius: 4px; }\n.search button:hover { color: var(--txt); background: var(--ink-700); }\n.toggle { display: inline-flex; align-items: center; gap: 5px; color: var(--txt-dim); font-size: 12px; cursor: pointer; white-space: nowrap; }\n.toggle input { accent-color: var(--paper); margin: 0; }\n.seg { display: inline-flex; background: var(--ink-850); border: 1px solid var(--ink-600); border-radius: 7px; padding: 2px; }\n.seg button { background: none; border: none; padding: 3px 10px; border-radius: 5px; color: var(--txt-dim); font-size: 12px; }\n.seg button.on { background: var(--ink-650); color: var(--txt); }\n.seg button:disabled { opacity: .35; cursor: default; }\ninput[type="range"] { accent-color: var(--paper); width: 110px; }\n\n/* ───── 本体（一覧と編集欄） ───── */\n.main { display: grid; grid-template-columns: 1fr var(--detail-w); min-height: 0; }\n.board-wrap {\n  position: relative; overflow: auto; min-height: 0;\n  background:\n    radial-gradient(1200px 600px at 30% -10%, rgba(239, 232, 216, .035), transparent 60%),\n    var(--ink-900);\n}\n.board {\n  display: grid; grid-template-columns: repeat(auto-fill, minmax(var(--tw), 1fr)); gap: 14px 12px;\n  padding: 18px 18px 120px; align-content: start; justify-content: start;\n}\n.board:not(.spread) > .card { width: auto; }\n.board.spread { grid-template-columns: repeat(auto-fill, calc(var(--tw) * 2 + 2px)); direction: rtl; gap: 18px 22px; }\n.spr { display: flex; gap: 2px; direction: rtl; }\n.spr > .card { direction: ltr; }\n.spr.single { justify-content: flex-start; }\n\n.card {\n  position: relative; width: var(--tw);\n  background: var(--ink-800); border: 1px solid var(--ink-700); border-radius: 7px;\n  overflow: hidden; cursor: default;\n  content-visibility: auto; contain-intrinsic-size: var(--tw) calc(var(--tw) * 1.4 + 46px);\n  transition: border-color .12s, box-shadow .15s, opacity .15s, transform .12s;\n}\n.card:hover { border-color: var(--ink-500); }\n.card .thumb {\n  position: relative; aspect-ratio: 5 / 7; background: var(--ink-950);\n  display: flex; align-items: center; justify-content: center; overflow: hidden;\n}\n.card .thumb img { width: 100%; height: 100%; object-fit: contain; display: block; pointer-events: none; }\n.card .thumb img.broken { display: none; }\n.badges { position: absolute; left: 0; right: 0; top: 0; display: flex; justify-content: space-between; align-items: flex-start; padding: 5px; pointer-events: none; }\n.pno {\n  font-family: var(--mono); font-size: 11px; font-weight: 600; letter-spacing: .02em;\n  background: rgba(7, 8, 11, .78); color: var(--txt); padding: 1px 6px; border-radius: 4px;\n  backdrop-filter: blur(4px);\n}\n.pair { display: flex; gap: 2px; }\n.pair b {\n  font-size: 10px; font-weight: 700; width: 17px; height: 17px; display: grid; place-items: center;\n  border-radius: 4px; background: rgba(7, 8, 11, .7); color: var(--txt-faint); border: 1px solid rgba(255, 255, 255, .06);\n}\n.pair b.on { color: var(--txt); border-color: rgba(239, 232, 216, .35); }\n.pair b.show { background: var(--paper); color: #16181c; border-color: var(--paper); }\n.pair b.lost { color: var(--danger); border-color: rgba(213, 95, 78, .6); }\n.scene-tag {\n  position: absolute; left: 0; right: 0; bottom: 0; padding: 18px 7px 5px; font-size: 11px; font-weight: 600;\n  background: linear-gradient(transparent, rgba(7, 8, 11, .88)); color: var(--paper);\n  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; pointer-events: none;\n}\n.newdot {\n  position: absolute; right: 6px; bottom: 6px; font-size: 10px; font-weight: 700; color: #16181c;\n  background: var(--paper); border-radius: 9px; padding: 0 6px; box-shadow: 0 0 10px rgba(239, 232, 216, .5);\n}\n.card .scene-tag + .newdot { bottom: 24px; }\n.checking {\n  position: absolute; inset: auto 0 0 0; text-align: center; font-size: 11px; color: var(--paper);\n  background: rgba(7, 8, 11, .8); padding: 4px; animation: pulse 1.2s infinite;\n}\n.card .meta { padding: 6px 8px 8px; min-height: 46px; }\n.card .lines {\n  font-size: 11.5px; line-height: 1.45; color: var(--txt-dim);\n  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; word-break: break-all;\n}\n.card .lines.empty { color: var(--txt-faint); }\n.card .fixnote {\n  margin-top: 3px; font-size: 11px; color: var(--s-fix); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;\n}\n.card .ribbon { position: absolute; left: 0; right: 0; bottom: 0; height: 3px; background: var(--s-todo); }\n.card.st-art .ribbon { background: var(--s-art); }\n.card.st-text .ribbon { background: var(--s-text); }\n.card.st-done .ribbon { background: var(--s-done); }\n.card.st-fix .ribbon { background: var(--s-fix); }\n.card.st-fix { border-color: rgba(213, 95, 78, .45); }\n\n.face {\n  position: absolute; inset: 8px; border: 1.5px dashed var(--ink-500); border-radius: 5px;\n  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px;\n  color: var(--txt-faint); text-align: center; font-size: 11px; line-height: 1.5;\n}\n.face b { font-size: 15px; letter-spacing: .5em; margin-right: -.5em; color: var(--txt-dim); font-weight: 600; }\n.card.blank { background: var(--ink-850); border-style: dashed; }\n.card.blank .thumb { background: transparent; }\n.card.wait .face { border-color: var(--paper-line); color: var(--paper); animation: waitglow 2.2s ease-in-out infinite; }\n.card.wait .face b { color: var(--paper); }\n@keyframes waitglow { 50% { box-shadow: 0 0 18px rgba(239, 232, 216, .18) inset; border-color: rgba(239, 232, 216, .25); } }\n.card.missing .face { border-color: rgba(213, 95, 78, .45); color: var(--txt-dim); }\n.card.missing .face b { color: var(--danger); letter-spacing: .1em; margin-right: 0; font-size: 13px; }\n.card.missing .face small { font-family: var(--mono); word-break: break-all; padding: 0 6px; }\n\n.card.sel { border-color: var(--paper-line); box-shadow: 0 0 0 1px var(--paper-line); }\n.card.primary {\n  border-color: var(--paper);\n  box-shadow: 0 0 0 2px var(--paper), 0 10px 34px rgba(239, 232, 216, .16), 0 0 60px rgba(239, 232, 216, .07);\n}\n.card.primary .meta { background: linear-gradient(var(--paper-soft), transparent); }\n.card.dim { opacity: .16; filter: grayscale(1); }\n.board.only-match .card.dim { display: none; }\n.board.only-match .spr:not(:has(.card:not(.dim))) { display: none; }\n.card.dragging { opacity: .35; }\n.card.fill-target { box-shadow: 0 0 0 2px var(--paper), 0 0 30px rgba(239, 232, 216, .35); }\n.card.fill-target .face { border-color: var(--paper); color: var(--paper); }\n.drop-mark {\n  position: absolute; width: 4px; border-radius: 2px; background: var(--paper);\n  box-shadow: 0 0 14px rgba(239, 232, 216, .8); pointer-events: none; display: none; z-index: 5;\n}\n.empty-state { grid-column: 1 / -1; padding: 80px 20px; text-align: center; color: var(--txt-dim); line-height: 2; direction: ltr; }\n.empty-state b { color: var(--txt); font-size: 15px; }\n\n/* ───── 右の編集欄 ───── */\n.detail {\n  border-left: 1px solid var(--ink-700); background: var(--ink-850);\n  overflow-y: auto; min-height: 0; padding: 14px 16px 30px;\n}\n.d-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }\n.d-pno { font-family: var(--mono); font-size: 24px; font-weight: 700; letter-spacing: .02em; }\n.d-pno small { font-size: 12px; color: var(--txt-faint); font-weight: 500; margin-left: 4px; }\n.d-kind { font-size: 12px; color: var(--txt-dim); }\n.d-nav { margin-left: auto; display: flex; gap: 4px; }\n.d-nav button { width: 28px; height: 28px; background: var(--ink-800); border: 1px solid var(--ink-600); border-radius: 6px; color: var(--txt-dim); }\n.d-nav button:hover { color: var(--txt); border-color: var(--ink-500); }\n.statusbar { display: grid; grid-template-columns: repeat(5, 1fr); gap: 4px; margin-bottom: 12px; }\n.statusbar button {\n  height: 30px; border-radius: 6px; border: 1px solid var(--ink-600); background: var(--ink-800);\n  color: var(--txt-dim); font-size: 11.5px; position: relative; padding: 0 2px; white-space: nowrap;\n}\n.statusbar button::before { content: ""; position: absolute; left: 6px; right: 6px; bottom: 3px; height: 2px; border-radius: 1px; background: var(--c); opacity: .55; }\n.statusbar button:hover { color: var(--txt); border-color: var(--ink-500); }\n.statusbar button.on { background: var(--c); border-color: var(--c); color: #0c0e12; font-weight: 700; }\n.statusbar button.on::before { display: none; }\n.statusbar button.mixed { border-color: var(--c); color: var(--txt); }\n.d-preview {\n  position: relative; background: var(--ink-950); border: 1px solid var(--ink-700); border-radius: 8px;\n  aspect-ratio: 5 / 7; max-height: 46vh; width: 100%; display: flex; align-items: center; justify-content: center;\n  overflow: hidden; cursor: zoom-in; margin-bottom: 8px;\n}\n.d-preview img { max-width: 100%; max-height: 100%; object-fit: contain; display: block; }\n.d-preview .face { inset: 12px; }\n.d-preview-bar { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }\n.files { display: flex; flex-direction: column; gap: 3px; margin-bottom: 14px; font-size: 12px; }\n.file {\n  display: grid; grid-template-columns: 34px 1fr auto; align-items: center; gap: 6px;\n  padding: 4px 6px; border-radius: 5px; background: var(--ink-800);\n}\n.file .k { color: var(--txt-faint); font-size: 11px; }\n.file .n { font-family: var(--mono); font-size: 11.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; user-select: text; }\n.file.lost .n { color: var(--danger); text-decoration: line-through; }\n.file .acts { display: flex; gap: 2px; }\n.file .acts button { background: none; border: none; color: var(--txt-faint); padding: 1px 4px; border-radius: 4px; font-size: 11px; }\n.file .acts button:hover { color: var(--txt); background: var(--ink-700); }\n.field { margin-bottom: 12px; }\n.field label { display: flex; align-items: baseline; gap: 8px; color: var(--txt-dim); font-size: 12px; margin-bottom: 4px; font-weight: 600; }\n.field label .cnt { margin-left: auto; font-family: var(--mono); font-weight: 400; color: var(--txt-faint); font-size: 11px; }\n.field label .cnt.over { color: var(--s-fix); }\n.field label .hint { font-weight: 400; color: var(--txt-faint); font-size: 11px; }\n.field input, .field textarea { width: 100%; }\n.field textarea.lines { min-height: 110px; font-size: 13.5px; }\n.field textarea.memo { min-height: 64px; }\n.field textarea.fix { min-height: 52px; }\n.field.fixf textarea:not(:placeholder-shown), .field.fixf.hot textarea { border-color: rgba(213, 95, 78, .55); }\n.field.fixf label { color: #e0907f; }\n.d-actions { display: flex; flex-wrap: wrap; gap: 6px; margin: 4px 0 14px; }\n.d-note { color: var(--txt-dim); font-size: 12px; line-height: 1.7; margin: 0 0 12px; }\n.d-multi-list { font-family: var(--mono); color: var(--txt-dim); font-size: 12px; line-height: 1.8; word-break: break-all; }\n.d-empty { color: var(--txt-faint); text-align: center; padding: 60px 10px; line-height: 2; }\n.d-old { margin-top: 2px; }\n\n/* ───── 読む（原寸） ───── */\n.reader {\n  position: fixed; inset: 52px 0 0 0; z-index: 30; background: #050608;\n  display: grid; grid-template-rows: auto 1fr auto;\n}\n.r-bar {\n  display: flex; align-items: center; gap: 10px; padding: 8px 14px; background: rgba(13, 16, 21, .96);\n  border-bottom: 1px solid var(--ink-700); flex-wrap: wrap;\n}\n.r-pno { font-family: var(--mono); font-size: 16px; font-weight: 700; }\n.r-pno small { color: var(--txt-faint); font-size: 12px; font-weight: 400; }\n.r-name { color: var(--txt-dim); font-family: var(--mono); font-size: 12px; max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }\n.r-bar .statusbar { margin: 0; width: 360px; }\n.r-bar .spacer { flex: 1; }\n.r-stage { position: relative; overflow: hidden; display: flex; align-items: center; justify-content: center; gap: 0; padding: 14px 64px; min-height: 0; }\n.r-stage.compare { gap: 18px; }\n.r-pane { position: relative; height: 100%; display: flex; align-items: center; justify-content: center; min-width: 0; flex: 0 1 auto; }\n.r-pane img { max-height: 100%; max-width: 100%; object-fit: contain; display: block; box-shadow: 0 10px 40px rgba(0, 0, 0, .6); }\n.r-stage.spread .r-pane { max-width: 50%; }\n.r-stage.spread .r-pane.left img { box-shadow: -2px 10px 40px rgba(0, 0, 0, .6); }\n.r-stage.compare .r-pane { max-width: 50%; }\n.r-pane .cap {\n  position: absolute; top: 8px; left: 50%; transform: translateX(-50%); font-size: 11px;\n  background: rgba(7, 8, 11, .8); color: var(--txt); padding: 2px 10px; border-radius: 10px; white-space: nowrap;\n}\n.r-pane .face { position: static; width: min(42vh, 360px); aspect-ratio: 5 / 7; }\n.r-pane.cur img { outline: 2px solid rgba(239, 232, 216, .5); outline-offset: 3px; }\n.r-nav {\n  position: absolute; top: 50%; transform: translateY(-50%); width: 46px; height: 92px; border-radius: 10px;\n  background: rgba(23, 28, 36, .6); border: 1px solid var(--ink-600); color: var(--txt-dim); font-size: 22px; z-index: 2;\n}\n.r-nav:hover { background: rgba(40, 48, 59, .85); color: var(--txt); }\n.r-nav.prev { left: 10px; }\n.r-nav.next { right: 10px; }\n.r-hint { text-align: center; color: var(--txt-faint); font-size: 11.5px; padding: 6px 10px 9px; }\n.r-hint kbd, .help kbd {\n  font-family: var(--mono); font-size: 11px; background: var(--ink-750); border: 1px solid var(--ink-600);\n  border-bottom-width: 2px; border-radius: 4px; padding: 0 5px; color: var(--txt-dim);\n}\n\n/* ───── 右クリック ───── */\n.menu {\n  position: fixed; z-index: 60; min-width: 230px; padding: 6px;\n  background: rgba(18, 22, 29, .98); border: 1px solid var(--ink-600); border-radius: 10px;\n  box-shadow: 0 18px 50px rgba(0, 0, 0, .55); backdrop-filter: blur(8px); font-size: 12.5px;\n}\n.menu .m-status { display: grid; grid-template-columns: repeat(5, 1fr); gap: 4px; padding: 4px 4px 8px; border-bottom: 1px solid var(--ink-700); margin-bottom: 4px; }\n.menu .m-status button {\n  height: 24px; border-radius: 5px; border: 1px solid var(--ink-600); background: var(--ink-800); position: relative;\n  color: var(--txt-dim); font-size: 10.5px; padding: 0;\n}\n.menu .m-status button::after { content: ""; position: absolute; left: 4px; right: 4px; bottom: 2px; height: 2px; background: var(--c); border-radius: 1px; }\n.menu .m-status button.on { background: var(--c); color: #0c0e12; font-weight: 700; border-color: var(--c); }\n.menu .m-status button:hover { border-color: var(--c); color: var(--txt); }\n.menu .m-status button.on:hover { color: #0c0e12; }\n.mi {\n  display: flex; align-items: center; gap: 10px; width: 100%; padding: 6px 10px; border: none; background: none;\n  border-radius: 6px; text-align: left; color: var(--txt); position: relative;\n}\n.mi:hover:not(:disabled), .mi.open { background: var(--ink-650); }\n.mi:disabled { color: var(--txt-faint); cursor: default; }\n.mi .k { margin-left: auto; color: var(--txt-faint); font-family: var(--mono); font-size: 11px; }\n.mi .arrow { margin-left: auto; color: var(--txt-faint); }\n.mi.warn { color: var(--warn); }\n.mi.danger { color: var(--danger); }\n.mi .sub-note { font-size: 10.5px; color: var(--txt-faint); margin-left: 6px; }\n.mi.danger .sub-note { color: rgba(213, 95, 78, .8); }\n.msep { height: 1px; background: var(--ink-700); margin: 4px 2px; }\n.mhead { padding: 4px 10px 2px; color: var(--txt-faint); font-size: 11px; }\n\n/* ───── 窓（モーダル） ───── */\n.modal-back {\n  position: fixed; inset: 0; z-index: 80; background: rgba(4, 5, 7, .62); backdrop-filter: blur(3px);\n  display: flex; align-items: center; justify-content: center; padding: 30px;\n}\n.modal {\n  width: min(560px, 100%); max-height: calc(100vh - 60px); display: flex; flex-direction: column;\n  background: var(--ink-800); border: 1px solid var(--ink-600); border-radius: 12px;\n  box-shadow: 0 30px 90px rgba(0, 0, 0, .6); overflow: hidden;\n}\n.modal.wide { width: min(980px, 100%); }\n.modal.mid { width: min(760px, 100%); }\n.m-head { display: flex; align-items: center; gap: 10px; padding: 14px 18px; border-bottom: 1px solid var(--ink-700); }\n.m-head h2 { margin: 0; font-size: 15px; font-weight: 700; letter-spacing: .04em; }\n.m-head .x { margin-left: auto; background: none; border: none; color: var(--txt-faint); font-size: 18px; }\n.m-head .x:hover { color: var(--txt); }\n.m-body { padding: 16px 18px; overflow-y: auto; user-select: text; }\n.m-foot { display: flex; align-items: center; justify-content: flex-end; gap: 8px; padding: 12px 18px; border-top: 1px solid var(--ink-700); background: var(--ink-850); }\n.m-foot .left { margin-right: auto; color: var(--txt-dim); font-size: 12px; flex: 1; min-width: 0; word-break: break-all; line-height: 1.5; }\n.m-foot .btn { flex-shrink: 0; white-space: nowrap; }\n.m-tabs { display: flex; gap: 2px; padding: 0 14px; border-bottom: 1px solid var(--ink-700); background: var(--ink-850); }\n.m-tabs button { background: none; border: none; padding: 10px 12px; color: var(--txt-dim); border-bottom: 2px solid transparent; margin-bottom: -1px; }\n.m-tabs button.on { color: var(--txt); border-bottom-color: var(--paper); }\n.form-row { display: grid; grid-template-columns: 150px 1fr; gap: 12px; align-items: center; margin-bottom: 12px; }\n.form-row > label:first-child { color: var(--txt-dim); font-size: 12.5px; }\n.form-row .note { grid-column: 2; color: var(--txt-faint); font-size: 11.5px; margin-top: -6px; line-height: 1.6; }\n.form-row .inline { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }\n.form-row input[type="number"] { width: 80px; }\n.radio { display: inline-flex; align-items: center; gap: 5px; margin-right: 12px; cursor: pointer; }\n.radio input, .check input { accent-color: var(--paper); }\n.check { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; }\n.sect { font-size: 12px; color: var(--paper); font-weight: 700; letter-spacing: .06em; margin: 18px 0 10px; padding-bottom: 6px; border-bottom: 1px solid var(--ink-700); }\n.sect:first-child { margin-top: 0; }\n.preview-names { font-family: var(--mono); font-size: 12px; color: var(--txt-dim); background: var(--ink-900); border: 1px solid var(--ink-700); border-radius: 7px; padding: 8px 10px; line-height: 1.75; max-height: 160px; overflow: auto; white-space: pre; }\n.progress { height: 6px; border-radius: 3px; background: var(--ink-700); overflow: hidden; }\n.progress > i { display: block; height: 100%; background: var(--paper); width: 0; transition: width .15s; }\n.msg-text { line-height: 1.8; white-space: pre-wrap; }\n.tile-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(118px, 1fr)); gap: 10px; }\n.tile {\n  background: var(--ink-850); border: 1px solid var(--ink-700); border-radius: 7px; overflow: hidden; cursor: pointer; text-align: left; padding: 0;\n  display: flex; flex-direction: column;\n}\n.tile:hover { border-color: var(--paper-line); }\n.tile .tp { aspect-ratio: 5 / 7; background: var(--ink-950); display: flex; align-items: center; justify-content: center; }\n.tile .tp img { width: 100%; height: 100%; object-fit: contain; }\n.tile .tc { padding: 5px 7px 7px; font-size: 11px; color: var(--txt-dim); line-height: 1.4; }\n.tile .tc b { color: var(--txt); font-family: var(--mono); font-weight: 600; }\n.tile .tc .fn { font-family: var(--mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: block; }\n.tile .tc .act { margin-top: 5px; display: flex; gap: 4px; }\n.tile.static { cursor: default; }\n.tile .tag { display: inline-block; font-size: 10px; padding: 0 5px; border-radius: 3px; background: var(--ink-700); color: var(--txt-dim); margin-right: 4px; }\n.tile .tag.new { background: var(--paper); color: #16181c; font-weight: 700; }\n.list-empty { color: var(--txt-faint); padding: 18px 4px; }\n.bk-list { display: flex; flex-direction: column; gap: 4px; }\n.bk { display: grid; grid-template-columns: 1fr auto auto; gap: 12px; align-items: center; padding: 7px 10px; border-radius: 6px; background: var(--ink-850); }\n.bk .when { font-size: 12.5px; }\n.bk .info { color: var(--txt-faint); font-size: 11.5px; }\n.help { display: grid; grid-template-columns: auto 1fr; gap: 7px 18px; font-size: 12.5px; }\n.help kbd { margin-right: 3px; }\n.help .h { grid-column: 1 / -1; color: var(--paper); font-weight: 700; font-size: 12px; margin-top: 10px; letter-spacing: .06em; }\n.help .h:first-child { margin-top: 0; }\n\n/* ───── 知らせ（トースト） ───── */\n.toasts { position: fixed; left: 50%; bottom: 22px; transform: translateX(-50%); z-index: 90; display: flex; flex-direction: column; gap: 8px; align-items: center; pointer-events: none; }\n.toast {\n  pointer-events: auto; display: flex; align-items: center; gap: 12px; max-width: min(720px, 90vw);\n  background: rgba(23, 28, 36, .97); border: 1px solid var(--ink-500); border-radius: 10px; padding: 9px 12px 9px 14px;\n  box-shadow: 0 14px 40px rgba(0, 0, 0, .5); font-size: 12.5px; animation: tin .18s ease-out;\n}\n.toast.error { border-color: rgba(213, 95, 78, .7); }\n.toast.error::before { content: "！"; color: var(--danger); font-weight: 700; }\n.toast button { background: none; border: 1px solid var(--ink-500); color: var(--paper); border-radius: 6px; padding: 3px 10px; font-size: 12px; }\n.toast button:hover { background: var(--ink-650); }\n@keyframes tin { from { transform: translateY(8px); opacity: 0; } }\n.drag-ghost {\n  position: fixed; top: -100px; left: -100px; padding: 6px 12px; border-radius: 8px; background: var(--paper);\n  color: #16181c; font-weight: 700; font-size: 12px; box-shadow: 0 8px 20px rgba(0, 0, 0, .4);\n}\n\n/* ───── 切れたとき ───── */\n.offline {\n  position: fixed; inset: 0; z-index: 100; background: rgba(5, 6, 8, .9); display: flex; align-items: center; justify-content: center;\n}\n.offline .box { max-width: 460px; text-align: center; line-height: 2; background: var(--ink-800); border: 1px solid var(--ink-600); border-radius: 12px; padding: 30px 34px; }\n.offline .box b { font-size: 15px; }\n\n@media (max-width: 1100px) {\n  :root { --detail-w: 320px; }\n  .brand span, .totals { display: none; }\n}\n',
    'app.js': '/* 台割工房 — 画面の動き。ファイルには触らず、操作はすべて本体（Python）へ頼む */\n\'use strict\';\n(() => {\n  const TOKEN = document.querySelector(\'meta[name="dk-token"]\').content;\n  const $ = (s, el = document) => el.querySelector(s);\n  const $$ = (s, el = document) => [...el.querySelectorAll(s)];\n  const esc = (s) => String(s ?? \'\').replace(/[&<>"\']/g, (c) => ({ \'&\': \'&amp;\', \'<\': \'&lt;\', \'>\': \'&gt;\', \'"\': \'&quot;\', "\'": \'&#39;\' }[c]));\n  const pad = (n) => String(n).padStart(3, \'0\');\n  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));\n  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));\n  const STATUS = [[\'todo\', \'未着手\'], [\'art\', \'画像のみ\'], [\'text\', \'セリフ入\'], [\'done\', \'完成\'], [\'fix\', \'要修正\']];\n  const SCOLOR = { todo: \'var(--s-todo)\', art: \'var(--s-art)\', text: \'var(--s-text)\', done: \'var(--s-done)\', fix: \'var(--s-fix)\' };\n  const SLABEL = Object.fromEntries(STATUS);\n  const FIELDS = [\'scene\', \'lines\', \'memo\', \'fix\'];\n\n  const S = {\n    st: null, rev: 0, pages: [], byId: new Map(), idx: new Map(),\n    sel: new Set(), primary: null, anchor: null, lastIdx: 0,\n    view: \'board\', readMode: \'single\',\n    filter: \'all\', query: \'\', onlyMatch: false, match: null,\n    detailWhich: null, detailSig: \'\', stripSig: \'\', layoutSig: \'\', noticesHtml: \'\',\n    pendingText: new Map(), chain: Promise.resolve(),\n    fetching: false, refetch: false, sseDown: 0, zoomLive: 0,\n    modals: [], exportBox: null,\n  };\n  const cards = new Map();\n\n  const thumbURL = (rel, v) => `/thumb?f=${encodeURIComponent(rel)}&v=${encodeURIComponent(v || \'\')}&t=${encodeURIComponent(TOKEN)}`;\n  const imgURL = (rel, v) => `/img?f=${encodeURIComponent(rel)}&v=${encodeURIComponent(v || \'\')}&t=${encodeURIComponent(TOKEN)}`;\n\n  // ───────────── 本体とのやり取り ─────────────\n\n  async function request(path, opts = {}) {\n    const headers = { \'X-Token\': TOKEN };\n    if (opts.body) headers[\'Content-Type\'] = \'application/json\';\n    const r = await fetch(path, { ...opts, headers });\n    try { return await r.json(); } catch (e) { return { ok: false, error: \'応答を読めませんでした\' }; }\n  }\n\n  function api(op, args = {}, opt = {}) {\n    const job = S.chain.then(() => doApi(op, args, opt));\n    S.chain = job.catch(() => null);\n    return job;\n  }\n\n  async function doApi(op, args, opt) {\n    let data;\n    try {\n      data = await request(\'/api/op\', { method: \'POST\', body: JSON.stringify({ op, args, state: opt.state !== false }) });\n    } catch (e) {\n      toast(\'台割工房の本体とつながりません\', \'error\');\n      throw e;\n    }\n    if (!data.ok) {\n      if (!opt.quiet) toast(data.error || \'うまくいきませんでした\', \'error\');\n      const err = new Error(data.error || \'failed\');\n      err.handled = true;\n      throw err;\n    }\n    if (data.state) applyState(data.state);\n    if (data.select && data.select.length) setSelection(data.select);\n    if (data.toast && !opt.noToast) {\n      const canUndo = data.label && op !== \'undo\' && op !== \'redo\';\n      toast(data.toast, \'info\', canUndo ? { label: \'元に戻す\', fn: undo } : null);\n    }\n    return data;\n  }\n\n  const run = (op, args, opt) => api(op, args, opt).catch(() => null);\n\n  async function fetchState() {\n    if (S.fetching) { S.refetch = true; return; }\n    S.fetching = true;\n    try {\n      const d = await request(\'/api/state\');\n      if (d.ok) applyState(d.state);\n    } catch (e) { /* つながらないときは見張りに任せる */ }\n    S.fetching = false;\n    if (S.refetch) { S.refetch = false; fetchState(); }\n  }\n  let fetchTimer = 0;\n  const scheduleFetch = () => { clearTimeout(fetchTimer); fetchTimer = setTimeout(fetchState, 50); };\n\n  function connect() {\n    const es = new EventSource(\'/api/events?t=\' + encodeURIComponent(TOKEN));\n    es.addEventListener(\'hello\', (e) => {\n      S.sseDown = 0; $(\'#offline\').hidden = true;\n      const d = JSON.parse(e.data);\n      if (d.rev !== S.rev) fetchState();\n    });\n    es.addEventListener(\'rev\', (e) => { if (JSON.parse(e.data) > S.rev) scheduleFetch(); });\n    es.addEventListener(\'index\', (e) => { if (S.st) { S.st.index = JSON.parse(e.data); renderStripInfo(); } });\n    es.addEventListener(\'ident\', (e) => { if (S.st) { S.st.ident = JSON.parse(e.data); renderStripInfo(); } });\n    es.addEventListener(\'export\', (e) => exportProgress(JSON.parse(e.data)));\n    es.onopen = () => { S.sseDown = 0; $(\'#offline\').hidden = true; };\n    es.onerror = () => { if (!S.sseDown) { S.sseDown = Date.now(); checkAlive(); } };\n  }\n\n  async function checkAlive() {\n    await sleep(4000);\n    if (!S.sseDown) return;\n    try { const d = await request(\'/api/ping\'); if (d.ok) return; } catch (e) { /* 下で知らせる */ }\n    $(\'#offline\').hidden = false;\n  }\n\n  // ───────────── 状態 ─────────────\n\n  function applyState(st) {\n    if (S.st && st.rev < S.rev) return;\n    const first = !S.st;\n    S.st = st;\n    S.rev = st.rev;\n    S.pages = st.pages;\n    S.byId = new Map(st.pages.map((p) => [p.id, p]));\n    S.idx = new Map(st.pages.map((p, i) => [p.id, i]));\n    // 入力中の文字は、手元の新しい方を残す\n    for (const it of S.pendingText.values()) {\n      const p = S.byId.get(it.id);\n      if (p) p[it.field] = it.value;\n    }\n    for (const id of [...S.sel]) if (!S.byId.has(id)) S.sel.delete(id);\n    if (S.primary && !S.byId.has(S.primary)) {\n      S.primary = null;\n      if (!S.sel.size && S.pages.length) {\n        const p = S.pages[clamp(S.lastIdx, 0, S.pages.length - 1)];\n        S.sel = new Set([p.id]);\n        S.anchor = p.id;\n      }\n      S.primary = [...S.sel].pop() || null;\n    }\n    if (first && S.pages.length && !S.primary) {\n      S.primary = S.pages[0].id; S.sel = new Set([S.primary]); S.anchor = S.primary;\n    }\n    if (S.primary) S.lastIdx = S.idx.get(S.primary);\n    for (const f of [\'new\', \'missing\', \'wait\']) if (S.filter === f && !st.counts[f]) S.filter = \'all\';\n    document.title = st.window_title || \'台割工房\';\n    computeMatches();\n    render();\n  }\n\n  function selectedIds() { return S.pages.filter((p) => S.sel.has(p.id)).map((p) => p.id); }\n\n  function pageMatches(p) {\n    const f = S.filter;\n    if (f !== \'all\') {\n      if (f === \'blank\' || f === \'missing\') { if (p.kind !== f) return false; }\n      else if (f === \'new\') { if (!p.new) return false; }\n      else if (f === \'wait\') { if (!p.wait) return false; }\n      else if (p.status !== f) return false;\n    }\n    const q = S.query.trim().toLowerCase();\n    if (q) {\n      const hay = [p.raw, p.fin, p.scene, p.lines, p.memo, p.fix, \'p.\' + pad(p.n)].join(\'\\n\').toLowerCase();\n      if (!q.split(/\\s+/).every((t) => hay.includes(t))) return false;\n    }\n    return true;\n  }\n\n  function computeMatches() {\n    S.match = (S.filter === \'all\' && !S.query.trim()) ? null : new Set(S.pages.filter(pageMatches).map((p) => p.id));\n  }\n\n  function shownFile(p, which) {\n    const w = which || S.st.settings.view;\n    const raw = p.raw_ok ? [\'raw\', p.raw, p.raw_v] : null;\n    const fin = p.fin_ok ? [\'fin\', p.fin, p.fin_v] : null;\n    return w === \'raw\' ? (raw || fin) : (fin || raw);\n  }\n\n  function spreads() {\n    const n = S.pages.length, out = [];\n    let i = 0;\n    if (S.st.settings.cover_single && n) { out.push([0]); i = 1; }\n    for (; i < n; i += 2) out.push(i + 1 < n ? [i, i + 1] : [i]);\n    return out;\n  }\n  const spreadStart = (i) => S.st.settings.cover_single ? (i === 0 || i % 2 === 1) : i % 2 === 0;\n  function spreadOf(i) { return spreads().find((g) => g.includes(i)) || [i]; }\n\n  function dialogueCount(text) {\n    let n = 0;\n    for (const ln of (text || \'\').split(\'\\n\')) {\n      const m = ln.match(/「(.*?)(」|$)/);\n      n += (m ? m[1] : ln).replace(/\\s/g, \'\').length;\n    }\n    return n;\n  }\n\n  // ───────────── 描く ─────────────\n\n  function render() {\n    if (!S.st) return;\n    renderTop();\n    renderStrip();\n    renderStripInfo();\n    renderNotices();\n    renderChips();\n    renderToolbar();\n    renderBoard();\n    renderDetail();\n    if (S.view === \'read\') renderReader();\n  }\n\n  function renderTop() {\n    const st = S.st;\n    const t = $(\'#title\');\n    if (document.activeElement !== t && t.value !== st.title) t.value = st.title;\n    t.placeholder = st.folder_name ? `作品タイトル（${st.folder_name}）` : \'作品タイトル\';\n    $(\'#folder\').textContent = st.folder_name;\n    $(\'#folder\').title = `${st.folder}\\n（クリックでエクスプローラを開く）`;\n    const c = st.counts, n = st.pages.length;\n    const pct = n ? Math.round((c.done / n) * 100) : 0;\n    $(\'#totals\').innerHTML = `<b>${n}</b> ページ\u3000完成 <b>${c.done}</b> <span class="pct">${pct}%</span>`;\n    const u = $(\'#undo\'), r = $(\'#redo\');\n    u.disabled = !st.undo.n; u.title = st.undo.n ? `元に戻す：${st.undo.label}（Ctrl+Z）` : \'元に戻す（Ctrl+Z）\';\n    r.disabled = !st.redo.n; r.title = st.redo.n ? `やり直し：${st.redo.label}（Ctrl+Y）` : \'やり直し（Ctrl+Y）\';\n    $$(\'#tabs button\').forEach((b) => b.classList.toggle(\'on\', b.dataset.view === S.view));\n  }\n\n  function renderStrip() {\n    const el = $(\'#strip\');\n    const spread = S.st.settings.spread;\n    const sig = S.pages.map((p) => p.status + p.kind[0]).join(\',\') + \'|\' + spread + S.st.settings.cover_single;\n    if (sig !== S.stripSig) {\n      S.stripSig = sig;\n      el.innerHTML = S.pages.map((p, i) =>\n        `<i data-i="${i}" class="st-${p.status}${p.kind !== \'image\' ? \' \' + p.kind : \'\'}${spread && i > 0 && spreadStart(i) ? \' gap\' : \'\'}"></i>`).join(\'\');\n    }\n    const ticks = el.children;\n    for (let i = 0; i < ticks.length && i < S.pages.length; i++) {\n      const p = S.pages[i];\n      ticks[i].classList.toggle(\'sel\', S.sel.has(p.id));\n      ticks[i].classList.toggle(\'primary\', p.id === S.primary);\n      ticks[i].classList.toggle(\'dim\', !!S.match && !S.match.has(p.id));\n    }\n  }\n\n  function renderStripInfo() {\n    const st = S.st, el = $(\'#strip-info\');\n    if (!st) return;\n    if (st.ident) el.innerHTML = `<span class="busy">名前の変わった絵を照合中 ${st.ident.done}/${st.ident.total}</span>`;\n    else if (st.index && st.index.total) el.innerHTML = `<span class="busy">絵を読み込み中 ${st.index.done}/${st.index.total}</span>`;\n    else el.textContent = `残り ${st.pages.length - st.counts.done} ・ 要修正 ${st.counts.fix}`;\n  }\n\n  function renderNotices() {\n    const st = S.st, out = [];\n    for (const n of st.notices) {\n      if (n.kind === \'ask\') {\n        out.push(`<div class="notice ask" data-id="${esc(n.id)}"><div class="txt">${esc(n.text)}</div><div class="acts">${\n          n.actions.map(([a, l], i) => `<button class="btn small${i === 0 ? \' primary\' : \'\'}" data-act="${esc(a)}">${esc(l)}</button>`).join(\'\')}</div></div>`);\n      } else {\n        out.push(`<div class="notice ${esc(n.kind)}" data-id="${esc(n.id)}"><div class="txt">${esc(n.text)}</div><button class="x" data-act="dismiss" title="閉じる">×</button></div>`);\n      }\n    }\n    for (const s of st.suggestions) {\n      const page = S.byId.get(s.page);\n      out.push(`<div class="notice ask" data-sugg="${esc(s.id)}"><img src="${thumbURL(s.cand, s.cand_v)}" alt=""><div class="txt">新しく来た絵「${esc(s.cand)}」は、${\n        page ? \'P.\' + pad(page.n) : \'見つからないページ\'}（${esc(s.missing)}・見つからない）の差し替えですか？</div><div class="acts"><button class="btn small primary" data-act="yes">差し替えとして入れる</button><button class="btn small" data-act="no">別のページのままにする</button></div></div>`);\n    }\n    const html = out.join(\'\');\n    if (html !== S.noticesHtml) { S.noticesHtml = html; $(\'#notices\').innerHTML = html; }\n  }\n\n  function renderChips() {\n    const c = S.st.counts, n = S.pages.length;\n    const chips = [[\'all\', \'すべて\', n, \'\']];\n    for (const [k, l] of STATUS) chips.push([k, l, c[k], `style="background:${SCOLOR[k]}"`]);\n    chips.push([\'blank\', \'空白\', c.blank, \'class="blank"\']);\n    if (c.wait) chips.push([\'wait\', \'待ち受け\', c.wait, \'class="wait"\']);\n    if (c.new) chips.push([\'new\', \'新着\', c.new, \'class="new"\']);\n    if (c.missing) chips.push([\'missing\', \'見つからない\', c.missing, \'class="missing"\']);\n    let html = chips.map(([k, l, cnt, dot]) =>\n      `<button class="chip${S.filter === k ? \' on\' : \'\'}" data-f="${k}">${k === \'all\' ? \'\' : `<i ${dot}></i>`}${l} <b>${cnt}</b></button>`).join(\'\');\n    if (c.new) html += \'<button class="btn small ghost" data-seen-all title="新着の印をすべて消す">新着の印を消す</button>\';\n    const el = $(\'#chips\');\n    if (el.innerHTML !== html) el.innerHTML = html;\n  }\n\n  function renderToolbar() {\n    const st = S.st;\n    let cnt = \'\';\n    if (S.match) {\n      const list = S.pages.filter((p) => S.match.has(p.id));\n      const pos = list.findIndex((p) => p.id === S.primary);\n      cnt = pos >= 0 ? `${pos + 1}/${list.length}` : `${list.length}件`;\n    }\n    $(\'#search-count\').textContent = cnt;\n    $$(\'#view-seg button\').forEach((b) => b.classList.toggle(\'on\', b.dataset.v === st.settings.view));\n    $(\'#spread\').checked = st.settings.spread;\n    $(\'#only-match\').checked = S.onlyMatch;\n    if (!S.zoomLive && document.activeElement !== $(\'#zoom\')) $(\'#zoom\').value = st.settings.thumb_size;\n  }\n\n  function cardSig(p) {\n    return [p.n, p.kind, p.status, p.raw, p.fin, p.raw_ok, p.fin_ok, p.raw_v, p.fin_v, p.scene,\n      p.lines.slice(0, 140), p.fix.slice(0, 80), p.wait, p.new, p.checking, S.st.settings.view].join(\'\\u0001\');\n  }\n\n  function cardHTML(p) {\n    let face = \'\', pair = \'\';\n    if (p.kind === \'image\') {\n      const f = shownFile(p);\n      pair = `<span class="pair"><b class="${p.raw ? (p.raw_ok ? \'on\' : \'lost\') : \'\'}${f[0] === \'raw\' ? \' show\' : \'\'}" title="原画${p.raw ? \'：\' + esc(p.raw) : \'なし\'}">原</b><b class="${p.fin ? (p.fin_ok ? \'on\' : \'lost\') : \'\'}${f[0] === \'fin\' ? \' show\' : \'\'}" title="完成${p.fin ? \'：\' + esc(p.fin) : \'なし\'}">完</b></span>`;\n      face = `<img loading="lazy" decoding="async" draggable="false" src="${thumbURL(f[1], f[2])}" alt="">`;\n    } else if (p.kind === \'blank\') {\n      face = p.wait ? \'<div class="face"><b>待ち受け</b>次にフォルダへ来た絵が<br>ここに入ります</div>\'\n        : \'<div class="face"><b>空白</b>ここに1ページ入ります</div>\';\n    } else {\n      face = `<div class="face"><b>見つかりません</b><small>${esc(p.raw || p.fin)}</small>戻せば元通りになります</div>`;\n    }\n    const text = p.lines.split(\'\\n\').filter((s) => s.trim()).join(\' ／ \');\n    const linesHtml = text ? esc(text) : (p.kind === \'blank\' ? (p.memo.trim() ? esc(p.memo.split(\'\\n\')[0]) : \'\') : \'セリフ未記入\');\n    return `<div class="thumb">${face}<div class="badges"><span class="pno">P.${pad(p.n)}</span>${pair}</div>${\n      p.scene ? `<div class="scene-tag">${esc(p.scene)}</div>` : \'\'}${p.new ? \'<span class="newdot">新</span>\' : \'\'}${\n      p.checking ? \'<div class="checking">照合中…</div>\' : \'\'}</div><div class="meta"><div class="lines${text ? \'\' : \' empty\'}">${linesHtml}</div>${\n      p.fix.trim() ? `<div class="fixnote">✎ ${esc(p.fix.split(\'\\n\')[0])}</div>` : \'\'}</div><i class="ribbon"></i>`;\n  }\n\n  function renderBoard() {\n    const board = $(\'#board\');\n    const st = S.st;\n    board.style.setProperty(\'--tw\', (S.zoomLive || st.settings.thumb_size) + \'px\');\n    const spread = st.settings.spread;\n    board.classList.toggle(\'spread\', spread);\n    board.classList.toggle(\'only-match\', S.onlyMatch && !!S.match);\n    const keep = new Set(), els = [];\n    for (const p of S.pages) {\n      keep.add(p.id);\n      let c = cards.get(p.id);\n      if (!c) {\n        const el = document.createElement(\'div\');\n        el.dataset.id = p.id;\n        el.draggable = true;\n        c = { el, sig: null };\n        cards.set(p.id, c);\n      }\n      const sig = cardSig(p);\n      if (c.sig !== sig) {\n        c.el.innerHTML = cardHTML(p);\n        c.el.className = `card ${p.kind} st-${p.status}${p.wait ? \' wait\' : \'\'}`;\n        c.sig = sig;\n      }\n      c.el.classList.toggle(\'sel\', S.sel.has(p.id));\n      c.el.classList.toggle(\'primary\', p.id === S.primary);\n      c.el.classList.toggle(\'dim\', !!S.match && !S.match.has(p.id));\n      els.push(c.el);\n    }\n    for (const id of [...cards.keys()]) if (!keep.has(id)) cards.delete(id);\n    const layout = (spread ? \'S\' + st.settings.cover_single : \'G\') + S.pages.map((p) => p.id).join(\',\');\n    if (layout !== S.layoutSig) {\n      S.layoutSig = layout;\n      if (!S.pages.length) {\n        board.innerHTML = \'<div class="empty-state"><b>このフォルダには、まだ絵がありません</b><br>画像を入れると、ここに並びます。空白を入れて先に台割を組むこともできます（I キー）。</div>\';\n        return;\n      }\n      if (spread) {\n        board.replaceChildren(...spreads().map((g) => {\n          const w = document.createElement(\'div\');\n          w.className = \'spr\' + (g.length === 1 ? \' single\' : \'\');\n          g.forEach((i) => w.appendChild(els[i]));\n          return w;\n        }));\n      } else {\n        board.replaceChildren(...els);\n      }\n    }\n  }\n\n  function renderSelection() {\n    for (const [id, c] of cards) {\n      c.el.classList.toggle(\'sel\', S.sel.has(id));\n      c.el.classList.toggle(\'primary\', id === S.primary);\n    }\n    renderStrip();\n    renderToolbar();\n    renderDetail();\n    if (S.view === \'read\') renderReader();\n  }\n\n  function kindLabel(p) {\n    if (p.kind === \'blank\') return p.wait ? \'空白（待ち受け）\' : \'空白\';\n    if (p.kind === \'missing\') return \'絵が見つかりません\';\n    return p.raw_ok && p.fin_ok ? \'原画と完成\' : (p.fin_ok ? \'完成のみ\' : \'原画のみ\');\n  }\n\n  function statusButtons(cur, mixed) {\n    return STATUS.map(([k, l], i) => `<button data-status="${k}" class="${cur === k ? \'on\' : (mixed && mixed.has(k) ? \'mixed\' : \'\')}" style="--c:${SCOLOR[k]}" title="${l}（${i + 1}）">${l}</button>`).join(\'\');\n  }\n\n  function fileRow(kind, rel, ok, extra = \'\') {\n    return `<div class="file${ok ? \'\' : \' lost\'}"><span class="k">${kind}</span><span class="n" title="${esc(rel)}">${esc(rel)}</span><span class="acts">${\n      ok ? `<button data-reveal="${esc(rel)}" title="エクスプローラで表示">表示</button><button data-ext="${esc(rel)}" title="外部アプリで開く">開く</button>` : \'<span style="color:var(--danger);font-size:11px">なし</span>\'}${extra}</span></div>`;\n  }\n\n  function detailHTML(p) {\n    const n = S.pages.length;\n    let h = `<div class="d-head"><div class="d-pno">P.${pad(p.n)}<small>/ ${n}</small></div><div class="d-kind">${kindLabel(p)}</div><div class="d-nav"><button data-nav="-1" title="前のページ（PageUp）">‹</button><button data-nav="1" title="次のページ（PageDown）">›</button></div></div>`;\n    h += `<div class="statusbar">${statusButtons(p.status)}</div>`;\n    if (p.kind === \'image\') {\n      const which = (S.detailWhich === \'raw\' && p.raw_ok) || (S.detailWhich === \'fin\' && p.fin_ok) ? S.detailWhich : null;\n      const f = shownFile(p, which);\n      h += `<div class="d-preview" data-act="read" title="クリックで原寸（Enter）"><img src="${thumbURL(f[1], f[2])}" alt=""></div>`;\n      if (p.raw_ok && p.fin_ok) {\n        h += `<div class="d-preview-bar"><div class="seg"><button data-which="raw" class="${f[0] === \'raw\' ? \'on\' : \'\'}">原画</button><button data-which="fin" class="${f[0] === \'fin\' ? \'on\' : \'\'}">完成</button></div></div>`;\n      }\n    } else if (p.kind === \'blank\') {\n      h += `<div class="d-preview" style="cursor:default"><div class="face"><b>${p.wait ? \'待ち受け\' : \'空白\'}</b>${p.wait ? \'次にフォルダへ来た絵が<br>ここに入ります\' : \'ここに1ページ入ります\'}</div></div>`;\n      h += `<div class="d-actions"><button class="btn small" data-act="fill">ここに絵を入れる…</button><button class="btn small" data-act="wait">${p.wait ? \'待ち受けをやめる\' : \'待ち受けにする\'}</button><button class="btn small ghost" data-act="delete">空白を消す</button></div>`;\n    } else {\n      h += `<div class="d-preview" style="cursor:default"><div class="face"><b>見つかりません</b><small>${esc(p.raw || p.fin)}</small></div></div>`;\n      h += \'<p class="d-note">絵のファイルがフォルダにありません。消したり外へ移したりしたなら、戻せば元通りになります。セリフとメモはそのまま残っています。</p>\';\n      h += `<div class="d-actions"><button class="btn small" data-act="fill">別の絵を入れる…</button><button class="btn small" data-act="wait">${p.wait ? \'待ち受けをやめる\' : \'待ち受けにする\'}</button><button class="btn small ghost" data-act="delete">このページを消す</button></div>`;\n    }\n    if (p.raw || p.fin || p.old.length) {\n      h += \'<div class="files">\';\n      if (p.raw) h += fileRow(\'原画\', p.raw, p.raw_ok);\n      if (p.fin) h += fileRow(\'完成\', p.fin, p.fin_ok);\n      for (const o of p.old) h += fileRow(\'旧版\', o.rel, o.ok, o.ok ? `<button data-useold="${esc(o.rel)}" title="この旧版を完成に戻す">完成に</button>` : \'\');\n      h += \'</div>\';\n    }\n    const warn = S.st.settings.chars_warn;\n    h += `<div class="field"><label>シーン名</label><input data-field="scene" placeholder="例）屋上・告白" maxlength="200" spellcheck="false"></div>`;\n    h += `<div class="field"><label>セリフ<span class="hint">1行に1台詞（話者「台詞」）</span><span class="cnt" id="cnt-lines" data-warn="${warn}"></span></label><textarea data-field="lines" class="lines" placeholder="例）サキ「あのさ」" spellcheck="false"></textarea></div>`;\n    h += \'<div class="field"><label>メモ<span class="hint">ト書き・演出・自分用の覚え書き</span></label><textarea data-field="memo" class="memo" spellcheck="false"></textarea></div>\';\n    h += `<div class="field fixf${p.status === \'fix\' ? \' hot\' : \'\'}"><label>修正指示</label><textarea data-field="fix" class="fix" placeholder="何を直すか（要修正のページで目立ちます）" spellcheck="false"></textarea></div>`;\n    return h;\n  }\n\n  function multiHTML(ids) {\n    const ps = ids.map((id) => S.byId.get(id));\n    const st = new Set(ps.map((p) => p.status));\n    const cur = st.size === 1 ? ps[0].status : null;\n    const pairable = ps.length === 2 && ps.every((p) => p.kind === \'image\' && (!!p.raw + !!p.fin) === 1);\n    let h = `<div class="d-head"><div class="d-pno">${ids.length}<small>ページを選択中</small></div></div>`;\n    h += `<div class="statusbar">${statusButtons(cur, cur ? null : st)}</div>`;\n    h += \'<div class="d-actions"><button class="btn small" data-move="top">先頭へ</button><button class="btn small" data-move="end">末尾へ</button><button class="btn small" data-move="up">1つ前へ</button><button class="btn small" data-move="down">1つ後ろへ</button><button class="btn small" data-move="number">番号を指定…</button></div>\';\n    h += `<div class="d-actions"><button class="btn small" data-act="blank-before">前に空白</button><button class="btn small" data-act="blank-after">後ろに空白</button>${pairable ? \'<button class="btn small" data-act="pair">2枚を原画と完成の組にする</button>\' : \'\'}</div>`;\n    h += \'<div class="d-actions"><button class="btn small warn" data-act="exclude" title="台割から外す。ファイルはそのまま（X）">除外</button><button class="btn small danger" data-act="quarantine" title="「未使用」フォルダへ移す（Shift+X）">隔離（ファイル移動）</button></div>\';\n    h += `<p class="d-note">Shift+クリックで範囲、Ctrl+クリックで1枚ずつ。ドラッグでまとめて動かせます。</p><div class="d-multi-list">${ps.map((p) => \'P.\' + pad(p.n)).join(\'\u3000\')}</div>`;\n    return h;\n  }\n\n  function renderDetail() {\n    const el = $(\'#detail\');\n    const ids = selectedIds();\n    if (!ids.length) {\n      if (S.detailSig !== \'none\') { S.detailSig = \'none\'; el.innerHTML = \'<div class="d-empty">ページを選ぶと、ここで<br>進行・セリフ・メモを編集できます</div>\'; }\n      return;\n    }\n    if (ids.length > 1) {\n      const sig = \'multi|\' + ids.join(\',\') + \'|\' + ids.map((id) => S.byId.get(id).status + S.byId.get(id).n).join(\',\');\n      if (S.detailSig !== sig) { S.detailSig = sig; el.innerHTML = multiHTML(ids); }\n      return;\n    }\n    const p = S.byId.get(S.primary) || S.byId.get(ids[0]);\n    const sig = [p.id, p.n, S.pages.length, p.kind, p.raw, p.fin, p.raw_ok, p.fin_ok, p.raw_v, p.fin_v,\n      JSON.stringify(p.old), p.status, p.wait, S.detailWhich, S.st.settings.view, S.st.settings.chars_warn].join(\'|\');\n    if (S.detailSig !== sig) {\n      const act = document.activeElement;\n      const keep = act && el.contains(act) && act.dataset.field && S.detailPage === p.id\n        ? { f: act.dataset.field, s: act.selectionStart, e: act.selectionEnd } : null;\n      S.detailSig = sig;\n      S.detailPage = p.id;\n      el.innerHTML = detailHTML(p);\n      for (const f of FIELDS) { const inp = el.querySelector(`[data-field="${f}"]`); if (inp) inp.value = p[f]; }\n      if (keep) {\n        const inp = el.querySelector(`[data-field="${keep.f}"]`);\n        if (inp) { inp.focus(); try { inp.setSelectionRange(keep.s, keep.e); } catch (e) { /* 入力欄の種類による */ } }\n      }\n    } else {\n      for (const f of FIELDS) {\n        const inp = el.querySelector(`[data-field="${f}"]`);\n        if (inp && document.activeElement !== inp && !S.pendingText.has(p.id + \'|\' + f) && inp.value !== p[f]) inp.value = p[f];\n      }\n    }\n    $$(\'textarea\', el).forEach(autoGrow);\n    updateCount();\n  }\n\n  function autoGrow(t) {\n    t.style.height = \'auto\';\n    t.style.height = Math.min(t.scrollHeight + 2, 420) + \'px\';\n  }\n\n  function updateCount() {\n    const c = $(\'#cnt-lines\'), t = $(\'#detail [data-field="lines"]\');\n    if (!c || !t) return;\n    const n = dialogueCount(t.value), warn = +c.dataset.warn || 120;\n    c.textContent = `${n}字`;\n    c.classList.toggle(\'over\', n > warn);\n    c.title = `セリフの字数（目安 ${warn}字）`;\n  }\n\n  // ───────────── 選ぶ ─────────────\n\n  function setSelection(ids) {\n    const ok = ids.filter((id) => S.byId.has(id));\n    if (!ok.length) return;\n    S.sel = new Set(ok);\n    S.primary = ok[ok.length - 1];\n    S.anchor = ok[0];\n    afterSelect({});\n  }\n\n  function selectOnly(id, opt = {}) {\n    if (!S.byId.has(id)) return;\n    flushAllText();\n    S.sel = new Set([id]);\n    S.primary = id;\n    S.anchor = id;\n    afterSelect(opt);\n  }\n\n  function toggleSel(id) {\n    flushAllText();\n    if (S.sel.has(id) && S.sel.size > 1) {\n      S.sel.delete(id);\n      if (S.primary === id) S.primary = selectedIds().pop();\n    } else {\n      S.sel.add(id);\n      S.primary = id;\n    }\n    S.anchor = id;\n    afterSelect({ noScroll: true });\n  }\n\n  function rangeSel(id, opt = {}) {\n    flushAllText();\n    const a = S.idx.get(S.anchor ?? id) ?? 0, b = S.idx.get(id);\n    const [lo, hi] = a < b ? [a, b] : [b, a];\n    S.sel = new Set(S.pages.slice(lo, hi + 1).map((p) => p.id));\n    S.primary = id;\n    afterSelect(opt);\n  }\n\n  function afterSelect(opt) {\n    S.detailWhich = null;\n    const p = S.byId.get(S.primary);\n    if (p) S.lastIdx = S.idx.get(p.id);\n    if (p && p.new && !opt.quiet) {\n      p.new = false;\n      const c = cards.get(p.id);\n      if (c) c.sig = null;\n      run(\'seen\', { ids: [p.id] }, { state: false });\n      renderBoard();\n    }\n    renderSelection();\n    if (!opt.noScroll) scrollToPrimary();\n  }\n\n  function scrollToPrimary() {\n    const c = cards.get(S.primary);\n    if (c && S.view === \'board\') c.el.scrollIntoView({ block: \'nearest\', inline: \'nearest\' });\n  }\n\n  function step(delta, opt = {}) {\n    if (!S.pages.length) return;\n    let i = S.idx.get(S.primary);\n    if (i == null) i = delta > 0 ? -1 : S.pages.length;\n    let j;\n    if (S.view === \'read\' && S.readMode === \'spread\') {\n      const groups = spreads();\n      const gi = groups.findIndex((g) => g.includes(i));\n      const g = groups[clamp(gi + delta, 0, groups.length - 1)];\n      j = g[0];\n    } else {\n      j = clamp(i + delta, 0, S.pages.length - 1);\n      if (S.onlyMatch && S.match) {\n        while (j >= 0 && j < S.pages.length && !S.match.has(S.pages[j].id)) j += Math.sign(delta);\n        if (j < 0 || j >= S.pages.length) return;\n      }\n    }\n    const field = opt.keepField ? document.activeElement?.dataset?.field : null;\n    if (opt.extend) rangeSel(S.pages[j].id); else selectOnly(S.pages[j].id);\n    if (field) {\n      const inp = $(`#detail [data-field="${field}"]`);\n      if (inp) { inp.focus(); const n = inp.value.length; try { inp.setSelectionRange(n, n); } catch (e) { /* なし */ } }\n    }\n  }\n\n  function rowStep(dir, extend) {\n    const c = cards.get(S.primary);\n    if (!c) return step(dir);\n    const r = c.el.getBoundingClientRect();\n    const cx = r.left + r.width / 2;\n    const i0 = S.idx.get(S.primary);\n    const list = dir > 0 ? S.pages.slice(i0 + 1) : S.pages.slice(0, i0).reverse();\n    let best = null, rowTop = null, bestDx = Infinity;\n    for (const p of list) {\n      const el = cards.get(p.id)?.el;\n      if (!el || !el.offsetParent) continue;\n      const rr = el.getBoundingClientRect();\n      if (Math.abs(rr.top - r.top) < 4) continue;\n      if (rowTop === null) rowTop = rr.top;\n      if (Math.abs(rr.top - rowTop) > 4) break;\n      const dx = Math.abs(rr.left + rr.width / 2 - cx);\n      if (dx < bestDx) { bestDx = dx; best = p; }\n    }\n    if (best) { if (extend) rangeSel(best.id); else selectOnly(best.id); }\n  }\n\n  function rowSize() {\n    const c = cards.get(S.primary);\n    if (!c) return 1;\n    const top = c.el.getBoundingClientRect().top;\n    let n = 0;\n    for (const p of S.pages) {\n      const el = cards.get(p.id)?.el;\n      if (el && Math.abs(el.getBoundingClientRect().top - top) < 4) n++;\n    }\n    return Math.max(1, n);\n  }\n\n  function gotoMatch(dir) {\n    if (!S.match || !S.match.size) return;\n    const list = S.pages.filter((p) => S.match.has(p.id));\n    const cur = S.idx.get(S.primary) ?? -1;\n    const target = dir > 0 ? (list.find((p) => p.n - 1 > cur) || list[0])\n      : ([...list].reverse().find((p) => p.n - 1 < cur) || list[list.length - 1]);\n    selectOnly(target.id);\n  }\n\n  function filterChanged() {\n    computeMatches();\n    if (S.match && S.match.size && !S.match.has(S.primary)) gotoMatch(1);\n    renderStrip();\n    renderChips();\n    renderToolbar();\n    renderBoard();\n  }\n\n  // ───────────── 文字の入力 ─────────────\n\n  function queueText(id, field, value) {\n    const k = id + \'|\' + field;\n    const prev = S.pendingText.get(k);\n    if (prev) clearTimeout(prev.t);\n    const p = S.byId.get(id);\n    if (p) p[field] = value;\n    S.pendingText.set(k, { id, field, value, t: setTimeout(() => flushText(k), 320) });\n  }\n\n  function flushText(k) {\n    const it = S.pendingText.get(k);\n    if (!it) return null;\n    clearTimeout(it.t);\n    S.pendingText.delete(k);\n    return run(\'text\', { id: it.id, field: it.field, value: it.value });\n  }\n\n  function flushAllText() { for (const k of [...S.pendingText.keys()]) flushText(k); }\n\n  // ───────────── 操作 ─────────────\n\n  function undo() { flushAllText(); run(\'undo\'); }\n  function redo() { flushAllText(); run(\'redo\'); }\n  function setStatus(st) { const ids = selectedIds(); if (ids.length) run(\'status\', { ids, status: st }); }\n  function move(how, extra = {}) { const ids = selectedIds(); if (ids.length) run(\'move\', { ids, how, ...extra }); }\n\n  async function moveToNumber() {\n    const ids = selectedIds();\n    if (!ids.length) return;\n    const v = await promptBox(`${ids.length}ページを何ページ目へ動かしますか？（1〜${S.pages.length}）`,\n      { value: String((S.idx.get(ids[0]) ?? 0) + 1), type: \'number\' });\n    if (v == null || v === \'\') return;\n    move(\'number\', { n: parseInt(v, 10) });\n  }\n\n  function insertBlank(where, wait = false) {\n    flushAllText();\n    run(\'blank\', { ids: selectedIds(), where, wait });\n  }\n\n  async function excludeSel() {\n    const ids = selectedIds();\n    if (!ids.length) return;\n    if (ids.length >= 10 && !(await confirmBox(`${ids.length}ページを除外します。ファイルはフォルダに残ります。`, { ok: \'除外する\', warn: true }))) return;\n    run(\'exclude\', { ids });\n  }\n\n  async function quarantineSel() {\n    const ids = selectedIds();\n    if (!ids.length) return;\n    const q = S.st.settings.quarantine_dir;\n    if (ids.length >= 10 && !(await confirmBox(`${ids.length}ページの絵を「${q}」フォルダへ移します。元に戻す（Ctrl+Z）で戻せます。`, { ok: \'隔離する\', danger: true }))) return;\n    run(\'quarantine\', { ids });\n  }\n\n  function deletePages() {\n    const ids = selectedIds().filter((id) => S.byId.get(id).kind !== \'image\');\n    if (!ids.length) { toast(\'消せるのは絵の無いページ（空白）だけです。絵のページは「除外」か「隔離」をお使いください\', \'error\'); return; }\n    run(\'delete\', { ids: selectedIds() });\n  }\n\n  function toggleWait() {\n    const ps = selectedIds().map((id) => S.byId.get(id)).filter((p) => p.kind !== \'image\');\n    if (!ps.length) { toast(\'待ち受けにできるのは、絵の無いページ（空白）だけです\', \'error\'); return; }\n    run(\'wait\', { ids: ps.map((p) => p.id), on: !ps.every((p) => p.wait) });\n  }\n\n  function setView(v) {\n    if (S.st.settings.view !== v) run(\'settings\', { patch: { view: v } });\n  }\n\n  function toggleView() { setView(S.st.settings.view === \'fin\' ? \'raw\' : \'fin\'); }\n\n  function openReader() {\n    if (!S.pages.length) return;\n    flushAllText();\n    if (!S.primary) selectOnly(S.pages[0].id);\n    if (S.sel.size > 1) selectOnly(S.primary);\n    if (S.readMode !== \'compare\') S.readMode = S.st.settings.spread ? \'spread\' : \'single\';\n    S.view = \'read\';\n    $(\'#reader\').hidden = false;\n    renderTop();\n    renderReader();\n  }\n\n  function closeReader() {\n    S.view = \'board\';\n    $(\'#reader\').hidden = true;\n    renderTop();\n    scrollToPrimary();\n  }\n\n  // ───────────── 読む ─────────────\n\n  function paneHTML(p, which, cur, side, cap) {\n    if (!p) return \'\';\n    if (p.kind !== \'image\') {\n      const t = p.kind === \'blank\' ? (p.wait ? \'<b>待ち受け</b>\' : \'<b>空白</b>ここに1ページ入ります\') : `<b>見つかりません</b><small>${esc(p.raw || p.fin)}</small>`;\n      return `<div class="r-pane ${side || \'\'}${cur ? \' cur\' : \'\'}" data-id="${p.id}"><div class="face">${t}</div>${cap ? `<span class="cap">${cap}</span>` : \'\'}</div>`;\n    }\n    const f = shownFile(p, which);\n    return `<div class="r-pane ${side || \'\'}${cur ? \' cur\' : \'\'}" data-id="${p.id}"><img src="${imgURL(f[1], f[2])}" alt="" draggable="false">${cap ? `<span class="cap">${cap}</span>` : \'\'}</div>`;\n  }\n\n  function renderReader() {\n    const p = S.byId.get(S.primary);\n    if (!p) { closeReader(); return; }\n    const i = S.idx.get(p.id);\n    const which = S.st.settings.view;\n    const stage = $(\'#r-stage\');\n    const mode = S.readMode;\n    stage.className = \'r-stage \' + mode;\n    let html = \'\';\n    const rtl = mode === \'spread\';\n    html += `<button class="r-nav prev" data-step="${rtl ? 1 : -1}" title="${rtl ? \'次へ（←）\' : \'前へ（←）\'}">‹</button>`;\n    html += `<button class="r-nav next" data-step="${rtl ? -1 : 1}" title="${rtl ? \'前へ（→）\' : \'次へ（→）\'}">›</button>`;\n    if (mode === \'spread\') {\n      const g = spreadOf(i);\n      const items = g.map((k) => S.pages[k]);\n      const panes = g.length === 2\n        ? paneHTML(items[1], which, items[1].id === p.id, \'left\') + paneHTML(items[0], which, items[0].id === p.id, \'right\')\n        : paneHTML(items[0], which, true, \'\');\n      html += panes;\n    } else if (mode === \'compare\' && p.kind === \'image\') {\n      html += paneHTML(p, \'raw\', false, \'\', p.raw_ok ? \'原画\' : \'原画なし\') + paneHTML(p, \'fin\', false, \'\', p.fin_ok ? \'完成\' : \'完成なし\');\n    } else {\n      html += paneHTML(p, which, false, \'\');\n    }\n    stage.innerHTML = html;\n    $(\'#r-pno\').innerHTML = `P.${pad(p.n)} <small>/ ${S.pages.length}</small>`;\n    const f = p.kind === \'image\' ? shownFile(p, which) : null;\n    $(\'#r-name\').textContent = f ? f[1] : (p.kind === \'blank\' ? \'空白\' : (p.raw || p.fin || \'\'));\n    $$(\'#r-which button\').forEach((b) => {\n      b.classList.toggle(\'on\', !!f && b.dataset.v === f[0]);\n      b.disabled = b.dataset.v === \'raw\' ? !p.raw_ok : !p.fin_ok;\n    });\n    $$(\'#r-mode button\').forEach((b) => b.classList.toggle(\'on\', b.dataset.m === mode));\n    $(\'#r-status\').innerHTML = statusButtons(p.status);\n    $(\'#r-hint\').innerHTML = mode === \'spread\'\n      ? \'見開き（右綴じ・右のページが先）\u3000<kbd>←</kbd> 次\u3000<kbd>→</kbd> 前\u3000<kbd>1</kbd>〜<kbd>5</kbd> 進行\u3000<kbd>X</kbd> 除外\u3000<kbd>Shift</kbd>+<kbd>X</kbd> 隔離\u3000<kbd>V</kbd> 原画/完成\u3000<kbd>Esc</kbd> 台割へ\'\n      : \'<kbd>→</kbd>／<kbd>PageDown</kbd> 次\u3000<kbd>←</kbd>／<kbd>PageUp</kbd> 前\u3000<kbd>1</kbd>〜<kbd>5</kbd> 進行\u3000<kbd>X</kbd> 除外\u3000<kbd>Shift</kbd>+<kbd>X</kbd> 隔離\u3000<kbd>V</kbd> 原画/完成\u3000<kbd>Esc</kbd> 台割へ\';\n    for (const k of [i - 2, i - 1, i + 1, i + 2, i + 3]) {\n      const q = S.pages[k];\n      if (q && q.kind === \'image\') { const ff = shownFile(q, which); const im = new Image(); im.src = imgURL(ff[1], ff[2]); }\n    }\n  }\n\n  // ───────────── 右クリック ─────────────\n\n  let subMenu = null;\n\n  function closeMenu() {\n    $(\'#menu\').hidden = true;\n    if (subMenu) { subMenu.remove(); subMenu = null; }\n  }\n\n  function menuItems() {\n    const ids = selectedIds();\n    const ps = ids.map((id) => S.byId.get(id));\n    const single = ps.length === 1 ? ps[0] : null;\n    const spread = S.st.settings.spread;\n    const fileless = ps.filter((p) => p.kind !== \'image\');\n    const q = S.st.settings.quarantine_dir;\n    const items = [{ status: true }];\n    items.push({ label: \'動かす\', sub: [\n      { label: \'先頭へ\', act: () => move(\'top\') },\n      { label: \'末尾へ\', act: () => move(\'end\') },\n      { label: \'1つ前へ\', key: spread ? \'Ctrl+→\' : \'Ctrl+←\', act: () => move(\'up\') },\n      { label: \'1つ後ろへ\', key: spread ? \'Ctrl+←\' : \'Ctrl+→\', act: () => move(\'down\') },\n      { sep: true },\n      { label: \'番号を指定…\', act: moveToNumber },\n    ] });\n    items.push({ label: \'空白\', sub: [\n      { label: \'前に挿入\', key: \'Shift+I\', act: () => insertBlank(\'before\') },\n      { label: \'後ろに挿入\', key: \'I\', act: () => insertBlank(\'after\') },\n      { label: \'後ろに待ち受けの空白を挿入\', act: () => insertBlank(\'after\', true) },\n      { sep: true },\n      { label: fileless.length && fileless.every((p) => p.wait) ? \'待ち受けをやめる\' : \'待ち受けにする\', disabled: !fileless.length || fileless.length !== ps.length, note: \'空白だけ\', act: toggleWait },\n      { label: \'ここに絵を入れる…\', disabled: !(single && single.kind !== \'image\'), act: () => openPicker(single.id) },\n      { label: \'空白を消す\', key: \'Del\', disabled: !fileless.length, act: deletePages },\n    ] });\n    items.push({ label: \'外す\', sub: [\n      { label: \'除外（その場に残す）\', cls: \'warn\', key: \'X\', note: \'ファイルはそのまま\', act: excludeSel },\n      { label: \'別フォルダへ隔離\', cls: \'danger\', key: \'Shift+X\', note: `「${q}」へファイル移動`, act: quarantineSel },\n      { sep: true },\n      { label: \'原画だけ外す（別ページへ）\', disabled: !(single && single.raw && single.fin), act: () => run(\'split\', { id: single.id, role: \'raw\' }) },\n      { label: \'完成だけ外す（別ページへ）\', disabled: !(single && single.raw && single.fin), act: () => run(\'split\', { id: single.id, role: \'fin\' }) },\n    ] });\n    const f = single && single.kind === \'image\' ? shownFile(single) : null;\n    items.push({ label: \'開く\', sub: [\n      { label: \'原寸で見る\', key: \'Enter\', disabled: !single, act: openReader },\n      { label: \'外部アプリで開く\', disabled: !f, act: () => run(\'external\', { rel: f[1] }, { state: false }) },\n      { label: \'フォルダで表示\', disabled: !f, act: () => run(\'reveal\', { rel: f[1] }, { state: false }) },\n    ] });\n    if (ps.length === 2 && ps.every((p) => p.kind === \'image\' && (!!p.raw + !!p.fin) === 1)) {\n      items.push({ sep: true });\n      items.push({ label: \'2枚を原画と完成の組にする\', act: () => run(\'pair\', { ids }) });\n    }\n    return { items, ps };\n  }\n\n  function buildMenu(el, items, ps) {\n    el.innerHTML = \'\';\n    for (const it of items) {\n      if (it.sep) { el.insertAdjacentHTML(\'beforeend\', \'<div class="msep"></div>\'); continue; }\n      if (it.status) {\n        const cur = new Set(ps.map((p) => p.status));\n        const row = document.createElement(\'div\');\n        row.className = \'m-status\';\n        row.innerHTML = STATUS.map(([k, l], i) => `<button data-st="${k}" class="${cur.size === 1 && cur.has(k) ? \'on\' : \'\'}" style="--c:${SCOLOR[k]}" title="${l}（${i + 1}）">${l}</button>`).join(\'\');\n        row.addEventListener(\'click\', (e) => { const b = e.target.closest(\'[data-st]\'); if (b) { closeMenu(); setStatus(b.dataset.st); } });\n        el.appendChild(row);\n        continue;\n      }\n      const b = document.createElement(\'button\');\n      b.className = \'mi\' + (it.cls ? \' \' + it.cls : \'\');\n      b.disabled = !!it.disabled;\n      b.innerHTML = `<span>${esc(it.label)}${it.note ? `<span class="sub-note">${esc(it.note)}</span>` : \'\'}</span>${it.sub ? \'<span class="arrow">▸</span>\' : (it.key ? `<span class="k">${esc(it.key)}</span>` : \'\')}`;\n      if (it.sub) {\n        const open = () => openSub(b, it.sub, ps);\n        b.addEventListener(\'mouseenter\', open);\n        b.addEventListener(\'click\', open);\n      } else {\n        b.addEventListener(\'mouseenter\', () => { if (el.id === \'menu\' && subMenu) { subMenu.remove(); subMenu = null; $$(\'#menu .mi.open\').forEach((x) => x.classList.remove(\'open\')); } });\n        b.addEventListener(\'click\', () => { closeMenu(); it.act(); });\n      }\n      el.appendChild(b);\n    }\n  }\n\n  function openSub(btn, sub, ps) {\n    $$(\'#menu .mi.open\').forEach((x) => x.classList.remove(\'open\'));\n    btn.classList.add(\'open\');\n    if (subMenu) subMenu.remove();\n    subMenu = document.createElement(\'div\');\n    subMenu.className = \'menu sub\';\n    buildMenu(subMenu, sub, ps);\n    document.body.appendChild(subMenu);\n    const r = btn.getBoundingClientRect();\n    const w = subMenu.offsetWidth, h = subMenu.offsetHeight;\n    let x = r.right + 4, y = r.top - 6;\n    if (x + w > innerWidth - 8) x = r.left - w - 4;\n    if (y + h > innerHeight - 8) y = innerHeight - h - 8;\n    subMenu.style.left = Math.max(8, x) + \'px\';\n    subMenu.style.top = Math.max(8, y) + \'px\';\n  }\n\n  function openMenu(x, y) {\n    const { items, ps } = menuItems();\n    if (!ps.length) return;\n    const el = $(\'#menu\');\n    buildMenu(el, items, ps);\n    el.hidden = false;\n    const w = el.offsetWidth, h = el.offsetHeight;\n    el.style.left = Math.max(8, Math.min(x, innerWidth - w - 8)) + \'px\';\n    el.style.top = Math.max(8, Math.min(y, innerHeight - h - 8)) + \'px\';\n  }\n\n  // ───────────── 窓（モーダル） ─────────────\n\n  function modal({ title, cls = \'\', foot = true, onClose }) {\n    const back = document.createElement(\'div\');\n    back.className = \'modal-back\';\n    back.innerHTML = `<div class="modal ${cls}"><div class="m-head"><h2>${esc(title)}</h2><button class="x" title="閉じる（Esc）">×</button></div><div class="m-body"></div>${foot ? \'<div class="m-foot"></div>\' : \'\'}</div>`;\n    $(\'#modal-root\').appendChild(back);\n    const m = { back, body: $(\'.m-body\', back), foot: $(\'.m-foot\', back), head: $(\'.m-head\', back) };\n    m.close = (v) => {\n      if (!back.isConnected) return;\n      back.remove();\n      S.modals = S.modals.filter((x) => x !== m);\n      if (onClose) onClose(v);\n    };\n    $(\'.x\', back).addEventListener(\'click\', () => m.close(null));\n    back.addEventListener(\'mousedown\', (e) => { if (e.target === back) m.close(null); });\n    S.modals.push(m);\n    closeMenu();\n    return m;\n  }\n\n  function confirmBox(text, opt = {}) {\n    return new Promise((resolve) => {\n      const m = modal({ title: opt.title || \'確認\', onClose: (v) => resolve(!!v) });\n      m.body.innerHTML = `<div class="msg-text">${esc(text)}</div>`;\n      m.foot.innerHTML = `<button class="btn" data-no>やめる</button><button class="btn ${opt.danger ? \'danger\' : opt.warn ? \'warn\' : \'primary\'}" data-ok>${esc(opt.ok || \'OK\')}</button>`;\n      $(\'[data-no]\', m.foot).onclick = () => m.close(false);\n      $(\'[data-ok]\', m.foot).onclick = () => m.close(true);\n      m.enter = () => m.close(true);\n      setTimeout(() => $(\'[data-ok]\', m.foot).focus(), 0);\n    });\n  }\n\n  function promptBox(text, opt = {}) {\n    return new Promise((resolve) => {\n      const m = modal({ title: opt.title || \'入力\', onClose: (v) => resolve(v ?? null) });\n      m.body.innerHTML = `<div class="msg-text" style="margin-bottom:10px">${esc(text)}</div><input style="width:100%" type="${opt.type || \'text\'}" ${opt.type === \'number\' ? `min="1" max="${S.pages.length}"` : \'\'}>`;\n      const inp = $(\'input\', m.body);\n      inp.value = opt.value || \'\';\n      m.foot.innerHTML = \'<button class="btn" data-no>やめる</button><button class="btn primary" data-ok>OK</button>\';\n      $(\'[data-no]\', m.foot).onclick = () => m.close(null);\n      $(\'[data-ok]\', m.foot).onclick = () => m.close(inp.value);\n      m.enter = () => m.close(inp.value);\n      setTimeout(() => { inp.focus(); inp.select(); }, 0);\n    });\n  }\n\n  function openPicker(blankId) {\n    const b = S.byId.get(blankId);\n    if (!b) return;\n    const m = modal({ title: `P.${pad(b.n)} に入れる絵を選ぶ`, cls: \'wide\', foot: true });\n    const cands = S.pages.filter((p) => p.kind === \'image\' && p.id !== blankId)\n      .sort((a, c) => (c.new - a.new) || (a.n - c.n));\n    const ex = S.st.excluded.filter((e) => e.ok.some(Boolean));\n    m.body.innerHTML = `<div style="display:flex;gap:10px;align-items:center;margin-bottom:12px"><input id="pk-q" placeholder="名前・セリフで絞り込む" style="flex:1"><span style="color:var(--txt-faint);font-size:12px">新着が先頭。選んだ絵のページはここへ移ります</span></div><div class="tile-grid" id="pk-grid"></div>`;\n    const grid = $(\'#pk-grid\', m.body);\n    const draw = () => {\n      const q = $(\'#pk-q\', m.body).value.trim().toLowerCase();\n      const ok = (s) => !q || s.toLowerCase().includes(q);\n      let html = \'\';\n      for (const p of cands) {\n        const f = shownFile(p);\n        if (!ok([p.raw, p.fin, p.lines, p.scene].join(\' \'))) continue;\n        html += `<button class="tile" data-src="${p.id}"><div class="tp"><img loading="lazy" src="${thumbURL(f[1], f[2])}" alt=""></div><div class="tc">${p.new ? \'<span class="tag new">新着</span>\' : \'\'}<b>P.${pad(p.n)}</b><span class="fn">${esc(f[1])}</span></div></button>`;\n      }\n      for (const e of ex) {\n        const k = e.ok.findIndex(Boolean);\n        if (!ok(e.files.join(\' \') + \' \' + e.text)) continue;\n        html += `<button class="tile" data-ex="${e.id}"><div class="tp"><img loading="lazy" src="${thumbURL(e.files[k], e.v[k])}" alt=""></div><div class="tc"><span class="tag">除外中</span><span class="fn">${esc(e.files[k])}</span></div></button>`;\n      }\n      grid.innerHTML = html || \'<div class="list-empty">入れられる絵がありません</div>\';\n    };\n    draw();\n    $(\'#pk-q\', m.body).addEventListener(\'input\', draw);\n    grid.addEventListener(\'click\', (e) => {\n      const t = e.target.closest(\'.tile\');\n      if (!t) return;\n      m.close();\n      if (t.dataset.src) run(\'fill\', { blank: blankId, src: t.dataset.src });\n      else run(\'fill\', { blank: blankId, excluded: t.dataset.ex });\n    });\n    m.foot.innerHTML = \'<span class="left">フォルダの外の絵は、先にエクスプローラで画像フォルダへコピーしてください（待ち受けにしておけば自動で入ります）</span><button class="btn" data-no>閉じる</button>\';\n    $(\'[data-no]\', m.foot).onclick = () => m.close();\n    setTimeout(() => $(\'#pk-q\', m.body).focus(), 0);\n  }\n\n  function openHelp() {\n    const m = modal({ title: \'キー操作\', cls: \'mid\' });\n    const k = (s) => s.split(\'+\').map((x) => `<kbd>${esc(x)}</kbd>`).join(\'+\');\n    const rows = [\n      [\'h\', \'ページを送る\'], [\'PageDown／PageUp\', \'次／前のページ（入力中でも効く。同じ欄のまま次のページへ）\'],\n      [\'→／←\', \'→ が次、← が前。見開き表示のときだけ、右綴じに合わせて ← が次\'], [\'↑／↓\', \'上下の段へ\'], [\'Home／End\', \'最初／最後のページ\'],\n      [\'h\', \'選ぶ\'], [\'クリック\', \'1枚を選ぶ\'], [\'Shift+クリック\', \'範囲を選ぶ（Shift+矢印でも）\'], [\'Ctrl+クリック\', \'1枚ずつ足す・外す\'], [\'Ctrl+A\', \'すべて選ぶ\'],\n      [\'h\', \'ページを変える\'], [\'1〜5\', \'進行（未着手・画像のみ・セリフ入・完成・要修正）\'], [\'Ctrl+矢印\', \'ページを動かす\'],\n      [\'I／Shift+I\', \'後ろ／前に空白\'], [\'Delete\', \'空白を消す\'], [\'X\', \'除外（ファイルはそのまま）\'], [\'Shift+X\', \'隔離（「未使用」へ移す）\'],\n      [\'h\', \'見る・探す\'], [\'Enter\', \'原寸で開く（読む）\'], [\'V\', \'原画と完成の切り替え\'], [\'/\', \'検索\'], [\'Esc\', \'入力を抜ける・閉じる\'],\n      [\'h\', \'取り消す\'], [\'Ctrl+Z\', \'元に戻す\'], [\'Ctrl+Y\', \'やり直し\'],\n    ];\n    m.body.innerHTML = `<div class="help">${rows.map(([a, b]) => a === \'h\' ? `<div class="h">${esc(b)}</div>` : `<div>${a.split(\'／\').map(k).join(\'／\')}</div><div>${esc(b)}</div>`).join(\'\')}</div>`;\n    m.foot.innerHTML = \'<span class="left">右クリックでも同じ操作ができます。ドラッグで並べ替え、空白の上に落とすとその空白へ入ります。</span><button class="btn primary" data-ok>閉じる</button>\';\n    $(\'[data-ok]\', m.foot).onclick = () => m.close();\n    m.enter = () => m.close();\n  }\n\n  // ───────────── 設定 ─────────────\n\n  function openSettings(tab = \'view\') {\n    const m = modal({ title: \'設定\', cls: \'wide\' });\n    const tabs = [[\'view\', \'表示\'], [\'pair\', \'完成の見分け\'], [\'quar\', \'隔離した絵\'], [\'excl\', \'除外した絵\'], [\'backup\', \'バックアップ\'], [\'other\', \'そのほか\']];\n    const tabBar = document.createElement(\'div\');\n    tabBar.className = \'m-tabs\';\n    tabBar.innerHTML = tabs.map(([k, l]) => `<button data-tab="${k}">${l}</button>`).join(\'\');\n    m.head.after(tabBar);\n    m.foot.innerHTML = \'<span class="left">変えた設定はすぐに保存されます</span><button class="btn primary" data-ok>閉じる</button>\';\n    $(\'[data-ok]\', m.foot).onclick = () => m.close();\n    const set = (patch) => run(\'settings\', { patch }).then(() => draw());\n    const draw = () => {\n      if (!m.back.isConnected) return;\n      const s = S.st.settings;\n      $$(\'button\', tabBar).forEach((b) => b.classList.toggle(\'on\', b.dataset.tab === tab));\n      const B = m.body;\n      if (tab === \'view\') {\n        B.innerHTML = `\n          <div class="sect">一覧</div>\n          <div class="form-row"><label>見せる絵</label><div class="inline"><label class="radio"><input type="radio" name="view" value="fin">完成を優先</label><label class="radio"><input type="radio" name="view" value="raw">原画を優先</label></div></div>\n          <div class="form-row"><label>サムネイルの大きさ</label><div class="inline"><input type="range" min="100" max="360" step="4" id="st-zoom"><span class="mono" id="st-zoomv"></span></div></div>\n          <div class="sect">見開き（確認用・右綴じ）</div>\n          <div class="form-row"><label>見開きで並べる</label><div class="inline"><label class="check"><input type="checkbox" id="st-spread">右のページが先（右綴じ）</label></div></div>\n          <div class="form-row"><label>1ページ目</label><div class="inline"><label class="check"><input type="checkbox" id="st-cover">単独にする（表紙）</label></div><div class="note">オフにすると、1・2ページ目から見開きになります。</div></div>`;\n        $$(\'input[name="view"]\', B).forEach((r) => { r.checked = r.value === s.view; r.onchange = () => set({ view: r.value }); });\n        const z = $(\'#st-zoom\', B); z.value = s.thumb_size; $(\'#st-zoomv\', B).textContent = s.thumb_size + \'px\';\n        z.oninput = () => { $(\'#st-zoomv\', B).textContent = z.value + \'px\'; };\n        z.onchange = () => set({ thumb_size: +z.value });\n        $(\'#st-spread\', B).checked = s.spread; $(\'#st-spread\', B).onchange = (e) => set({ spread: e.target.checked });\n        $(\'#st-cover\', B).checked = s.cover_single; $(\'#st-cover\', B).onchange = (e) => set({ cover_single: e.target.checked });\n      } else if (tab === \'pair\') {\n        B.innerHTML = `\n          <p class="d-note">次のどれかに当たる絵を、同じ名前のページの「完成」として組み込みます。名前の変わった絵は、絵の中身（画素の指紋）で追いかけます。</p>\n          <div class="form-row"><label>台詞入りフォルダ</label><input id="st-findir" spellcheck="false"><div class="note">台詞工房のフォルダ作業の保存先。ここにある同じ名前の絵を完成にします。</div></div>\n          <div class="form-row"><label>_V番号</label><div class="inline"><label class="check"><input type="checkbox" id="st-v">名前_V2・_V3 を完成にする（大きい番号が新しい。前の版は「旧版」へ）</label></div></div>\n          <div class="form-row"><label>末尾の決まり</label><input id="st-suf" spellcheck="false"><div class="note">空白かカンマで区切って並べます。例：_s _serif _fin</div></div>\n          <div class="form-row"><label>自動で組にする</label><div class="inline"><label class="check"><input type="checkbox" id="st-auto">フォルダに来た完成を、同じ名前のページへ入れる</label></div></div>\n          <div class="form-row"><label>進行</label><div class="inline"><label class="check"><input type="checkbox" id="st-astat">完成が来たら「セリフ入」にする（未着手・画像のみのとき）</label></div></div>`;\n        const fd = $(\'#st-findir\', B); fd.value = s.fin_dir; fd.onchange = () => set({ fin_dir: fd.value.trim() });\n        $(\'#st-v\', B).checked = s.use_vsave; $(\'#st-v\', B).onchange = (e) => set({ use_vsave: e.target.checked });\n        const sf = $(\'#st-suf\', B); sf.value = s.fin_suffixes.join(\' \'); sf.onchange = () => set({ fin_suffixes: sf.value.split(/[\\s,、]+/).filter(Boolean) });\n        $(\'#st-auto\', B).checked = s.auto_pair; $(\'#st-auto\', B).onchange = (e) => set({ auto_pair: e.target.checked });\n        $(\'#st-astat\', B).checked = s.auto_status; $(\'#st-astat\', B).onchange = (e) => set({ auto_status: e.target.checked });\n      } else if (tab === \'quar\') {\n        const items = S.st.quarantine;\n        B.innerHTML = `<p class="d-note">「${esc(s.quarantine_dir)}」フォルダの絵です（台割工房で隔離した絵・番付工房や手で移した絵）。戻すと台割に入り、隔離したときのセリフとメモも戻ります。</p>\n          <div class="form-row"><label>隔離フォルダの名前</label><input id="st-q" spellcheck="false"><div class="note">番付工房と同じ「未使用」にしておくと、どちらで外した絵も一か所に集まります。</div></div>\n          <div style="display:flex;align-items:center;gap:8px;margin:6px 0 10px"><b style="font-size:12.5px">${items.length} 枚</b>${items.length ? \'<button class="btn small" id="q-all">すべて戻す</button>\' : \'\'}</div>\n          <div class="tile-grid" id="q-grid">${items.map((q) => `<div class="tile static"><div class="tp"><img loading="lazy" src="${thumbURL(q.q, q.v)}" alt=""></div><div class="tc"><span class="fn" title="${esc(q.q)}">${esc(q.q.split(\'/\').pop())}</span>${q.orig ? `<span class="fn" style="color:var(--txt-faint)">元：${esc(q.orig)}</span>` : (q.legacy ? \'<span class="tag">以前の隔離</span>\' : \'<span class="tag">ほかで外した絵</span>\')}${q.text ? `<span class="fn" style="color:var(--txt-faint)">${esc(q.text)}</span>` : \'\'}<div class="act"><button class="btn small" data-restore="${esc(q.q)}">戻す</button></div></div></div>`).join(\'\') || \'<div class="list-empty">隔離した絵はありません</div>\'}</div>`;\n        const qi = $(\'#st-q\', B); qi.value = s.quarantine_dir; qi.onchange = () => set({ quarantine_dir: qi.value.trim() });\n        B.onclick = (e) => {\n          const r = e.target.closest(\'[data-restore]\');\n          if (r) run(\'unquarantine\', { items: [r.dataset.restore], after: S.primary }).then(draw);\n          if (e.target.id === \'q-all\') run(\'unquarantine\', { items: items.map((q) => q.q), after: S.primary }).then(draw);\n        };\n      } else if (tab === \'excl\') {\n        const items = S.st.excluded;\n        B.onclick = null;\n        B.innerHTML = `<p class="d-note">除外したページです。ファイルはフォルダに残っています。戻すと、いま選んでいるページの後ろに入ります（選んでいなければ元の場所）。</p>\n          <div class="tile-grid">${items.map((e) => { const k = e.ok.findIndex(Boolean); return `<div class="tile static"><div class="tp">${k >= 0 ? `<img loading="lazy" src="${thumbURL(e.files[k], e.v[k])}" alt="">` : \'<span style="color:var(--txt-faint);font-size:11px">見つかりません</span>\'}</div><div class="tc"><span class="fn">${esc(e.files.join(\' ／ \'))}</span>${e.text ? `<span class="fn" style="color:var(--txt-faint)">${esc(e.text)}</span>` : \'\'}<div class="act"><button class="btn small" data-unex="${e.id}">戻す</button></div></div></div>`; }).join(\'\') || \'<div class="list-empty">除外したページはありません</div>\'}</div>`;\n        B.onclick = (e) => { const r = e.target.closest(\'[data-unex]\'); if (r) run(\'unexclude\', { ids: [r.dataset.unex], after: S.primary }).then(draw); };\n      } else if (tab === \'backup\') {\n        B.onclick = null;\n        B.innerHTML = \'<p class="d-note">台割.daiwari の自動バックアップです（直近20回分と、日ごとの7日分）。戻しても、元に戻す（Ctrl+Z）で今の状態へ帰れます。</p><div class="bk-list" id="bk">読み込み中…</div>\';\n        request(\'/api/backups\').then((d) => {\n          const el = $(\'#bk\', B);\n          if (!el) return;\n          if (!d.ok || !d.items.length) { el.innerHTML = \'<div class="list-empty">まだバックアップはありません</div>\'; return; }\n          el.innerHTML = d.items.map((b) => `<div class="bk"><div><div class="when">${esc(b.label)}</div><div class="info">${b.pages == null ? \'読めません\' : `${b.pages}ページ・完成 ${b.done}${b.title ? \'・\' + esc(b.title) : \'\'}`}</div></div><span></span><button class="btn small" data-bk="${esc(b.name)}" ${b.pages == null ? \'disabled\' : \'\'}>この時点に戻す</button></div>`).join(\'\');\n          el.onclick = async (e) => {\n            const r = e.target.closest(\'[data-bk]\');\n            if (!r) return;\n            if (await confirmBox(\'この時点の台割に戻しますか？（元に戻す で今の状態へ帰れます）\', { ok: \'戻す\' })) run(\'restore_backup\', { name: r.dataset.bk });\n          };\n        });\n      } else {\n        B.onclick = null;\n        B.innerHTML = `\n          <div class="form-row"><label>外部アプリ</label><input id="st-ext" spellcheck="false" placeholder="空なら Windows の既定のアプリ"><div class="note">右クリック →「開く」→「外部アプリで開く」で使います。例："C:\\\\Program Files\\\\App\\\\app.exe" "{file}"（{file} が絵の場所に置き換わります）</div></div>\n          <div class="form-row"><label>字数の目安</label><div class="inline"><input type="number" id="st-warn" min="10" max="2000">字／ページ（超えると字数が赤くなります）</div></div>\n          <div class="sect">非常用</div>\n          <div class="form-row"><label>読み直し</label><div class="inline"><button class="btn small" id="st-rescan">フォルダを読み直す</button></div><div class="note">ふだんはフォルダを自動で見張っています。表示が合わないときだけ押してください。</div></div>\n          <div class="form-row"><label>記録</label><div class="mono" style="font-size:12px;color:var(--txt-dim);user-select:text">${esc(S.st.folder)}\\\\.daiwari\\\\log.txt</div></div>\n          <div class="form-row"><label>版</label><div class="mono" style="font-size:12px;color:var(--txt-dim)">台割工房 ${esc(S.st.version)}</div></div>`;\n        const ex = $(\'#st-ext\', B); ex.value = s.external_cmd; ex.onchange = () => set({ external_cmd: ex.value.trim() });\n        const w = $(\'#st-warn\', B); w.value = s.chars_warn; w.onchange = () => set({ chars_warn: +w.value || 120 });\n        $(\'#st-rescan\', B).onclick = () => run(\'rescan\');\n      }\n    };\n    tabBar.addEventListener(\'click\', (e) => { const b = e.target.closest(\'[data-tab]\'); if (b) { tab = b.dataset.tab; m.body.onclick = null; draw(); } });\n    draw();\n    m.redraw = draw;\n  }\n\n  // ───────────── 書き出し ─────────────\n\n  function exportPreview(o) {\n    const lines = [];\n    let n = o.start, shown = 0;\n    const total = S.pages.length;\n    S.pages.forEach((p, i) => {\n      let src = null;\n      if (o.use === \'raw\') src = p.raw_ok ? p.raw : (p.fin_ok ? p.fin : null);\n      else if (o.use === \'finonly\') src = p.fin_ok ? p.fin : null;\n      else src = p.fin_ok ? p.fin : (p.raw_ok ? p.raw : null);\n      const num = String(n).padStart(o.pad, \'0\');\n      const show = shown < 6 || i >= total - 2;\n      if (!src) {\n        if (show) lines.push(`P.${pad(p.n)}  （${p.kind === \'image\' ? \'完成なし\' : \'空白\'}${o.blank === \'skip\' ? `・${num} を飛ばす` : \'・詰める\'}）`);\n        if (o.blank === \'skip\') n++;\n      } else {\n        const ext = src.slice(src.lastIndexOf(\'.\')).toLowerCase();\n        if (show) lines.push(`P.${pad(p.n)}  ${src}  →  ${o.prefix}${num}${ext}`);\n        n++;\n      }\n      if (show) shown++;\n      else if (shown === 6) { lines.push(\'\u3000…\'); shown++; }\n    });\n    return lines.join(\'\\n\');\n  }\n\n  function openExport() {\n    flushAllText();\n    const s = S.st.settings;\n    const m = modal({ title: \'書き出し\', cls: \'mid\' });\n    const o = { use: s.export_use, prefix: s.export_prefix || \'\', pad: s.export_pad, start: s.export_start, blank: s.export_blank,\n      strip: s.export_strip, script: s.export_script, memo: s.export_memo, dest: null };\n    m.body.innerHTML = `\n      <p class="d-note">台割の順に、別のフォルダへ連番でコピーします。元の画像には触りません。</p>\n      <div class="form-row"><label>使う絵</label><div class="inline"><label class="radio"><input type="radio" name="use" value="fin">完成を優先（無ければ原画）</label><label class="radio"><input type="radio" name="use" value="raw">原画</label><label class="radio"><input type="radio" name="use" value="finonly">完成だけ</label></div></div>\n      <div class="form-row"><label>名前</label><div class="inline"><input id="ex-prefix" placeholder="頭の文字（空でもよい）" style="width:200px" spellcheck="false">＋番号<input type="number" id="ex-pad" min="1" max="6" title="桁数">桁\u3000開始<input type="number" id="ex-start" min="0" max="99999"></div></div>\n      <div class="form-row"><label>空白・絵の無いページ</label><div class="inline"><label class="radio"><input type="radio" name="blank" value="skip">番号を飛ばす</label><label class="radio"><input type="radio" name="blank" value="close">詰める</label></div></div>\n      <div class="form-row"><label>メタデータ</label><div class="inline"><label class="check"><input type="checkbox" id="ex-strip">生成情報（プロンプトなど）を消す</label></div><div class="note">絵のデータはそのまま、PNG・JPEG・WebP の文字情報だけを除きます。</div></div>\n      <div class="form-row"><label>台本</label><div class="inline"><label class="check"><input type="checkbox" id="ex-script">台本.txt も書き出す</label><label class="check"><input type="checkbox" id="ex-memo">メモと修正指示も入れる</label></div></div>\n      <div class="form-row"><label>書き出し先</label><div class="inline"><span id="ex-dest" class="mono" style="font-size:12px;color:var(--txt-dim)"></span><button class="btn small" id="ex-pick">フォルダを選ぶ…</button><button class="btn small ghost" id="ex-reset" hidden>元に戻す</button></div><div class="note">選ばなければ、画像フォルダの中の「書き出し」フォルダに、日時つきのフォルダを作ります。</div></div>\n      <div class="sect">変わる名前（はじめの数枚）</div>\n      <div class="preview-names" id="ex-prev"></div>\n      <div id="ex-progress" hidden style="margin-top:12px"><div class="progress"><i></i></div><div style="font-size:12px;color:var(--txt-dim);margin-top:6px" id="ex-ptext"></div></div>`;\n    const B = m.body;\n    const sync = () => {\n      o.use = $(\'input[name="use"]:checked\', B).value;\n      o.blank = $(\'input[name="blank"]:checked\', B).value;\n      o.prefix = $(\'#ex-prefix\', B).value.replace(/[\\\\/:*?"<>|]/g, \'\');\n      o.pad = clamp(parseInt($(\'#ex-pad\', B).value, 10) || 3, 1, 6);\n      o.start = clamp(parseInt($(\'#ex-start\', B).value, 10) || 0, 0, 99999);\n      o.strip = $(\'#ex-strip\', B).checked;\n      o.script = $(\'#ex-script\', B).checked;\n      o.memo = $(\'#ex-memo\', B).checked;\n      $(\'#ex-memo\', B).disabled = !o.script;\n      $(\'#ex-prev\', B).textContent = exportPreview(o);\n      $(\'#ex-dest\', B).textContent = o.dest ? o.dest + \'\\\\…\' : `${S.st.folder_name}\\\\書き出し\\\\…`;\n      $(\'#ex-reset\', B).hidden = !o.dest;\n    };\n    $$(\'input[name="use"]\', B).forEach((r) => { r.checked = r.value === o.use; });\n    $$(\'input[name="blank"]\', B).forEach((r) => { r.checked = r.value === o.blank; });\n    $(\'#ex-prefix\', B).value = o.prefix;\n    $(\'#ex-pad\', B).value = o.pad;\n    $(\'#ex-start\', B).value = o.start;\n    $(\'#ex-strip\', B).checked = o.strip;\n    $(\'#ex-script\', B).checked = o.script;\n    $(\'#ex-memo\', B).checked = o.memo;\n    B.addEventListener(\'input\', sync);\n    B.addEventListener(\'change\', sync);\n    $(\'#ex-pick\', B).onclick = async () => {\n      const d = await api(\'pick_folder\', {}, { state: false }).catch(() => null);\n      if (d && d.result) { o.dest = d.result; sync(); }\n    };\n    $(\'#ex-reset\', B).onclick = () => { o.dest = null; sync(); };\n    sync();\n    m.foot.innerHTML = \'<span class="left" id="ex-left"></span><button class="btn" data-no>閉じる</button><button class="btn primary" data-go>書き出す</button>\';\n    $(\'[data-no]\', m.foot).onclick = () => m.close();\n    $(\'[data-go]\', m.foot).onclick = async () => {\n      sync();\n      const go = $(\'[data-go]\', m.foot);\n      go.disabled = true;\n      run(\'settings\', { patch: { export_use: o.use, export_prefix: o.prefix, export_pad: o.pad, export_start: o.start, export_blank: o.blank, export_strip: o.strip, export_script: o.script, export_memo: o.memo } }, { state: false });\n      $(\'#ex-progress\', B).hidden = false;\n      S.exportBox = B;\n      try {\n        const d = await request(\'/api/export\', { method: \'POST\', body: JSON.stringify({ opts: o }) });\n        if (!d.ok) { toast(d.error || \'書き出せませんでした\', \'error\'); go.disabled = false; return; }\n        const r = d.result;\n        $(\'#ex-ptext\', B).textContent = `${r.count}枚を書き出しました${r.skipped ? `（飛ばしたページ ${r.skipped}）` : \'\'}${o.strip ? `。メタデータを消した絵 ${r.stripped}枚` : \'\'}。`;\n        $(\'.progress i\', B).style.width = \'100%\';\n        $(\'#ex-left\', m.foot).textContent = r.dest;\n        go.textContent = \'フォルダを開く\';\n        go.disabled = false;\n        go.onclick = () => run(\'open_dest\', { path: r.dest }, { state: false });\n      } catch (e) {\n        toast(\'書き出せませんでした\', \'error\');\n        go.disabled = false;\n      } finally {\n        S.exportBox = null;\n      }\n    };\n  }\n\n  function exportProgress(d) {\n    const B = S.exportBox;\n    if (!B) return;\n    $(\'.progress i\', B).style.width = Math.round((d.done / Math.max(1, d.total)) * 100) + \'%\';\n    $(\'#ex-ptext\', B).textContent = `書き出し中 ${d.done}/${d.total}`;\n  }\n\n  // ───────────── 知らせ ─────────────\n\n  function toast(text, kind = \'info\', action = null) {\n    const box = $(\'#toasts\');\n    const el = document.createElement(\'div\');\n    el.className = \'toast \' + kind;\n    el.innerHTML = `<span>${esc(text)}</span>`;\n    if (action) {\n      const b = document.createElement(\'button\');\n      b.textContent = action.label;\n      b.onclick = () => { el.remove(); action.fn(); };\n      el.appendChild(b);\n    }\n    box.appendChild(el);\n    while (box.children.length > 4) box.firstChild.remove();\n    setTimeout(() => el.remove(), action ? 7000 : kind === \'error\' ? 6500 : 3400);\n  }\n\n  // ───────────── ドラッグで並べ替え ─────────────\n\n  let drag = null;\n\n  function nearestCard(x, y) {\n    let best = null, bd = Infinity;\n    const wr = $(\'#board-wrap\').getBoundingClientRect();\n    for (const c of cards.values()) {\n      if (!c.el.offsetParent) continue;\n      const r = c.el.getBoundingClientRect();\n      if (r.bottom < wr.top || r.top > wr.bottom) continue;\n      const dx = x < r.left ? r.left - x : x > r.right ? x - r.right : 0;\n      const dy = y < r.top ? r.top - y : y > r.bottom ? y - r.bottom : 0;\n      const d = dx * dx + dy * dy * 4;\n      if (d < bd) { bd = d; best = c.el; }\n    }\n    return best;\n  }\n\n  function dropTarget(e) {\n    let el = document.elementFromPoint(e.clientX, e.clientY)?.closest(\'.card\');\n    if (!el) el = nearestCard(e.clientX, e.clientY);\n    if (!el) return null;\n    const id = el.dataset.id;\n    if (drag.ids.includes(id)) return null;\n    const p = S.byId.get(id);\n    const r = el.getBoundingClientRect();\n    if (drag.single && p.kind !== \'image\' && S.byId.get(drag.single)?.kind === \'image\') {\n      const inner = e.clientX > r.left + r.width * 0.2 && e.clientX < r.right - r.width * 0.2\n        && e.clientY > r.top + r.height * 0.12 && e.clientY < r.bottom - r.height * 0.12;\n      if (inner) return { fill: true, id, el };\n    }\n    const rtl = S.st.settings.spread;\n    const left = e.clientX < r.left + r.width / 2;\n    const after = rtl ? left : !left;\n    const i = S.idx.get(id);\n    let before = id;\n    if (after) {\n      let j = i + 1;\n      while (j < S.pages.length && drag.ids.includes(S.pages[j].id)) j++;\n      before = j < S.pages.length ? S.pages[j].id : null;\n    }\n    return { fill: false, before, el, after, r, rtl };\n  }\n\n  function showDrop(t) {\n    $$(\'.card.fill-target\').forEach((x) => x.classList.remove(\'fill-target\'));\n    const mark = $(\'#drop-mark\');\n    if (!t) { mark.style.display = \'none\'; return; }\n    if (t.fill) { t.el.classList.add(\'fill-target\'); mark.style.display = \'none\'; return; }\n    const wrap = $(\'#board-wrap\');\n    const wr = wrap.getBoundingClientRect();\n    const onLeft = t.rtl ? t.after : !t.after;\n    const x = onLeft ? t.r.left - 8 : t.r.right + 4;\n    mark.style.display = \'block\';\n    mark.style.left = (x - wr.left + wrap.scrollLeft) + \'px\';\n    mark.style.top = (t.r.top - wr.top + wrap.scrollTop) + \'px\';\n    mark.style.height = t.r.height + \'px\';\n  }\n\n  function endDrag() {\n    if (drag) drag.ids.forEach((id) => cards.get(id)?.el.classList.remove(\'dragging\'));\n    drag = null;\n    showDrop(null);\n  }\n\n  // ───────────── つなぐ ─────────────\n\n  function bind() {\n    const board = $(\'#board\'), wrap = $(\'#board-wrap\');\n\n    board.addEventListener(\'error\', (e) => { if (e.target.tagName === \'IMG\') e.target.classList.add(\'broken\'); }, true);\n\n    board.addEventListener(\'mousedown\', (e) => {\n      const c = e.target.closest(\'.card\');\n      if (!c || e.button !== 0) return;\n      const id = c.dataset.id;\n      if (e.shiftKey) { e.preventDefault(); rangeSel(id, { noScroll: true }); }\n      else if (e.ctrlKey || e.metaKey) { e.preventDefault(); toggleSel(id); }\n      else if (!S.sel.has(id) || S.sel.size === 1) selectOnly(id, { noScroll: true });\n      else S.pendingClick = id;\n    });\n    board.addEventListener(\'click\', (e) => {\n      const c = e.target.closest(\'.card\');\n      if (c && S.pendingClick === c.dataset.id && !e.shiftKey && !e.ctrlKey) selectOnly(c.dataset.id, { noScroll: true });\n      S.pendingClick = null;\n    });\n    board.addEventListener(\'dblclick\', (e) => { if (e.target.closest(\'.card\')) openReader(); });\n    wrap.addEventListener(\'contextmenu\', (e) => {\n      const c = e.target.closest(\'.card\');\n      if (!c) return;\n      e.preventDefault();\n      if (!S.sel.has(c.dataset.id)) selectOnly(c.dataset.id, { noScroll: true });\n      openMenu(e.clientX, e.clientY);\n    });\n    wrap.addEventListener(\'mousedown\', (e) => {\n      if (e.target === wrap || e.target === board) { if (!e.shiftKey && !e.ctrlKey && S.sel.size > 1) selectOnly(S.primary, { noScroll: true }); }\n    });\n    wrap.addEventListener(\'wheel\', (e) => {\n      if (!e.ctrlKey) return;\n      e.preventDefault();\n      const v = clamp((S.zoomLive || S.st.settings.thumb_size) + (e.deltaY < 0 ? 8 : -8), 100, 360);\n      applyZoom(v);\n    }, { passive: false });\n\n    // ドラッグ\n    board.addEventListener(\'dragstart\', (e) => {\n      const c = e.target.closest(\'.card\');\n      if (!c) return;\n      const id = c.dataset.id;\n      if (!S.sel.has(id)) selectOnly(id, { noScroll: true });\n      const ids = selectedIds();\n      drag = { ids, single: ids.length === 1 ? ids[0] : null };\n      e.dataTransfer.effectAllowed = \'move\';\n      e.dataTransfer.setData(\'text/x-daiwari\', ids.join(\',\'));\n      const g = document.createElement(\'div\');\n      g.className = \'drag-ghost\';\n      g.textContent = ids.length > 1 ? `${ids.length}ページ` : \'P.\' + pad(S.byId.get(id).n);\n      document.body.appendChild(g);\n      e.dataTransfer.setDragImage(g, 12, 12);\n      setTimeout(() => g.remove(), 0);\n      requestAnimationFrame(() => ids.forEach((i) => cards.get(i)?.el.classList.add(\'dragging\')));\n    });\n    wrap.addEventListener(\'dragover\', (e) => {\n      if (!drag) {\n        if ([...e.dataTransfer.types].includes(\'Files\')) { e.preventDefault(); e.dataTransfer.dropEffect = \'none\'; }\n        return;\n      }\n      e.preventDefault();\n      e.dataTransfer.dropEffect = \'move\';\n      const wr = wrap.getBoundingClientRect();\n      if (e.clientY < wr.top + 56) wrap.scrollTop -= 16;\n      else if (e.clientY > wr.bottom - 56) wrap.scrollTop += 16;\n      S.dropT = dropTarget(e);\n      showDrop(S.dropT);\n    });\n    wrap.addEventListener(\'dragleave\', (e) => { if (e.target === wrap) showDrop(null); });\n    wrap.addEventListener(\'drop\', (e) => {\n      if (!drag) {\n        if ([...e.dataTransfer.types].includes(\'Files\')) {\n          e.preventDefault();\n          toast(\'外の絵を落として取り込むのは次の版で入ります。いまはエクスプローラで画像フォルダへコピーしてください（待ち受けの空白があればそこへ入ります）\');\n        }\n        return;\n      }\n      e.preventDefault();\n      const t = S.dropT, d = drag;\n      endDrag();\n      if (!t) return;\n      if (t.fill) run(\'fill\', { blank: t.id, src: d.single });\n      else run(\'move\', { ids: d.ids, how: \'before\', before: t.before });\n    });\n    board.addEventListener(\'dragend\', endDrag);\n\n    // ストリップ\n    const strip = $(\'#strip\');\n    strip.addEventListener(\'click\', (e) => {\n      const t = e.target.closest(\'i\');\n      if (!t) return;\n      const p = S.pages[+t.dataset.i];\n      if (p) selectOnly(p.id);\n    });\n    strip.addEventListener(\'mousemove\', (e) => {\n      const t = e.target.closest(\'i\');\n      const p = t && S.pages[+t.dataset.i];\n      strip.title = p ? `P.${pad(p.n)}\u3000${SLABEL[p.status]}\u3000${p.kind === \'blank\' ? \'空白\' : (p.fin || p.raw || \'\')}` : \'\';\n    });\n\n    // お知らせ\n    $(\'#notices\').addEventListener(\'click\', (e) => {\n      const b = e.target.closest(\'[data-act]\');\n      if (!b) return;\n      const n = b.closest(\'.notice\');\n      if (n.dataset.sugg) run(\'suggest\', { id: n.dataset.sugg, accept: b.dataset.act === \'yes\' });\n      else run(\'notice\', { id: n.dataset.id, action: b.dataset.act });\n    });\n\n    // 道具の列\n    $(\'#chips\').addEventListener(\'click\', (e) => {\n      if (e.target.closest(\'[data-seen-all]\')) { run(\'seen\', { all: true }); return; }\n      const c = e.target.closest(\'[data-f]\');\n      if (!c) return;\n      S.filter = (S.filter === c.dataset.f && c.dataset.f !== \'all\') ? \'all\' : c.dataset.f;\n      filterChanged();\n    });\n    const search = $(\'#search\');\n    search.addEventListener(\'input\', () => { S.query = search.value; filterChanged(); });\n    search.addEventListener(\'keydown\', (e) => {\n      if (e.key === \'Enter\') { e.preventDefault(); gotoMatch(e.shiftKey ? -1 : 1); }\n      if (e.key === \'Escape\') { e.preventDefault(); if (search.value) { search.value = \'\'; S.query = \'\'; filterChanged(); } else search.blur(); }\n    });\n    $(\'#search-next\').onclick = () => gotoMatch(1);\n    $(\'#search-prev\').onclick = () => gotoMatch(-1);\n    $(\'#only-match\').onchange = (e) => { S.onlyMatch = e.target.checked; renderBoard(); };\n    $(\'#view-seg\').addEventListener(\'click\', (e) => { const b = e.target.closest(\'[data-v]\'); if (b) setView(b.dataset.v); });\n    $(\'#spread\').onchange = (e) => run(\'settings\', { patch: { spread: e.target.checked } });\n    const zoom = $(\'#zoom\');\n    zoom.addEventListener(\'input\', () => applyZoom(+zoom.value));\n\n    // 上の帯\n    let titleTimer = 0;\n    const title = $(\'#title\');\n    title.addEventListener(\'input\', () => { clearTimeout(titleTimer); titleTimer = setTimeout(() => run(\'title\', { value: title.value }), 450); });\n    title.addEventListener(\'keydown\', (e) => { if (e.key === \'Enter\' || e.key === \'Escape\') { e.preventDefault(); title.blur(); } });\n    title.addEventListener(\'blur\', () => { clearTimeout(titleTimer); if (S.st && title.value !== S.st.title) run(\'title\', { value: title.value }); });\n    $(\'#folder\').onclick = () => run(\'open_folder\', {}, { state: false });\n    $(\'#tabs\').addEventListener(\'click\', (e) => { const b = e.target.closest(\'[data-view]\'); if (!b) return; if (b.dataset.view === \'read\') openReader(); else closeReader(); });\n    $(\'#undo\').onclick = undo;\n    $(\'#redo\').onclick = redo;\n    $(\'#btn-export\').onclick = openExport;\n    $(\'#btn-settings\').onclick = () => openSettings();\n    $(\'#btn-help\').onclick = openHelp;\n    $(\'#offline-retry\').onclick = () => location.reload();\n\n    // 右の編集欄\n    const detail = $(\'#detail\');\n    detail.addEventListener(\'input\', (e) => {\n      const f = e.target.dataset.field;\n      if (!f || !S.primary) return;\n      if (e.target.tagName === \'TEXTAREA\') autoGrow(e.target);\n      if (f === \'lines\') updateCount();\n      queueText(S.primary, f, e.target.value);\n    });\n    detail.addEventListener(\'focusout\', (e) => {\n      const f = e.target.dataset?.field;\n      if (f && S.primary) flushText(S.primary + \'|\' + f);\n    });\n    detail.addEventListener(\'click\', (e) => {\n      const t = e.target.closest(\'button, .d-preview\');\n      if (!t) return;\n      const d = t.dataset;\n      if (d.status) setStatus(d.status);\n      else if (d.nav) step(+d.nav);\n      else if (d.which) { S.detailWhich = d.which; renderDetail(); }\n      else if (d.reveal) run(\'reveal\', { rel: d.reveal }, { state: false });\n      else if (d.ext) run(\'external\', { rel: d.ext }, { state: false });\n      else if (d.useold) run(\'use_old\', { id: S.primary, rel: d.useold });\n      else if (d.move) { if (d.move === \'number\') moveToNumber(); else move(d.move); }\n      else if (d.act === \'read\') openReader();\n      else if (d.act === \'fill\') openPicker(S.primary);\n      else if (d.act === \'wait\') toggleWait();\n      else if (d.act === \'delete\') deletePages();\n      else if (d.act === \'exclude\') excludeSel();\n      else if (d.act === \'quarantine\') quarantineSel();\n      else if (d.act === \'pair\') run(\'pair\', { ids: selectedIds() });\n      else if (d.act === \'blank-before\') insertBlank(\'before\');\n      else if (d.act === \'blank-after\') insertBlank(\'after\');\n    });\n\n    // 読む\n    $(\'#r-back\').onclick = closeReader;\n    $(\'#r-stage\').addEventListener(\'click\', (e) => {\n      const b = e.target.closest(\'[data-step]\');\n      if (b) { step(+b.dataset.step); return; }\n      const pane = e.target.closest(\'.r-pane\');\n      if (pane && pane.dataset.id && pane.dataset.id !== S.primary) selectOnly(pane.dataset.id, { noScroll: true });\n    });\n    $(\'#r-stage\').addEventListener(\'wheel\', (e) => { e.preventDefault(); step(e.deltaY > 0 ? 1 : -1); }, { passive: false });\n    $(\'#r-which\').addEventListener(\'click\', (e) => { const b = e.target.closest(\'[data-v]\'); if (b && !b.disabled) setView(b.dataset.v); });\n    $(\'#r-mode\').addEventListener(\'click\', (e) => { const b = e.target.closest(\'[data-m]\'); if (b) { S.readMode = b.dataset.m; renderReader(); } });\n    $(\'#r-status\').addEventListener(\'click\', (e) => { const b = e.target.closest(\'[data-status]\'); if (b) setStatus(b.dataset.status); });\n    $(\'#r-exclude\').onclick = excludeSel;\n    $(\'#r-quar\').onclick = quarantineSel;\n\n    // 閉じる・窓\n    document.addEventListener(\'mousedown\', (e) => {\n      if (!$(\'#menu\').hidden && !e.target.closest(\'.menu\')) closeMenu();\n    });\n    window.addEventListener(\'blur\', closeMenu);\n    wrap.addEventListener(\'scroll\', closeMenu, { passive: true });\n    let geoTimer = 0;\n    window.addEventListener(\'resize\', () => {\n      closeMenu();\n      clearTimeout(geoTimer);\n      geoTimer = setTimeout(() => run(\'window\', { w: outerWidth, h: outerHeight, aw: screen.availWidth, ah: screen.availHeight }, { state: false }), 900);\n    });\n    window.addEventListener(\'pagehide\', () => {\n      flushAllText();\n      try { navigator.sendBeacon(\'/api/bye?t=\' + encodeURIComponent(TOKEN)); } catch (e) { /* 送れなくても問題ない */ }\n    });\n    document.addEventListener(\'keydown\', onKey);\n  }\n\n  let zoomTimer = 0;\n  function applyZoom(v) {\n    S.zoomLive = v;\n    $(\'#board\').style.setProperty(\'--tw\', v + \'px\');\n    $(\'#zoom\').value = v;\n    clearTimeout(zoomTimer);\n    zoomTimer = setTimeout(() => {\n      run(\'settings\', { patch: { thumb_size: v } }).then(() => { S.zoomLive = 0; });\n    }, 500);\n  }\n\n  // ───────────── キー ─────────────\n\n  function onKey(e) {\n    const t = e.target;\n    const typing = t && (t.tagName === \'INPUT\' || t.tagName === \'TEXTAREA\' || t.tagName === \'SELECT\' || t.isContentEditable);\n    const ctrl = e.ctrlKey || e.metaKey;\n    if (S.modals.length) {\n      const m = S.modals[S.modals.length - 1];\n      if (e.key === \'Escape\') { e.preventDefault(); m.close(null); }\n      else if (e.key === \'Enter\' && m.enter && !(t && t.tagName === \'TEXTAREA\')) { e.preventDefault(); m.enter(); }\n      return;\n    }\n    if (!$(\'#menu\').hidden && e.key === \'Escape\') { closeMenu(); return; }\n    if (!S.st) return;\n    if (e.key === \'PageDown\' || e.key === \'PageUp\') {\n      e.preventDefault();\n      step(e.key === \'PageDown\' ? 1 : -1, { keepField: typing && t.closest(\'#detail\') });\n      return;\n    }\n    if (typing) {\n      if (e.key === \'Escape\') { e.preventDefault(); t.blur(); }\n      return;\n    }\n    const k = e.key;\n    const spread = S.view === \'read\' ? S.readMode === \'spread\' : S.st.settings.spread;\n    if (ctrl) {\n      const lk = k.toLowerCase();\n      if (lk === \'z\' && !e.shiftKey) { e.preventDefault(); undo(); return; }\n      if (lk === \'y\' || (lk === \'z\' && e.shiftKey)) { e.preventDefault(); redo(); return; }\n      if (lk === \'a\') { e.preventDefault(); S.sel = new Set(S.pages.map((p) => p.id)); S.anchor = S.pages[0]?.id; renderSelection(); return; }\n      if (k === \'ArrowLeft\' || k === \'ArrowRight\') { e.preventDefault(); const later = (k === \'ArrowRight\') !== spread; move(later ? \'down\' : \'up\'); return; }\n      if (k === \'ArrowUp\' || k === \'ArrowDown\') { e.preventDefault(); const n = spread ? rowSize() * 2 : rowSize(); move(k === \'ArrowDown\' ? \'down\' : \'up\', { k: n }); return; }\n      if (k === \'Home\') { e.preventDefault(); move(\'top\'); return; }\n      if (k === \'End\') { e.preventDefault(); move(\'end\'); return; }\n      return;\n    }\n    if (e.altKey) return;\n    switch (k) {\n      case \'ArrowRight\': case \'ArrowLeft\': {\n        e.preventDefault();\n        const next = (k === \'ArrowRight\') !== spread;\n        step(next ? 1 : -1, { extend: e.shiftKey && S.view === \'board\' });\n        return;\n      }\n      case \'ArrowDown\': case \'ArrowUp\':\n        if (S.view === \'board\') { e.preventDefault(); rowStep(k === \'ArrowDown\' ? 1 : -1, e.shiftKey); }\n        return;\n      case \'Home\': e.preventDefault(); if (S.pages.length) selectOnly(S.pages[0].id); return;\n      case \'End\': e.preventDefault(); if (S.pages.length) selectOnly(S.pages[S.pages.length - 1].id); return;\n      case \'1\': case \'2\': case \'3\': case \'4\': case \'5\': e.preventDefault(); setStatus(STATUS[+k - 1][0]); return;\n      case \'i\': case \'I\': e.preventDefault(); insertBlank(e.shiftKey ? \'before\' : \'after\'); return;\n      case \'x\': case \'X\': e.preventDefault(); if (e.shiftKey) quarantineSel(); else excludeSel(); return;\n      case \'v\': case \'V\': e.preventDefault(); toggleView(); return;\n      case \'Enter\': e.preventDefault(); if (S.view === \'board\') openReader(); return;\n      case \'Delete\': case \'Backspace\': if (S.view === \'board\') { e.preventDefault(); deletePages(); } return;\n      case \'/\': e.preventDefault(); if (S.view === \'read\') closeReader(); $(\'#search\').focus(); $(\'#search\').select(); return;\n      case \'?\': e.preventDefault(); openHelp(); return;\n      case \'Escape\':\n        e.preventDefault();\n        if (S.view === \'read\') closeReader();\n        else if (S.sel.size > 1) selectOnly(S.primary, { noScroll: true });\n        else if (S.filter !== \'all\' || S.query) { S.filter = \'all\'; S.query = \'\'; $(\'#search\').value = \'\'; filterChanged(); }\n        return;\n      default:\n    }\n  }\n\n  // ───────────── はじめる ─────────────\n\n  bind();\n  fetchState().then(() => {\n    connect();\n    if (S.primary) scrollToPrimary();\n  });\n  window.__dk = { S, api, step, openMenu, cards };\n})();\n',
    'icon.svg': '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">\n  <defs>\n    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">\n      <stop offset="0" stop-color="#1d232c"/>\n      <stop offset="1" stop-color="#0a0c10"/>\n    </linearGradient>\n    <radialGradient id="glow" cx=".5" cy=".5" r=".5">\n      <stop offset="0" stop-color="#efe8d8" stop-opacity=".55"/>\n      <stop offset="1" stop-color="#efe8d8" stop-opacity="0"/>\n    </radialGradient>\n  </defs>\n  <rect x="2" y="2" width="60" height="60" rx="13" fill="url(#bg)"/>\n  <rect x="2.5" y="2.5" width="59" height="59" rx="12.5" fill="none" stroke="#38424f" stroke-width="1"/>\n  <g fill="#38424f">\n    <rect x="11" y="10" width="11" height="15" rx="1.6"/>\n    <rect x="26.5" y="10" width="11" height="15" rx="1.6"/>\n    <rect x="42" y="10" width="11" height="15" rx="1.6"/>\n    <rect x="11" y="29" width="11" height="15" rx="1.6"/>\n    <rect x="42" y="29" width="11" height="15" rx="1.6"/>\n  </g>\n  <circle cx="32" cy="36.5" r="14" fill="url(#glow)"/>\n  <rect x="26.5" y="29" width="11" height="15" rx="1.6" fill="#efe8d8"/>\n  <g>\n    <rect x="11" y="50" width="8" height="4" rx="1" fill="#4dab78"/>\n    <rect x="20.5" y="50" width="8" height="4" rx="1" fill="#4dab78"/>\n    <rect x="30" y="50" width="8" height="4" rx="1" fill="#c6a052"/>\n    <rect x="39.5" y="50" width="6" height="4" rx="1" fill="#6d8dd6"/>\n    <rect x="47" y="50" width="6" height="4" rx="1" fill="#4d5665"/>\n  </g>\n</svg>\n',
}

log = logging.getLogger("daiwari")


class OpError(Exception):
    """画面にそのまま出してよい失敗（ユーザー向けの文）"""


# ───────────────────────────── 小道具 ─────────────────────────────

_NUM = re.compile(r"(\d+)")


def natkey(s: str):
    """「2」<「10」のように、人が期待する順に並べるためのキー"""
    return [int(t) if t.isdigit() else t.lower() for t in _NUM.split(s)]


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def rel_join(d: str, name: str) -> str:
    return f"{d}/{name}" if d else name


def rel_split(rel: str):
    d, _, n = rel.rpartition("/")
    return d, n


def rel_stem(rel: str) -> str:
    return os.path.splitext(rel_split(rel)[1])[0]


def vtoken(st) -> str:
    """ファイルの版（大きさと更新時刻から）。上書きされると変わるので画像のキャッシュ避けに使う"""
    if not st:
        return ""
    return hashlib.md5(f"{st[0]}:{st[1]}".encode()).hexdigest()[:10]


def atomic_write(path: Path, data: bytes):
    """書き込みの途中で止まっても壊れない保存（一時ファイル → 置き換え）"""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    last = None
    for i in range(12):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as e:   # ウイルス対策ソフトなどが一瞬つかんでいるとき
            last = e
            time.sleep(0.05 * (i + 1))
    raise last


def dump_json(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8")


def load_json(path: Path):
    with open(path, "rb") as f:
        data = f.read()
    return json.loads(data.decode("utf-8-sig"))


def set_hidden(path: Path):
    if IS_WIN:
        try:
            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)
        except Exception:
            pass


def is_image_name(name: str) -> bool:
    return name.lower().endswith(IMAGE_EXTS) and not name.startswith((".", "~$"))


def list_images(d: Path) -> dict:
    """フォルダ直下の画像 → (大きさ, 更新時刻ns)"""
    out = {}
    try:
        with os.scandir(d) as it:
            for e in it:
                n = e.name
                if not is_image_name(n):
                    continue
                try:
                    if not e.is_file():
                        continue
                    st = e.stat()
                except OSError:
                    continue
                out[n] = (st.st_size, st.st_mtime_ns)
    except (FileNotFoundError, NotADirectoryError):
        pass
    except OSError as e:
        log.warning("フォルダを読めません: %s (%s)", d, e)
    return out


def safe_name(s: str, fallback: str = "台割") -> str:
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", s or "").strip().rstrip(".")
    return s[:60] or fallback


def appdata_dir() -> Path:
    base = (os.environ.get("DAIWARI_APPDATA") or os.environ.get("LOCALAPPDATA")
            or str(Path.home() / ".local" / "share"))
    d = Path(base) / APP_ID
    d.mkdir(parents=True, exist_ok=True)
    return d


# ───────────────────────────── 絵の指紋とサムネイル ─────────────────────────────

_PIL = None


def pil():
    global _PIL
    if _PIL is None:
        from PIL import Image  # noqa: WPS433
        Image.MAX_IMAGE_PIXELS = 400_000_000
        _PIL = Image
    return _PIL


def have_pillow() -> bool:
    try:
        pil()
        return True
    except Exception:
        return False


def _transpose_for(orient: int):
    Image = pil()
    T = Image.Transpose
    return {2: T.FLIP_LEFT_RIGHT, 3: T.ROTATE_180, 4: T.FLIP_TOP_BOTTOM,
            5: T.TRANSPOSE, 6: T.ROTATE_270, 7: T.TRANSVERSE, 8: T.ROTATE_90}.get(orient)


def dhash(img) -> str:
    """似た絵の指紋：縮めた絵の明暗の模様（64ビット）"""
    Image = pil()
    g = img.convert("L").resize((9, 8), Image.Resampling.BILINEAR)
    b = g.tobytes()
    bits = 0
    for y in range(8):
        row = y * 9
        for x in range(8):
            bits = (bits << 1) | (1 if b[row + x] > b[row + x + 1] else 0)
    return format(bits, "016x")


def look_distance(a: str, b: str) -> int:
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except (TypeError, ValueError):
        return 64


def decode_image(path: Path):
    """絵を1回だけ開いて、画素の指紋・似た絵の指紋・サムネイル（JPEG）をまとめて作る。

    画素の指紋は色の並びそのものから作るので、名前・メタデータ・PNG の圧縮のしかたが
    変わっても同じになる（番付工房でリネーム＋メタデータ削除をしても追える）。
    """
    Image = pil()
    with Image.open(path) as im:
        try:
            orient = int(im.getexif().get(0x0112, 1) or 1)
        except Exception:
            orient = 1
        im.load()
        if im.mode not in ("RGB", "RGBA"):
            has_alpha = "A" in im.getbands() or "transparency" in im.info
            im = im.convert("RGBA" if has_alpha else "RGB")
        if im.mode == "RGBA" and im.getchannel("A").getextrema()[0] == 255:
            im = im.convert("RGB")       # 不透明なら RGB と同じ指紋にする
        w, h = im.size
        hs = hashlib.blake2b(digest_size=12)
        hs.update(f"{im.mode}:{w}x{h};".encode())
        hs.update(im.tobytes())
        pixel = hs.hexdigest()
        im.thumbnail((THUMB_MAX, THUMB_MAX), Image.Resampling.LANCZOS, reducing_gap=3.0)
        th = im
        tr = _transpose_for(orient)
        if tr is not None:
            th = th.transpose(tr)
            if orient >= 5:
                w, h = h, w
        if th.mode == "RGBA":
            bg = Image.new("RGB", th.size, THUMB_BG)
            bg.paste(th, mask=th.getchannel("A"))
            th = bg
        look = dhash(th)
        buf = io.BytesIO()
        th.save(buf, "JPEG", quality=82, optimize=True)
    return {"pixel": pixel, "look": look, "w": w, "h": h}, buf.getvalue()


def legacy_sig(path: Path, size: int) -> str:
    """現行版（v2）の中身の指紋（引っ越しのときだけ使う）"""
    chunk = 65536
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(chunk))
        if size > chunk * 2:
            f.seek(-chunk, os.SEEK_END)
            h.update(f.read(chunk))
    return h.hexdigest()[:20]


# ───────────────────────────── メタデータの削除（書き出し用） ─────────────────────────────

PNG_SIG = b"\x89PNG\r\n\x1a\n"
PNG_KEEP_ANCILLARY = {b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"iCCP", b"sBIT", b"pHYs",
                      b"bKGD", b"hIST", b"sPLT", b"acTL", b"fcTL", b"fdAT", b"cICP",
                      b"mDCv", b"cLLi"}


def strip_png(data: bytes):
    """PNG から文字情報（プロンプト・ワークフロー等）を除く。絵のデータには触らない"""
    if not data.startswith(PNG_SIG):
        return None
    out = [PNG_SIG]
    i = 8
    n = len(data)
    while i + 12 <= n:
        ln = struct.unpack(">I", data[i:i + 4])[0]
        typ = data[i + 4:i + 8]
        end = i + 12 + ln
        if end > n:
            return None
        critical = 65 <= typ[0] <= 90
        if critical or typ in PNG_KEEP_ANCILLARY:
            out.append(data[i:end])
        i = end
        if typ == b"IEND":
            break
    return b"".join(out)


def _exif_orientation(tiff: bytes):
    try:
        if tiff[:2] == b"II":
            e = "<"
        elif tiff[:2] == b"MM":
            e = ">"
        else:
            return None
        off = struct.unpack(e + "I", tiff[4:8])[0]
        cnt = struct.unpack(e + "H", tiff[off:off + 2])[0]
        for k in range(cnt):
            p = off + 2 + k * 12
            tag, typ = struct.unpack(e + "HH", tiff[p:p + 4])
            if tag == 0x0112 and typ == 3:
                return struct.unpack(e + "H", tiff[p + 8:p + 10])[0]
    except (struct.error, IndexError):
        return None
    return None


def _exif_orientation_segment(o: int) -> bytes:
    tiff = (b"MM\x00\x2a" + struct.pack(">I", 8) + struct.pack(">H", 1)
            + struct.pack(">HHIHH", 0x0112, 3, 1, o, 0) + struct.pack(">I", 0))
    payload = b"Exif\x00\x00" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def strip_jpeg(data: bytes):
    """JPEG から Exif・XMP・IPTC・コメントを除く（向きの情報だけは残す）"""
    if not data.startswith(b"\xff\xd8"):
        return None
    out = [b"\xff\xd8"]
    i = 2
    n = len(data)
    orient = None
    insert_at = 1
    while i < n:
        if data[i] != 0xFF:
            return None
        while i + 1 < n and data[i + 1] == 0xFF:
            i += 1
        if i + 1 >= n:
            return None
        marker = data[i + 1]
        if marker == 0xDA:                       # ここから先は絵のデータ
            out.append(data[i:])
            break
        if marker == 0xD8 or marker == 0x01 or 0xD0 <= marker <= 0xD7:
            out.append(data[i:i + 2])
            i += 2
            continue
        if i + 4 > n:
            return None
        ln = struct.unpack(">H", data[i + 2:i + 4])[0]
        seg = data[i:i + 2 + ln]
        drop = False
        if marker == 0xE1:
            if seg[4:10] == b"Exif\x00\x00":
                orient = _exif_orientation(seg[10:])
            drop = True
        elif marker in (0xED, 0xFE):
            drop = True
        elif 0xE3 <= marker <= 0xEF and marker != 0xEE:
            drop = True
        if not drop:
            out.append(seg)
            if marker == 0xE0:
                insert_at = len(out)
        i += 2 + ln
    if orient and orient != 1:
        out.insert(insert_at, _exif_orientation_segment(orient))
    return b"".join(out)


def strip_webp(data: bytes):
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    out = []
    i = 12
    n = len(data)
    while i + 8 <= n:
        fourcc = data[i:i + 4]
        ln = struct.unpack("<I", data[i + 4:i + 8])[0]
        end = i + 8 + ln + (ln & 1)
        chunk = bytearray(data[i:min(end, n)])
        if fourcc in (b"EXIF", b"XMP "):
            i = end
            continue
        if fourcc == b"VP8X" and len(chunk) > 8:
            chunk[8] &= ~(0x08 | 0x04) & 0xFF
        out.append(bytes(chunk))
        i = end
    body = b"WEBP" + b"".join(out)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def strip_metadata(data: bytes, ext: str):
    ext = ext.lower()
    try:
        if ext == ".png":
            return strip_png(data)
        if ext in (".jpg", ".jpeg"):
            return strip_jpeg(data)
        if ext == ".webp":
            return strip_webp(data)
    except Exception:
        log.exception("メタデータを消せませんでした")
    return None


# ───────────────────────────── 完成の見分け方 ─────────────────────────────

_V_RE = re.compile(r"^(.+?)_[Vv](\d+)$")


class Rules:
    """原画と完成の見分け。
      1) 「台詞入り」フォルダの同じ名前（台詞工房のフォルダ作業）
      2) 名前_V2・_V3 …（台詞工房のふだんの保存。番号の大きい方が新しい）
      3) 末尾が _s・_serif などの決まり（設定で変えられる）
    """

    def __init__(self, settings: dict):
        self.fin_dir = settings.get("fin_dir") or "台詞入り"
        self.use_v = bool(settings.get("use_vsave", True))
        sufs = [s for s in settings.get("fin_suffixes") or [] if isinstance(s, str) and s]
        self.suffixes = sorted(sufs, key=len, reverse=True)

    def classify(self, rel: str):
        """→ (種類, 元の名前の幹, 版番号)。種類は raw / findir / vsave / suffix / other"""
        d, name = rel_split(rel)
        stem = os.path.splitext(name)[0]
        if d:
            if d != self.fin_dir:
                return "other", stem, 0
            m = _V_RE.match(stem) if self.use_v else None
            if m:
                return "findir", m.group(1), int(m.group(2))
            return "findir", stem, 1
        if self.use_v:
            m = _V_RE.match(stem)
            if m:
                return "vsave", m.group(1), int(m.group(2))
        low = stem.lower()
        for suf in self.suffixes:
            if len(stem) > len(suf) and low.endswith(suf.lower()):
                return "suffix", stem[:-len(suf)], 0
        return "raw", stem, 0


def stem_key(s: str) -> str:
    return s.lower()


def kind_is_known(pr, st) -> bool:
    """保存してある指紋が、今のファイル（大きさ・更新時刻）のものか"""
    return bool(pr) and (pr.get("size"), pr.get("mtime_ns")) == tuple(st) and \
        (bool(pr.get("pixel")) or bool(pr.get("bad")))


# ───────────────────────────── 管理データの形 ─────────────────────────────

EMPTY_PAGE = {"raw": None, "fin": None, "old": [], "status": "todo", "scene": "",
              "lines": "", "memo": "", "fix": "", "wait": False, "new": False}
SNAP_KEYS = ("title", "pages", "sections", "excluded", "quarantined", "flags")
_BAD_DIR = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def valid_dirname(s, default):
    if not isinstance(s, str):
        return default
    s = s.strip()
    if not s or s in (".", "..") or s.startswith(".") or _BAD_DIR.search(s) or len(s) > 80:
        return default
    return s


def clean_rel(v):
    """管理データの中のファイル名を、フォルダの中の相対パス（/区切り・1段まで）にそろえる"""
    if not isinstance(v, str):
        return None
    v = v.strip().replace("\\", "/")
    if not v:
        return None
    if re.match(r"^[A-Za-z]:", v) or v.startswith("/"):
        v = v.rsplit("/", 1)[-1]          # 絶対パスは名前だけ使う（フォルダを移しても動くように）
    parts = [x for x in v.split("/") if x not in ("", ".")]
    if not parts or any(x == ".." for x in parts) or len(parts) > 2:
        return None
    return "/".join(parts)


def merge_settings(st) -> dict:
    out = copy.deepcopy(DEFAULT_SETTINGS)
    if not isinstance(st, dict):
        return out
    for k, dv in DEFAULT_SETTINGS.items():
        if k not in st:
            continue
        v = st[k]
        if isinstance(dv, bool):
            out[k] = bool(v)
        elif isinstance(dv, int):
            try:
                out[k] = int(v)
            except (TypeError, ValueError):
                pass
        elif isinstance(dv, str):
            if isinstance(v, str):
                out[k] = v[:500]
        elif isinstance(dv, list):
            if isinstance(v, list):
                out[k] = [str(x).strip() for x in v if str(x).strip()][:40]
    out["thumb_size"] = max(100, min(360, out["thumb_size"]))
    out["export_pad"] = max(1, min(6, out["export_pad"]))
    out["export_start"] = max(0, min(99999, out["export_start"]))
    out["chars_warn"] = max(10, min(2000, out["chars_warn"]))
    if out["view"] not in ("fin", "raw"):
        out["view"] = "fin"
    if out["export_use"] not in ("fin", "raw", "finonly"):
        out["export_use"] = "fin"
    if out["export_blank"] not in ("skip", "close"):
        out["export_blank"] = "skip"
    out["fin_dir"] = valid_dirname(out["fin_dir"], DEFAULT_SETTINGS["fin_dir"])
    out["quarantine_dir"] = valid_dirname(out["quarantine_dir"], DEFAULT_SETTINGS["quarantine_dir"])
    if out["quarantine_dir"] == out["fin_dir"]:
        out["quarantine_dir"] = DEFAULT_SETTINGS["quarantine_dir"]
    out["export_prefix"] = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", out["export_prefix"])[:60]
    return out


def clean_page(p) -> dict:
    q = copy.deepcopy(EMPTY_PAGE)
    if not isinstance(p, dict):
        p = {}
    q["id"] = p.get("id") if isinstance(p.get("id"), str) else ""
    for k in ("raw", "fin"):
        q[k] = clean_rel(p.get(k))
    olds = []
    for v in p.get("old") or []:
        r = clean_rel(v)
        if r and r not in olds and r not in (q["raw"], q["fin"]):
            olds.append(r)
    q["old"] = olds
    s = p.get("status")
    q["status"] = s if s in STATUS_IDS else "todo"
    for f in TEXT_FIELDS:
        q[f] = str(p.get(f) or "")[:TEXT_MAX]
    q["wait"] = bool(p.get("wait")) and not q["raw"] and not q["fin"]
    q["new"] = bool(p.get("new"))
    return q


def split_dialogue(text: str):
    """「」『』を含む行をセリフ、それ以外をメモに振り分ける"""
    lines, memo = [], []
    for ln in (text or "").splitlines():
        if not ln.strip():
            continue
        (lines if ("「" in ln or "『" in ln) else memo).append(ln)
    return "\n".join(lines), "\n".join(memo)


# ───────────────────────────── 現行版（v2）の読み取り ─────────────────────────────

_LEGACY_STATUS = {
    "todo": "todo", "none": "todo", "new": "todo", "wip": "todo", "未着手": "todo", "0": "todo",
    "art": "art", "image": "art", "img": "art", "draw": "art", "drawn": "art", "ready": "art",
    "画像のみ": "art", "画像": "art", "作画": "art", "1": "art",
    "text": "text", "serif": "text", "lines": "text", "dialog": "text", "dialogue": "text",
    "セリフ入": "text", "セリフ入り": "text", "セリフ": "text", "2": "text",
    "done": "done", "fin": "done", "finish": "done", "finished": "done", "complete": "done",
    "完成": "done", "3": "done",
    "fix": "fix", "redo": "fix", "retake": "fix", "要修正": "fix", "修正": "fix", "4": "fix",
}


def _first_str(d, keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return None


def _names(v):
    out = []
    if isinstance(v, dict):
        v = list(v.keys())
    if isinstance(v, list):
        for x in v:
            if isinstance(x, dict):
                x = _first_str(x, ("file", "name", "raw", "src", "path"))
            r = clean_rel(x) if isinstance(x, str) else None
            if r:
                out.append(r)
    return out


def parse_legacy(data) -> dict:
    """現行版の .daiwari.json を、いろいろな書き方を許して読み取る"""
    root = data if isinstance(data, dict) else {}
    items = data if isinstance(data, list) else (root.get("pages") or root.get("order")
                                                   or root.get("items") or [])
    meta = root.get("meta") if isinstance(root.get("meta"), dict) else {}
    if isinstance(items, dict):
        order = root.get("order") if isinstance(root.get("order"), list) else sorted(items, key=natkey)
        items = [dict(items.get(n) or {}, file=n) if isinstance(items.get(n), dict) else {"file": n}
                 for n in order]
    sigs = {}
    for key in ("sigs", "signatures", "fingerprints", "hashes"):
        v = root.get(key)
        if isinstance(v, dict):
            for n, s in v.items():
                r = clean_rel(n)
                if r and isinstance(s, str):
                    sigs[r] = s
    pages = []
    for it in items if isinstance(items, list) else []:
        if isinstance(it, str):
            it = dict(meta.get(it) or {}, file=it)
        if not isinstance(it, dict):
            continue
        files = it.get("files") if isinstance(it.get("files"), list) else []
        raw = _first_str(it, ("raw", "file", "name", "src", "image", "img", "path", "filename"))
        fin = _first_str(it, ("fin", "final", "serif", "serif_file", "done_file", "text_file",
                              "with_text", "finished", "fin_file", "completed", "pair"))
        if raw is None and files:
            raw = files[0] if isinstance(files[0], str) else None
            if len(files) > 1 and fin is None and isinstance(files[1], str):
                fin = files[1]
        if "raw" in it and isinstance(it.get("file"), str) and it.get("file") != it.get("raw") and fin is None:
            fin = it.get("file")
        kind = str(it.get("type") or it.get("kind") or "").lower()
        blank = bool(it.get("blank") or it.get("empty") or it.get("placeholder")
                     or kind in ("blank", "empty", "placeholder", "space"))
        raw_r, fin_r = (None, None) if blank else (clean_rel(raw), clean_rel(fin))
        if fin_r == raw_r:
            fin_r = None
        st = it.get("status", it.get("state"))
        status = _LEGACY_STATUS.get(str(st).strip().lower(), _LEGACY_STATUS.get(str(st).strip(), "todo")) \
            if st is not None else "todo"
        memo = it.get("memo")
        if not isinstance(memo, str):
            memo = _first_str(it, ("note", "notes", "text", "comment", "serif_text", "lines", "dialogue")) or ""
        scene = _first_str(it, ("scene", "heading", "label", "caption")) or ""
        for key in ("sig", "signature"):
            if isinstance(it.get(key), str) and raw_r:
                sigs.setdefault(raw_r, it[key])
        pages.append({"raw": raw_r, "fin": fin_r, "status": status, "scene": scene[:200],
                      "memo": memo[:TEXT_MAX]})
    excluded = []
    for key in ("excluded", "exclude", "hidden", "removed", "ignored"):
        excluded += _names(root.get(key))
    settings = root.get("settings") if isinstance(root.get("settings"), dict) else {}
    ren = {"cover": "cover_single", "first_single": "cover_single", "thumb": "thumb_size",
           "zoom": "thumb_size", "show": "view", "external": "external_cmd", "editor": "external_cmd",
           "suffixes": "fin_suffixes", "prefix": "export_prefix", "pad": "export_pad"}
    st2 = {ren.get(k, k): v for k, v in settings.items()}
    title = root.get("title") if isinstance(root.get("title"), str) else ""
    return {"title": title[:200], "pages": pages, "excluded": excluded, "sigs": sigs, "settings": st2}


# ───────────────────────────── 画面への知らせ ─────────────────────────────

class Hub:
    """画面への知らせ（Server-Sent Events）と、つながっている窓の数"""

    def __init__(self):
        self.cond = threading.Condition()
        self.seq = 0
        self.items = deque(maxlen=256)
        self.clients = 0
        self.ever = False
        self.changed_at = time.time()
        self.bye_at = 0.0

    def publish(self, kind, data=None):
        with self.cond:
            self.seq += 1
            self.items.append((self.seq, kind, data))
            self.cond.notify_all()

    def wait(self, after, timeout):
        with self.cond:
            self.cond.wait_for(lambda: self.seq > after, timeout)
            return [it for it in self.items if it[0] > after], self.seq

    def connect(self):
        with self.cond:
            self.clients += 1
            self.ever = True
            self.changed_at = time.time()

    def disconnect(self):
        with self.cond:
            self.clients = max(0, self.clients - 1)
            self.changed_at = time.time()


# ───────────────────────────── 台割（1フォルダ分） ─────────────────────────────

class Project:
    def __init__(self, folder, hub: Hub | None = None):
        self.folder = Path(folder).resolve()
        self.file = self.folder / PROJECT_FILE
        self.ddir = self.folder / DATA_DIR
        self.thumbs = self.ddir / "thumbs"
        self.bdir = self.ddir / "backup"
        self.hub = hub or Hub()
        self.lock = threading.RLock()
        self.doc: dict = {}
        self.rev = 1
        self.undo_stack: list = []
        self.redo_stack: list = []
        self.dirty = 0
        self.dirty_at = 0.0
        self.last_backup = 0.0
        self.listing: dict = {}
        self.qlisting: dict = {}
        self.pending: set = set()
        self.ident = None
        self.print_cache: dict = {}
        self.suggestions: list = []
        self.messages: list = []
        self.decode_sem = threading.BoundedSemaphore(3)
        self._klocks: dict = {}
        self._kguard = threading.Lock()
        self.need_reconcile = True
        self.wake = threading.Event()
        self.index_state = {"done": 0, "total": 0}
        self._moves = None
        self._legacy_src = None
        self._msg_seq = 0
        self.save_error = None
        self.quiet_fresh = False      # 引っ越し直後の1回は「新着」の印を付けない
        self.transient: set = set()   # いま開けなかった絵（あとで読み直す）

    # ── 開く・作る・引っ越す ──

    def open(self):
        for d in (self.ddir, self.thumbs, self.bdir):
            d.mkdir(parents=True, exist_ok=True)
        set_hidden(self.ddir)
        with self.lock:
            if self.file.exists():
                self.doc = self._load_with_fallback()
            elif (self.folder / LEGACY_FILE).exists():
                self.doc = self._migrate_legacy()
            else:
                self.doc = self._build_new()
            listing, ql = self.scan()
            self._reconcile_locked(listing, ql, quick=True)
            self.save(force=True)
            self._finish_migration()
            self._prune_prints()
        self.need_reconcile = True
        self.wake.set()

    def _empty_doc(self) -> dict:
        return {"app": APP_NAME, "version": 3, "title": "", "next_id": 1, "pages": [],
                "sections": [], "excluded": [], "quarantined": [], "prints": {},
                "settings": copy.deepcopy(DEFAULT_SETTINGS), "flags": {},
                "saved_at": "", "saved_by": f"{APP_NAME} {VERSION}"}

    def _build_new(self) -> dict:
        self.doc = self._empty_doc()
        listing, ql = self.scan()
        self._add_fresh(sorted(listing, key=natkey), listing, initial=True)
        self.listing, self.qlisting = listing, ql
        return self.doc

    def _load_with_fallback(self) -> dict:
        try:
            raw = load_json(self.file)
            doc = self._normalize(raw)
            try:
                newer = int(raw.get("version") or 3) > 3
            except (TypeError, ValueError):
                newer = False
            if newer:
                self.message("warn", "新しい版の台割工房で保存されたデータです。知らない項目は保存の際に消えることがあります。")
            return doc
        except Exception as e:
            log.exception("台割.daiwari を読めません")
            broken = self.bdir / f"壊れていた_{stamp()}.json"
            try:
                shutil.copy2(self.file, broken)
            except OSError:
                pass
            for b in self.list_backups():
                try:
                    doc = self._normalize(load_json(self.bdir / b["name"]))
                    self.message("warn", f"台割.daiwari が読めなかったので、バックアップ（{b['label']}）から戻しました。"
                                         f"読めなかったファイルは .daiwari\\backup に残してあります。（{e}）")
                    return doc
                except Exception:
                    continue
            self.message("warn", "台割.daiwari が読めず、バックアップも無かったので、フォルダの中身から作り直しました。"
                                 "読めなかったファイルは .daiwari\\backup に残してあります。")
            return self._build_new()

    def _normalize(self, src) -> dict:
        if not isinstance(src, dict) or not isinstance(src.get("pages"), list):
            raise ValueError("台割.daiwari の形式ではありません")
        doc = self._empty_doc()
        doc["title"] = str(src.get("title") or "")[:200]
        doc["settings"] = merge_settings(src.get("settings"))
        doc["flags"] = dict(src["flags"]) if isinstance(src.get("flags"), dict) else {}
        doc["sections"] = [s for s in src.get("sections") or [] if isinstance(s, dict)]
        used, fix_later = set(), []
        maxn = 0

        def claim(obj, prefix):
            nonlocal maxn
            pid = obj.get("id") or ""
            m = re.fullmatch(re.escape(prefix) + r"(\d+)", pid)
            if m and pid not in used:
                used.add(pid)
                maxn = max(maxn, int(m.group(1)))
            else:
                fix_later.append((obj, prefix))

        for p in src["pages"]:
            q = clean_page(p)
            claim(q, "p")
            doc["pages"].append(q)
        for e in src.get("excluded") or []:
            if isinstance(e, str):
                e = {"page": {"raw": e}}
            if not isinstance(e, dict):
                continue
            ent = {"id": e.get("id") if isinstance(e.get("id"), str) else "",
                   "page": clean_page(e.get("page")), "after": e.get("after") if isinstance(e.get("after"), str) else None,
                   "at": str(e.get("at") or "")}
            claim(ent, "e")
            if not ent["page"]["id"]:
                ent["page"]["id"] = ""
            claim(ent["page"], "p")
            doc["excluded"].append(ent)
        for e in src.get("quarantined") or []:
            if not isinstance(e, dict):
                continue
            moves = []
            for mv in e.get("moves") or []:
                if isinstance(mv, (list, tuple)) and len(mv) == 2:
                    a, b = clean_rel(mv[0]), clean_rel(mv[1])
                    if a and b:
                        moves.append([a, b])
            if not moves:
                continue
            ent = {"id": e.get("id") if isinstance(e.get("id"), str) else "", "moves": moves,
                   "page": clean_page(e["page"]) if isinstance(e.get("page"), dict) else None,
                   "pid": str(e.get("pid") or ""), "role": e.get("role") if e.get("role") in ("page", "raw", "fin", "old") else "page",
                   "after": e.get("after") if isinstance(e.get("after"), str) else None,
                   "at": str(e.get("at") or ""), "by": str(e.get("by") or "")}
            claim(ent, "q")
            if ent["page"] is not None:
                claim(ent["page"], "p")
            doc["quarantined"].append(ent)
        try:
            maxn = max(maxn, int(src.get("next_id") or 1) - 1)
        except (TypeError, ValueError):
            pass
        doc["next_id"] = maxn + 1
        self.doc = doc
        for obj, prefix in fix_later:
            obj["id"] = self.new_id(prefix)
        prints = src.get("prints") if isinstance(src.get("prints"), dict) else {}
        for k, v in prints.items():
            r = clean_rel(k)
            if r and isinstance(v, dict) and isinstance(v.get("size"), int) and isinstance(v.get("mtime_ns"), int):
                doc["prints"][r] = {"size": v["size"], "mtime_ns": v["mtime_ns"],
                                    "pixel": v.get("pixel") if isinstance(v.get("pixel"), str) else None,
                                    "look": v.get("look") if isinstance(v.get("look"), str) else None,
                                    "w": int(v.get("w") or 0), "h": int(v.get("h") or 0),
                                    **({"bad": True} if v.get("bad") else {})}
        return doc

    def _migrate_legacy(self) -> dict:
        src = self.folder / LEGACY_FILE
        self._legacy_src = src
        try:
            info = parse_legacy(load_json(src))
        except Exception as e:
            log.exception("v2 のデータを読めません")
            self.message("warn", f"以前の台割（.daiwari.json）を読めなかったので、フォルダの中身から作り直しました。"
                                 f"元のファイルは .daiwari\\backup に移してあります。（{e}）")
            return self._build_new()
        self.doc = doc = self._empty_doc()
        doc["title"] = info["title"]
        doc["settings"] = merge_settings(info["settings"])
        listing, ql = self.scan()
        for lp in info["pages"]:
            doc["pages"].append(self.new_page(raw=lp["raw"], fin=lp["fin"], status=lp["status"],
                                              scene=lp["scene"], lines=lp["memo"]))
        known = {r for p in doc["pages"] for r in (p["raw"], p["fin"]) if r}
        for name in info["excluded"]:
            if name in known:
                continue
            known.add(name)
            doc["excluded"].append({"id": self.new_id("e"), "page": self.new_page(raw=name),
                                    "after": None, "at": now_iso()})
        if info["sigs"]:
            self._match_legacy_sigs(info["sigs"], listing)
        doc["flags"]["migrated"] = {"from": "v2", "at": now_iso(), "pages": len(doc["pages"])}
        has_lines = any(p["lines"].strip() for p in doc["pages"])
        has_other = any(ln.strip() and "「" not in ln and "『" not in ln
                        for p in doc["pages"] for ln in p["lines"].splitlines())
        if has_lines and has_other:
            doc["flags"]["memo_split"] = "offer"
        self.quiet_fresh = True
        self.message("info", f"以前の台割（v2）から {len(doc['pages'])} ページを引っ越しました。"
                             "元のファイルは .daiwari\\backup に移してあります。")
        return doc

    def _match_legacy_sigs(self, sigs, listing):
        refs = self._refs()
        want = {}
        for r in refs:
            if r not in listing and sigs.get(r):
                want.setdefault(sigs[r], r)
        if not want:
            return
        mapping = {}
        for f in sorted(r for r in listing if r not in refs):
            try:
                s = legacy_sig(self.abs(f), listing[f][0])
            except OSError:
                continue
            o = want.pop(s, None)
            if o:
                mapping[o] = f
            if not want:
                break
        if mapping:
            log.info("v2 の指紋で %d 枚の名前の変化を追いました", len(mapping))
            self._remap(mapping)

    def _finish_migration(self):
        src = self._legacy_src
        self._legacy_src = None
        if not src or not src.exists():
            return
        try:
            shutil.copy2(src, self.bdir / f"v2の台割_{stamp()}.json")
            src.unlink()
        except OSError:
            log.exception("v2 のファイルを移せませんでした")

    # ── 保存とバックアップ ──

    def message(self, kind, text):
        self._msg_seq += 1
        self.messages.append({"id": f"msg{self._msg_seq}", "kind": kind, "text": text})
        del self.messages[:-6]

    def mark_dirty(self, level=2):
        if not self.dirty:
            self.dirty_at = time.time()
        self.dirty = max(self.dirty, level)

    def bump(self):
        self.rev += 1
        self.hub.publish("rev", self.rev)

    def save(self, force=False):
        with self.lock:
            if not force and not self.dirty:
                return True
            self.doc["saved_at"] = now_iso()
            self.doc["saved_by"] = f"{APP_NAME} {VERSION}"
            data = dump_json(self.doc)
            try:
                self._backup_before_save()
            except OSError:
                log.exception("バックアップを作れませんでした")
            try:
                atomic_write(self.file, data)
            except OSError as e:
                log.exception("保存できませんでした")
                if not self.save_error:
                    self.save_error = str(e)
                    self.message("error", f"台割.daiwari を保存できません（{e}）。フォルダが読み取り専用でないか確かめてください。")
                    self.bump()
                return False
            if self.save_error:
                self.save_error = None
            self.dirty = 0
            return True

    def _backup_before_save(self):
        if not self.file.exists():
            return
        today = dt.date.today().strftime("%Y%m%d")
        daily = self.bdir / f"日ごと_{today}.json"
        if not daily.exists():
            shutil.copy2(self.file, daily)
            for old in sorted(self.bdir.glob("日ごと_*.json"))[:-BACKUP_DAYS]:
                old.unlink(missing_ok=True)
        now = time.time()
        if now - self.last_backup >= BACKUP_EVERY:
            shutil.copy2(self.file, self.bdir / f"直近_{stamp()}.json")
            self.last_backup = now
            for old in sorted(self.bdir.glob("直近_*.json"))[:-BACKUP_KEEP]:
                old.unlink(missing_ok=True)

    def list_backups(self):
        out = []
        for p in self.bdir.glob("*.json"):
            m = re.match(r"^(直近|日ごと)_(\d{8})(?:-(\d{6}))?\.json$", p.name)
            if not m:
                continue
            d = m.group(2)
            t = m.group(3) or ""
            try:
                st = p.stat()
            except OSError:
                continue
            when = dt.datetime.fromtimestamp(st.st_mtime)
            label = f"{int(d[4:6])}月{int(d[6:8])}日 {when.strftime('%H:%M')}（{m.group(1)}）"
            out.append({"name": p.name, "label": label, "kind": m.group(1), "mtime": st.st_mtime,
                        "key": d + (t or when.strftime("%H%M%S"))})
        out.sort(key=lambda b: b["mtime"], reverse=True)
        return out

    def backup_info(self):
        out = []
        for b in self.list_backups():
            try:
                d = load_json(self.bdir / b["name"])
                pages = d.get("pages") or []
                b = dict(b, pages=len(pages),
                         done=sum(1 for p in pages if isinstance(p, dict) and p.get("status") == "done"),
                         title=str(d.get("title") or ""))
            except Exception:
                b = dict(b, pages=None)
            out.append(b)
        return out

    # ── 元に戻す ──

    def _snapshot(self) -> str:
        return json.dumps({k: self.doc[k] for k in SNAP_KEYS}, ensure_ascii=False, separators=(",", ":"))

    def _restore(self, snap: str):
        d = json.loads(snap)
        for k in SNAP_KEYS:
            self.doc[k] = d[k]

    def _push_undo(self, label, snap, moves, key):
        now = time.time()
        if key and self.undo_stack:
            top = self.undo_stack[-1]
            if top.get("key") == key and now - top["t"] < 4 and not top["moves"] and not moves:
                top["t"] = now
                self.redo_stack.clear()
                return
        self.undo_stack.append({"label": label, "snap": snap, "moves": list(moves or []), "key": key, "t": now})
        del self.undo_stack[:-UNDO_LIMIT]
        self.redo_stack.clear()

    # ── フォルダとファイル ──

    def abs(self, rel: str) -> Path:
        parts = rel.split("/")
        if not rel or len(parts) > 2 or any(x in ("", ".", "..") for x in parts) or ":" in rel or "\\" in rel:
            raise OpError("フォルダの外のファイルは扱えません")
        return self.folder.joinpath(*parts)

    def _scan_dirs(self):
        s = self.doc.get("settings") or DEFAULT_SETTINGS
        qdir = s["quarantine_dir"]
        dirs = {s["fin_dir"]}
        for r in self._refs():
            d = rel_split(r)[0]
            if d:
                dirs.add(d)
        qdirs = {qdir, LEGACY_QDIR}
        for e in self.doc.get("quarantined") or []:
            for _, q in e["moves"]:
                qdirs.add(rel_split(q)[0])
        dirs -= qdirs | {DATA_DIR, ""}
        qdirs.discard("")
        return dirs, qdirs

    def scan(self):
        """フォルダの今の中身 → (ページになる絵, 隔離フォルダの絵)"""
        with self.lock:
            dirs, qdirs = self._scan_dirs()
        listing = dict(list_images(self.folder))
        for d in dirs:
            for n, st in list_images(self.folder / d).items():
                listing[rel_join(d, n)] = st
        ql = {}
        for d in qdirs:
            for n, st in list_images(self.folder / d).items():
                ql[rel_join(d, n)] = st
        return listing, ql

    def _rescan_now(self):
        self.listing, self.qlisting = self.scan()

    def _refs(self):
        """ファイル名 → [(ページ, 役目, どこ)]。どこ = page（台割）/ excl（除外）"""
        refs: dict = {}
        for p in self.doc.get("pages") or []:
            self._add_refs(refs, p, "page")
        for e in self.doc.get("excluded") or []:
            self._add_refs(refs, e["page"], "excl")
        return refs

    @staticmethod
    def _add_refs(refs, p, where):
        for role in ("raw", "fin"):
            r = p.get(role)
            if r:
                refs.setdefault(r, []).append((p, role, where))
        for r in p.get("old") or []:
            refs.setdefault(r, []).append((p, "old", where))

    def _qreg(self):
        reg = {}
        for e in self.doc["quarantined"]:
            for _, q in e["moves"]:
                reg[q] = e
        return reg

    def _remap(self, mapping, lost=()):
        """ファイル名の付け替えを、全部のページへ同時に当てる（入れ替わりにも対応）"""
        lost = set(lost)

        def f(r):
            if r in mapping:
                return mapping[r]
            if r in lost:
                return None
            return r

        for p in self.doc["pages"] + [e["page"] for e in self.doc["excluded"]]:
            for role in ("raw", "fin"):
                if p.get(role):
                    p[role] = f(p[role])
            olds = []
            for r in p.get("old") or []:
                x = f(r)
                if x and x not in olds and x not in (p.get("raw"), p.get("fin")):
                    olds.append(x)
            p["old"] = olds
        prints = self.doc["prints"]
        for o, c in mapping.items():
            pr = prints.get(o)
            if pr and c not in prints:
                prints[c] = dict(pr)

    def _prune_prints(self):
        refs = self._refs()
        keep = set(refs) | set(self.listing) | set(self.qlisting) | set(self._qreg())
        prints = self.doc["prints"]
        for k in [k for k in prints if k not in keep]:
            del prints[k]

    # ── ページの小道具 ──

    def new_id(self, prefix="p") -> str:
        n = int(self.doc.get("next_id") or 1)
        self.doc["next_id"] = n + 1
        return f"{prefix}{n}"

    def new_page(self, **kw) -> dict:
        p = copy.deepcopy(EMPTY_PAGE)
        p["id"] = self.new_id("p")
        p.update(kw)
        return p

    def _page(self, pid) -> dict:
        for p in self.doc["pages"]:
            if p["id"] == pid:
                return p
        raise OpError("そのページが見つかりません（ほかの操作で変わったかもしれません）")

    def _sel(self, a) -> list:
        ids = a.get("ids") or ([a["id"]] if a.get("id") else [])
        want = set(ids)
        return [p for p in self.doc["pages"] if p["id"] in want]

    def _present(self, p) -> list:
        return [r for r in (p.get("raw"), p.get("fin"), *(p.get("old") or [])) if r and r in self.listing]

    def _index_after(self, after) -> int:
        pages = self.doc["pages"]
        if after == "^":
            return 0
        if after:
            for i, p in enumerate(pages):
                if p["id"] == after:
                    return i + 1
        return len(pages)

    def _free_rel(self, d, name, taken) -> str:
        stem, ext = os.path.splitext(name)
        cand, i = name, 2
        while True:
            rel = rel_join(d, cand)
            if rel.lower() not in taken and not self.abs(rel).exists():
                taken.add(rel.lower())
                return rel
            cand = f"{stem}_{i}{ext}"
            i += 1

    def _do_moves(self, pairs):
        """[(元, 先)] の順にファイルを動かす。途中で失敗したら動かした分を戻して OpError"""
        done = []
        try:
            for s, d in pairs:
                sp, dp = self.abs(s), self.abs(d)
                name = rel_split(s)[1]
                if not sp.exists():
                    raise OpError(f"「{s}」が見つかりません")
                if dp.exists():
                    raise OpError(f"「{d}」がすでにあります")
                dp.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.rename(sp, dp)
                except PermissionError:
                    raise OpError(f"「{name}」をほかのアプリが使っているため動かせません。そのアプリを閉じてから、もう一度どうぞ")
                except OSError as e:
                    raise OpError(f"「{name}」を動かせません（{e.strerror or e}）")
                done.append((s, d))
                pr = self.doc["prints"].get(s)
                if pr:
                    self.doc["prints"][d] = dict(pr)
        except OpError:
            for s, d in reversed(done):
                try:
                    os.rename(self.abs(d), self.abs(s))
                except OSError:
                    log.exception("動かしたファイルを戻せませんでした: %s", d)
            self._rescan_now()
            raise
        if self._moves is not None:
            self._moves.extend(done)
        self._rescan_now()
        return done

    # ── 画面に渡す中身 ──

    def state(self) -> dict:
        with self.lock:
            listing = self.listing
            prints = self.doc["prints"]
            pages = []
            counts = {k: 0 for k in STATUS_IDS}
            counts.update(blank=0, missing=0, new=0, wait=0, fixnote=0)
            for i, p in enumerate(self.doc["pages"], 1):
                q = {k: p.get(k) for k in ("id", "raw", "fin", "status", "scene", "lines", "memo", "fix", "wait", "new")}
                q["n"] = i
                for role in ("raw", "fin"):
                    r = p.get(role)
                    st = listing.get(r) if r else None
                    q[role + "_ok"] = st is not None
                    q[role + "_v"] = vtoken(st)
                    pr = prints.get(r) if r else None
                    if pr and pr.get("w"):
                        q[role + "_wh"] = [pr["w"], pr["h"]]
                q["old"] = [{"rel": r, "ok": r in listing, "v": vtoken(listing.get(r))} for r in p.get("old") or []]
                has = q["raw_ok"] or q["fin_ok"]
                q["kind"] = "image" if has else ("missing" if (p.get("raw") or p.get("fin")) else "blank")
                q["checking"] = bool(self.pending and ({p.get("raw"), p.get("fin")} & self.pending))
                counts[p["status"]] += 1
                if q["kind"] == "blank":
                    counts["blank"] += 1
                elif q["kind"] == "missing":
                    counts["missing"] += 1
                if p.get("new"):
                    counts["new"] += 1
                if p.get("wait"):
                    counts["wait"] += 1
                if p.get("fix", "").strip():
                    counts["fixnote"] += 1
                pages.append(q)
            excluded = []
            for e in self.doc["excluded"]:
                pg = e["page"]
                files = [r for r in (pg.get("raw"), pg.get("fin")) if r]
                excluded.append({"id": e["id"], "files": files, "ok": [r in listing for r in files],
                                 "v": [vtoken(listing.get(r)) for r in files],
                                 "text": (pg.get("lines") or pg.get("memo") or "")[:80], "at": e.get("at", "")})
            quar = []
            reg = self._qreg()
            for q, st in sorted(self.qlisting.items(), key=lambda kv: natkey(kv[0])):
                e = reg.get(q)
                orig = next((o for o, qq in e["moves"] if qq == q), None) if e else None
                pg = (e or {}).get("page") or {}
                quar.append({"q": q, "v": vtoken(st), "entry": e["id"] if e else None, "orig": orig,
                             "legacy": rel_split(q)[0] == LEGACY_QDIR,
                             "text": (pg.get("lines") or pg.get("memo") or "")[:80],
                             "at": (e or {}).get("at", "")})
            notices = list(self.messages)
            fl = self.doc["flags"]
            if fl.get("memo_split") == "offer":
                notices.append({"id": "memo_split", "kind": "ask",
                                "text": "引っ越したメモは、いまは全部「セリフ」欄に入っています。「」を含む行をセリフ、それ以外の行をメモに振り分けますか？（あとで元に戻せます）",
                                "actions": [["accept", "振り分ける"], ["decline", "このままにする"]]})
            nleg = sum(1 for q in self.qlisting if rel_split(q)[0] == LEGACY_QDIR)
            if nleg and fl.get("legacy_q") not in ("merged", "kept"):
                qd = self.doc["settings"]["quarantine_dir"]
                notices.append({"id": "legacy_q", "kind": "ask",
                                "text": f"以前の隔離フォルダ「{LEGACY_QDIR}」に {nleg} 枚あります。「{qd}」へまとめますか？（まとめなくても隔離した絵として扱います）",
                                "actions": [["accept", f"「{qd}」へまとめる"], ["decline", "このままにする"]]})
            sugg = []
            for s in self.suggestions:
                sugg.append(dict(s))
            return {
                "app": APP_NAME, "version": VERSION, "rev": self.rev,
                "folder": str(self.folder), "folder_name": self.folder.name,
                "window_title": f"{self.folder.name} — {APP_NAME}",
                "title": self.doc["title"], "settings": self.doc["settings"],
                "statuses": [list(s) for s in STATUSES], "pages": pages, "counts": counts,
                "excluded": excluded, "quarantine": quar, "notices": notices, "suggestions": sugg,
                "undo": {"n": len(self.undo_stack), "label": self.undo_stack[-1]["label"] if self.undo_stack else ""},
                "redo": {"n": len(self.redo_stack), "label": self.redo_stack[-1]["label"] if self.redo_stack else ""},
                "index": dict(self.index_state), "ident": dict(self.ident) if self.ident else None,
                "save_error": self.save_error,
            }

    # ───────── フォルダとの突き合わせ（見張り役のスレッドから） ─────────

    def reconcile(self, listing, qlisting):
        """フォルダの中身と台割を突き合わせる。名前が消えた絵は、中身の指紋で追いかける"""
        with self.lock:
            plan = self._plan(listing, qlisting)
            if not plan["decode"]:
                return self._apply(plan, listing, qlisting)
            self.pending = set(plan["waiting"])
            self.ident = {"done": 0, "total": len(plan["decode"])}
            self.bump()
        last = 0.0
        for i, (rel, st) in enumerate(plan["decode"], 1):
            self.compute_print(rel, st)
            self.ident = {"done": i, "total": len(plan["decode"])}
            if time.time() - last > 0.25:
                last = time.time()
                self.hub.publish("ident", self.ident)
        with self.lock:
            plan = self._plan(listing, qlisting, final=True)
            self.pending = set()
            self.ident = None
            changed = self._apply(plan, listing, qlisting)
            self.bump()
            return changed

    def _reconcile_locked(self, listing, qlisting, quick=False):
        plan = self._plan(listing, qlisting, quick=quick)
        return self._apply(plan, listing, qlisting)

    @staticmethod
    def _pair_ok(old_kind, cand_kind):
        if old_kind in ("missing", "changed"):
            return cand_kind in ("fresh", "changed", "qfresh")
        if old_kind == "qmissing":
            return cand_kind in ("fresh", "qfresh")
        return False

    def _plan(self, listing, qlisting, quick=False, final=False) -> dict:
        """final=True：開けなかった絵は「まだ分からない」として、そのまま突き合わせを進める"""
        prints = self.doc["prints"]
        refs = self._refs()
        reg = self._qreg()

        def st_of(pr):
            return (pr.get("size"), pr.get("mtime_ns")) if pr else None

        def px_of(r):
            pr = prints.get(r)
            return pr.get("pixel") if pr else None

        missing = [r for r in refs if r not in listing]
        changed = [r for r in refs if r in listing and prints.get(r) is not None
                   and st_of(prints[r]) != listing[r]]
        fresh = [r for r in listing if r not in refs]
        qfresh = [q for q in qlisting if q not in reg]
        qmissing = [q for q in reg if q not in qlisting]
        olds = [(r, "missing") for r in missing] + [(r, "changed") for r in changed] + \
               [(q, "qmissing") for q in qmissing]
        cands = {r: ("fresh", listing[r]) for r in fresh}
        cands.update({q: ("qfresh", qlisting[q]) for q in qfresh})
        cands.update({r: ("changed", listing[r]) for r in changed})
        okind = dict(olds)
        match, used, same = {}, set(), set()

        # 1) 大きさと更新時刻がぴったり同じ → 名前だけ変わった（開かずに分かる）
        by_stat: dict = {}
        for c, (_, st) in cands.items():
            by_stat.setdefault(st, []).append(c)
        for o, ok in olds:
            st = st_of(prints.get(o))
            if not st:
                continue
            for c in by_stat.get(st, []):
                if c in used or c == o or not self._pair_ok(ok, cands[c][0]):
                    continue
                match[o] = c
                used.add(c)
                break

        plan = {"decode": [], "waiting": [], "same": set(), "newpx": {}, "quick": quick}
        newpx = {}
        if not quick:
            want = [o for o, ok in olds if o not in match and px_of(o)]
            check = [c for c in changed if c not in used and px_of(c)]
            need = [c for c in cands if c not in used] if want else list(check)
            for c in need:
                st = cands[c][1]
                pr = self.print_cache.get((c, st[0], st[1]))
                if pr is None and kind_is_known(prints.get(c), st) and cands[c][0] != "changed":
                    pr = prints[c]       # 隔離フォルダの絵などは、裏でもう指紋を取ってある
                if pr is None:
                    plan["decode"].append((c, st))
                else:
                    newpx[c] = pr
            if plan["decode"] and not final:
                plan["waiting"] = [o for o in want if okind.get(o) == "missing"] + check
                return plan
            plan["decode"] = []
            # 2) 上書きされたように見えて、絵は同じ（メタデータだけ変わった）
            for c in check:
                pr = newpx.get(c)
                if pr and pr.get("pixel") and pr["pixel"] == px_of(c):
                    same.add(c)
                    used.add(c)
            # 3) 画素の指紋が同じ → 同じ絵（リネーム＋メタデータ削除にも効く）
            by_px: dict = {}
            for c in need:
                if c in used:
                    continue
                pr = newpx.get(c)
                if pr and pr.get("pixel"):
                    by_px.setdefault(pr["pixel"], []).append(c)
            for o, ok in olds:
                if o in match or o in same:
                    continue
                p = px_of(o)
                if not p:
                    continue
                for c in by_px.get(p, []):
                    if c in used or c == o or not self._pair_ok(ok, cands[c][0]):
                        continue
                    match[o] = c
                    used.add(c)
                    break

        plan["map"] = {o: c for o, c in match.items() if okind[o] in ("missing", "changed") and cands[c][0] in ("fresh", "changed")}
        plan["to_q"] = {o: c for o, c in match.items() if okind[o] in ("missing", "changed") and cands[c][0] == "qfresh"}
        plan["restore"] = {o: c for o, c in match.items() if okind[o] == "qmissing" and cands[c][0] == "fresh"}
        plan["qmove"] = {o: c for o, c in match.items() if okind[o] == "qmissing" and cands[c][0] == "qfresh"}
        plan["same"] = same
        plan["overwritten"] = [] if quick else [c for c in changed if c not in same and c not in match and c not in used]
        plan["lost"] = [] if quick else [c for c in changed if c in used and c not in match and c not in same]
        plan["qgone"] = [] if quick else [q for q in qmissing if q not in match]
        plan["newpx"] = newpx
        plan["missing_any"] = any(okind[o] == "missing" and o not in match for o in okind)
        # 4) 名前も絵も変わった → 似た絵を探して、確かめてもらう
        sugg = []
        if not quick:
            for o, ok in olds:
                if ok != "missing" or o in match:
                    continue
                opr = prints.get(o)
                if not opr or not opr.get("look"):
                    continue
                best = []
                for c in fresh:
                    if c in used:
                        continue
                    pr = newpx.get(c)
                    if not pr or not pr.get("look"):
                        continue
                    if opr.get("w") and pr.get("w"):
                        a1 = opr["w"] / max(1, opr["h"])
                        a2 = pr["w"] / max(1, pr["h"])
                        if abs(a1 - a2) > 0.03 * max(a1, a2):
                            continue
                    d = look_distance(opr["look"], pr["look"])
                    if d <= LOOK_NEAR:
                        best.append((d, c))
                best.sort()
                if best and (len(best) == 1 or best[1][0] > best[0][0]):
                    sugg.append((o, best[0][1], best[0][0]))
        plan["suggest"] = sugg
        return plan

    def _apply(self, plan, listing, qlisting) -> bool:
        doc = self.doc
        prints = doc["prints"]
        allst = dict(qlisting)
        allst.update(listing)
        changed = False

        def setprint(rel, base):
            st = allst.get(rel)
            if not st:
                return
            pr = dict(base) if base else {"pixel": None, "look": None, "w": 0, "h": 0}
            pr["size"], pr["mtime_ns"] = st
            prints[rel] = pr

        newpx = plan.get("newpx") or {}
        # 1) 名前の付け替え（入れ替わりもまとめて）
        if plan["map"] or plan["lost"]:
            olds = {o: prints.get(o) for o in plan["map"]}
            self._remap(plan["map"], plan["lost"])
            for o, c in plan["map"].items():
                setprint(c, newpx.get(c) or olds.get(o))
            changed = True
            log.info("名前の変化を追いました: %d 枚", len(plan["map"]))
        # 2) 外で「未使用」へ移された
        if plan["to_q"]:
            self._external_quarantine(plan["to_q"])
            for o, q in plan["to_q"].items():
                setprint(q, prints.get(o))
            changed = True
        # 3) 「未使用」から外へ戻された
        if plan["restore"]:
            reg = self._qreg()
            done = set()
            for q, c in plan["restore"].items():
                e = reg.get(q)
                if not e or id(e) in done:
                    continue
                done.add(id(e))
                pm = {}
                for orig, qq in e["moves"]:
                    if qq in plan["restore"]:
                        pm[orig] = plan["restore"][qq]
                        setprint(plan["restore"][qq], prints.get(qq))
                self._restore_entry(e, pm, None, mark_new=False)
                if e in doc["quarantined"]:
                    doc["quarantined"].remove(e)
            changed = True
        # 4) 「未使用」の中で名前が変わった
        if plan["qmove"]:
            for e in doc["quarantined"]:
                for mv in e["moves"]:
                    if mv[1] in plan["qmove"]:
                        old = mv[1]
                        mv[1] = plan["qmove"][old]
                        setprint(mv[1], prints.get(old))
            changed = True
        # 5) 「未使用」から消された（ゴミ箱へ等）→ 記録を外す
        if plan["qgone"]:
            gone = set(plan["qgone"])
            for e in list(doc["quarantined"]):
                e["moves"] = [mv for mv in e["moves"] if mv[1] not in gone]
                if not e["moves"]:
                    doc["quarantined"].remove(e)
            changed = True
        # 6) 上書き保存
        for c in plan["same"]:
            setprint(c, prints.get(c))
        for c in plan["overwritten"]:
            setprint(c, newpx.get(c))
            changed = True
        # 7) 新しく来た絵
        if not plan.get("quick"):
            refs_now = self._refs()
            fresh = sorted((r for r in listing if r not in refs_now), key=natkey)
            waiting = [r for r in fresh if (r, *listing[r]) in self.transient]
            if waiting and plan.get("missing_any"):
                fresh = [r for r in fresh if r not in waiting]
                self.need_reconcile = True
            if fresh:
                for r in fresh:
                    st = listing[r]
                    pr = newpx.get(r) or self.print_cache.get((r, st[0], st[1]))
                    if pr:
                        setprint(r, pr)
                self._add_fresh(fresh, listing, quiet=self.quiet_fresh)
                changed = True
            self.quiet_fresh = False
            # 8) 似た絵の確かめ
            for o, c, d in plan.get("suggest") or []:
                page = next((p for p in doc["pages"] if o in (p.get("raw"), p.get("fin"))), None)
                cand = next((p for p in doc["pages"] if c in (p.get("raw"), p.get("fin"))), None)
                if not page or not cand or page is cand:
                    continue
                if any(s["missing"] == o and s["cand"] == c for s in self.suggestions):
                    continue
                self.suggestions.append({"id": f"s{len(self.suggestions) + 1}_{secrets.token_hex(2)}",
                                         "page": page["id"], "missing": o, "cand": c, "cand_page": cand["id"],
                                         "dist": d, "cand_v": vtoken(listing.get(c))})
                changed = True
            self.suggestions = [s for s in self.suggestions if s["cand"] in listing and s["missing"] not in listing]
        self.listing, self.qlisting = listing, qlisting
        if changed:
            self.bump()
            self.mark_dirty(2)
            self.wake.set()
        return changed

    def _external_quarantine(self, to_q: dict):
        """ほかのアプリ（番付工房など）や手で「未使用」へ移された絵 → 隔離と同じ扱いにする"""
        refs = self._refs()
        groups: dict = {}
        for o, q in to_q.items():
            for p, role, where in refs.get(o, []):
                groups.setdefault(id(p), (p, where, []))[2].append((o, q, role))
        pages = self.doc["pages"]
        for p, where, items in groups.values():
            before = copy.deepcopy(p)
            for o, q, role in items:
                if role in ("raw", "fin") and p.get(role) == o:
                    p[role] = None
                elif role == "old" and o in p.get("old", []):
                    p["old"].remove(o)
            empty = not p.get("raw") and not p.get("fin") and not p.get("old")
            moves = [[o, q] for o, q, _ in items]
            if where == "excl":
                ent = next((e for e in self.doc["excluded"] if e["page"] is p), None)
                if ent and empty:
                    self.doc["excluded"].remove(ent)
                    self.doc["quarantined"].append({"id": self.new_id("q"), "moves": moves, "page": before,
                                                    "pid": p["id"], "role": "page", "after": ent.get("after"),
                                                    "at": now_iso(), "by": "outside"})
                    continue
            if empty and where == "page" and p in pages:
                idx = pages.index(p)
                after = pages[idx - 1]["id"] if idx > 0 else "^"
                pages.remove(p)
                before["new"] = False
                self.doc["quarantined"].append({"id": self.new_id("q"), "moves": moves, "page": before,
                                                "pid": p["id"], "role": "page", "after": after,
                                                "at": now_iso(), "by": "outside"})
            else:
                for o, q, role in items:
                    self.doc["quarantined"].append({"id": self.new_id("q"), "moves": [[o, q]], "page": None,
                                                    "pid": p["id"], "role": role, "after": None,
                                                    "at": now_iso(), "by": "outside"})

    def _restore_entry(self, e, pm, after, mark_new=True) -> list:
        """隔離の記録を、台割へ戻す。pm = 元の名前 → 戻した先の名前"""
        pages = self.doc["pages"]
        if e.get("page"):
            p = copy.deepcopy(e["page"])
            for role in ("raw", "fin"):
                if p.get(role) in pm:
                    p[role] = pm[p[role]]
            p["old"] = [pm.get(r, r) for r in p.get("old") or []]
            if any(x["id"] == p["id"] for x in pages):
                p["id"] = self.new_id("p")
            p["wait"] = False
            p["new"] = mark_new
            pages.insert(self._index_after(after or e.get("after")), p)
            return [p["id"]]
        role, pid = e.get("role"), e.get("pid")
        target = next((x for x in pages if x["id"] == pid), None)
        ids = []
        for orig, r in pm.items():
            if target is not None and role in ("raw", "fin") and not target.get(role):
                target[role] = r
                ids.append(target["id"])
            elif target is not None and role == "old":
                target.setdefault("old", []).append(r)
                ids.append(target["id"])
            else:
                p = self.new_page(raw=r, new=mark_new)
                idx = pages.index(target) + 1 if target is not None else self._index_after(after)
                pages.insert(idx, p)
                ids.append(p["id"])
        return ids

    def _add_fresh(self, rels, listing, initial=False, quiet=False) -> list:
        """新しく来た絵を台割へ。完成なら同じ名前のページへ、待ち受けの空白があればそこへ、無ければ末尾へ"""
        doc = self.doc
        pages = doc["pages"]
        s = doc["settings"]
        rules = Rules(s)
        by_stem: dict = {}
        for p in pages:
            if p.get("raw") and "/" not in p["raw"]:
                by_stem.setdefault(stem_key(rel_stem(p["raw"])), p)
        excl_stem: dict = {}            # 除外したページの完成は、除外したページへ入れる
        for e in doc["excluded"]:
            r = e["page"].get("raw")
            if r and "/" not in r:
                excl_stem.setdefault(stem_key(rel_stem(r)), e["page"])
        raws, fins = [], []
        for r in sorted(rels, key=natkey):
            kind, base, ver = rules.classify(r)
            if kind in ("raw", "other") or not s["auto_pair"]:
                raws.append(r)
            else:
                fins.append((r, kind, base, ver))
        new_stems = {stem_key(rel_stem(r)) for r in raws if "/" not in r}
        keep = []
        for f in fins:
            r, kind, base, ver = f
            known = stem_key(base) in by_stem or stem_key(base) in new_stems or stem_key(base) in excl_stem
            if kind in ("vsave", "suffix") and not known:
                raws.append(r)            # 元の絵が無い _V2 などは、ふつうの絵として扱う
            else:
                keep.append(f)
        raws.sort(key=natkey)
        touched = []
        waits = [] if initial else [p for p in pages if p.get("wait") and not self._present(p)]
        for r in raws:
            if waits:
                p = waits.pop(0)
                p["raw"], p["fin"], p["old"], p["wait"] = r, None, [], False
            else:
                p = self.new_page(raw=r)
                pages.append(p)
            p["new"] = not (initial or quiet)
            if "/" not in r:
                by_stem.setdefault(stem_key(rel_stem(r)), p)
            touched.append(p)
        keep.sort(key=lambda f: (natkey(f[2]), f[3], (listing.get(f[0]) or (0, 0))[1]))
        for r, kind, base, ver in keep:
            p = by_stem.get(stem_key(base))
            if p is None and stem_key(base) in excl_stem:
                self._attach_fin(excl_stem[stem_key(base)], r, kind, ver, listing, rules)
                continue
            if p is None:
                if waits:
                    p = waits.pop(0)
                    p["raw"], p["old"], p["wait"] = None, [], False
                else:
                    p = self.new_page()
                    pages.append(p)
                p["fin"] = r
                by_stem[stem_key(base)] = p
            else:
                self._attach_fin(p, r, kind, ver, listing, rules)
            if s["auto_status"] and p["status"] in ("todo", "art"):
                p["status"] = "text"
            p["new"] = not (initial or quiet)
            touched.append(p)
        if initial:
            pages.sort(key=lambda p: natkey(rel_split(p.get("raw") or p.get("fin") or "")[1]))
        return touched

    def _attach_fin(self, p, r, kind, ver, listing, rules):
        cur = p.get("fin")
        if not cur or cur == r:
            p["fin"] = r
            return
        if cur not in listing:              # 前の完成はもう無い
            p["fin"] = r
            return
        ck, _, cv = rules.classify(cur)
        if ck == kind and kind in ("vsave", "findir") and cv != ver:
            newer = ver > cv                # _V3 は _V2 より新しい
        else:
            newer = (listing.get(r) or (0, 0))[1] >= (listing.get(cur) or (0, 0))[1]
        if newer:
            if cur not in p["old"]:
                p["old"].append(cur)
            p["fin"] = r
        elif r not in p["old"]:
            p["old"].append(r)

    # ───────── 指紋とサムネイル ─────────

    def _key_lock(self, key):
        with self._kguard:
            lk = self._klocks.get(key)
            if lk is None:
                lk = self._klocks[key] = threading.Lock()
                if len(self._klocks) > 4000:
                    self._klocks = {key: lk}
            return lk

    def compute_print(self, rel, st):
        key = (rel, st[0], st[1])
        pr = self.print_cache.get(key)
        if pr is not None:
            return pr
        with self._key_lock(key):
            pr = self.print_cache.get(key)
            if pr is not None:
                return pr
            try:
                path = self.abs(rel)
                with self.decode_sem:
                    info, jpg = decode_image(path)
                thumb = self.thumbs / f"{info['pixel']}.jpg"
                if not thumb.exists():
                    atomic_write(thumb, jpg)
                pr = {"size": st[0], "mtime_ns": st[1], **info}
            except (PermissionError, FileNotFoundError) as e:
                log.info("いまは開けません（あとで読み直します）: %s (%s)", rel, e)
                self.transient.add(key)
                return {"size": st[0], "mtime_ns": st[1], "pixel": None, "look": None, "w": 0, "h": 0, "transient": True}
            except Exception as e:
                log.info("絵を読めません: %s (%s)", rel, e)
                pr = {"size": st[0], "mtime_ns": st[1], "pixel": None, "look": None, "w": 0, "h": 0, "bad": True}
            self.transient.discard(key)
            self.print_cache[key] = pr
            if len(self.print_cache) > 6000:
                for k in list(self.print_cache)[:2000]:
                    self.print_cache.pop(k, None)
            return pr

    def store_print(self, rel, pr):
        if pr.get("transient"):
            return
        with self.lock:
            st = self.listing.get(rel) or self.qlisting.get(rel)
            if not st or st != (pr["size"], pr["mtime_ns"]):
                return
            cur = self.doc["prints"].get(rel)
            if cur == pr:
                return
            self.doc["prints"][rel] = dict(pr)
            self.mark_dirty(1)

    def index_todo(self):
        """まだ指紋かサムネイルの無い絵（ページの順）"""
        with self.lock:
            prints = self.doc["prints"]
            out, seen = [], set()

            def consider(r, st):
                if not r or r in seen or not st:
                    return
                seen.add(r)
                pr = prints.get(r)
                if pr and (pr.get("size"), pr.get("mtime_ns")) == st:
                    if pr.get("bad"):
                        return
                    if pr.get("pixel") and (self.thumbs / f"{pr['pixel']}.jpg").exists():
                        return
                out.append((r, st))

            for p in self.doc["pages"]:
                for r in (p.get("raw"), p.get("fin"), *(p.get("old") or [])):
                    consider(r, self.listing.get(r) if r else None)
            for e in self.doc["excluded"]:
                pg = e["page"]
                for r in (pg.get("raw"), pg.get("fin")):
                    consider(r, self.listing.get(r) if r else None)
            for q, st in self.qlisting.items():
                consider(q, st)
            return out

    def thumb_file(self, rel):
        """サムネイルの JPEG（無ければその場で作る）"""
        p = self.abs(rel)
        try:
            s = p.stat()
        except OSError:
            return None
        st = (s.st_size, s.st_mtime_ns)
        with self.lock:
            pr = self.doc["prints"].get(rel)
        if pr and pr.get("pixel") and (pr.get("size"), pr.get("mtime_ns")) == st:
            t = self.thumbs / f"{pr['pixel']}.jpg"
            if t.exists():
                return t
        pr = self.compute_print(rel, st)
        if not pr.get("pixel"):
            return None
        self.store_print(rel, pr)
        t = self.thumbs / f"{pr['pixel']}.jpg"
        return t if t.exists() else None

    def prune_thumbs(self):
        with self.lock:
            keep = {pr.get("pixel") for pr in self.doc["prints"].values() if pr.get("pixel")}
        try:
            files = list(self.thumbs.glob("*.jpg"))
        except OSError:
            return
        if len(files) <= len(keep) + 200:
            return
        for f in files:
            if f.stem not in keep:
                try:
                    f.unlink()
                except OSError:
                    pass

    # ───────── 操作 ─────────

    UNDOABLE = {"title", "status", "text", "move", "blank", "delete", "wait", "exclude", "unexclude",
                "quarantine", "unquarantine", "fill", "pair", "split", "use_old", "notice", "suggest",
                "restore_backup"}

    def run(self, name, args):
        fn = getattr(self, "op_" + name, None) if re.fullmatch(r"[a-z_]+", name or "") else None
        if fn is None:
            raise OpError("知らない操作です")
        if not isinstance(args, dict):
            args = {}
        with self.lock:
            if name in self.UNDOABLE:
                snap = self._snapshot()
                self._moves = []
                try:
                    res = fn(args) or {}
                    moves = self._moves
                except Exception:
                    done, self._moves = self._moves or [], None
                    if done:      # 途中まで動かしたファイルは戻しておく
                        try:
                            self._do_moves([(d, s) for s, d in reversed(done)])
                        except Exception:
                            log.exception("途中まで動かしたファイルを戻せませんでした")
                    self._restore(snap)
                    raise
                finally:
                    self._moves = None
                if res.get("changed", True):
                    if res.get("undo", True):
                        self._push_undo(res.get("label") or name, snap, moves, res.get("coalesce"))
                    self.bump()
                    self.mark_dirty(2)
            else:
                res = fn(args) or {}
                if res.get("changed"):
                    self.bump()
                    self.mark_dirty(res.get("dirty", 2))
            res.setdefault("changed", True)
            return res

    def _after_restore(self):
        listing, ql = self.scan()
        self._reconcile_locked(listing, ql, quick=True)
        self.need_reconcile = True
        self.wake.set()

    def op_undo(self, a):
        if not self.undo_stack:
            return {"changed": False, "toast": "これより前には戻せません"}
        e = self.undo_stack[-1]
        if e["moves"]:
            self._do_moves([(d, s) for s, d in reversed(e["moves"])])
        self.undo_stack.pop()
        cur = self._snapshot()
        self._restore(e["snap"])
        self.redo_stack.append({"label": e["label"], "snap": cur, "moves": e["moves"], "key": None, "t": 0})
        self._after_restore()
        return {"changed": True, "toast": f"元に戻しました：{e['label']}"}

    def op_redo(self, a):
        if not self.redo_stack:
            return {"changed": False, "toast": "やり直せる操作がありません"}
        e = self.redo_stack[-1]
        if e["moves"]:
            self._do_moves([(s, d) for s, d in e["moves"]])
        self.redo_stack.pop()
        cur = self._snapshot()
        self._restore(e["snap"])
        self.undo_stack.append({"label": e["label"], "snap": cur, "moves": e["moves"], "key": None, "t": 0})
        self._after_restore()
        return {"changed": True, "toast": f"やり直しました：{e['label']}"}

    def op_title(self, a):
        v = str(a.get("value") or "")[:200]
        if v == self.doc["title"]:
            return {"changed": False}
        self.doc["title"] = v
        return {"label": "タイトル", "coalesce": "title"}

    def op_status(self, a):
        st = a.get("status")
        if st not in STATUS_IDS:
            raise OpError("進行の値が違います")
        pages = [p for p in self._sel(a) if p["status"] != st]
        if not pages:
            return {"changed": False}
        for p in pages:
            p["status"] = st
        return {"label": f"進行を「{STATUS_LABEL[st]}」に（{len(pages)}ページ）"}

    def op_text(self, a):
        f = a.get("field")
        if f not in TEXT_FIELDS:
            raise OpError("欄の名前が違います")
        p = self._page(a.get("id"))
        v = str(a.get("value") if a.get("value") is not None else "")[:TEXT_MAX]
        if f == "scene":
            v = v.replace("\n", " ")[:200]
        if p[f] == v:
            return {"changed": False}
        p[f] = v
        return {"label": f"{FIELD_LABEL[f]}の入力", "coalesce": ("text", p["id"], f)}

    def op_seen(self, a):
        pages = [p for p in self._sel(a) if p.get("new")]
        if a.get("all"):
            pages = [p for p in self.doc["pages"] if p.get("new")]
        for p in pages:
            p["new"] = False
        return {"changed": bool(pages)}

    def op_move(self, a):
        pages = self.doc["pages"]
        sel = {p["id"] for p in self._sel(a)}
        if not sel:
            return {"changed": False}
        how = a.get("how")
        before = [p["id"] for p in pages]
        if how in ("up", "down"):
            k = max(1, min(500, int(a.get("k") or 1)))
            arr = list(pages)
            for _ in range(k):
                if how == "up":
                    for i in range(1, len(arr)):
                        if arr[i]["id"] in sel and arr[i - 1]["id"] not in sel:
                            arr[i - 1], arr[i] = arr[i], arr[i - 1]
                else:
                    for i in range(len(arr) - 2, -1, -1):
                        if arr[i]["id"] in sel and arr[i + 1]["id"] not in sel:
                            arr[i], arr[i + 1] = arr[i + 1], arr[i]
            new = arr
        else:
            block = [p for p in pages if p["id"] in sel]
            rest = [p for p in pages if p["id"] not in sel]
            if how == "top":
                idx = 0
            elif how == "end":
                idx = len(rest)
            elif how == "number":
                try:
                    n = int(a.get("n"))
                except (TypeError, ValueError):
                    raise OpError("番号を数字で入れてください")
                idx = max(0, min(len(rest), n - 1))
            elif how == "before":
                tid = a.get("before")
                if tid is None:
                    idx = len(rest)
                else:
                    ids = [p["id"] for p in pages]
                    if tid not in ids:
                        raise OpError("動かす先のページが見つかりません")
                    j = ids.index(tid)
                    while j < len(ids) and ids[j] in sel:
                        j += 1
                    idx = len(rest) if j >= len(ids) else next(i for i, p in enumerate(rest) if p["id"] == ids[j])
            else:
                raise OpError("動かし方が違います")
            new = rest[:idx] + block + rest[idx:]
        if [p["id"] for p in new] == before:
            return {"changed": False}
        self.doc["pages"] = new
        names = {"top": "先頭へ", "end": "末尾へ", "up": "前へ", "down": "後ろへ", "number": "番号を指定して", "before": ""}
        return {"label": f"ページを{names.get(how, '')}動かす（{len(sel)}ページ）"}

    def op_blank(self, a):
        pages = self.doc["pages"]
        sel = self._sel(a)
        count = max(1, min(50, int(a.get("count") or 1)))
        new = [self.new_page(wait=bool(a.get("wait"))) for _ in range(count)]
        if not sel:
            idx = len(pages)
        elif a.get("where") == "before":
            idx = pages.index(sel[0])
        else:
            idx = pages.index(sel[-1]) + 1
        pages[idx:idx] = new
        w = "待ち受けの空白" if a.get("wait") else "空白"
        return {"label": f"{w}を入れる（{count}）", "select": [p["id"] for p in new]}

    def op_wait(self, a):
        on = bool(a.get("on", True))
        pages = [p for p in self._sel(a) if not self._present(p)]
        if not pages:
            raise OpError("待ち受けにできるのは、絵の無いページ（空白）だけです")
        ch = [p for p in pages if p["wait"] != on]
        for p in ch:
            p["wait"] = on
        if not ch:
            return {"changed": False}
        return {"label": "待ち受けにする" if on else "待ち受けをやめる",
                "toast": f"{len(ch)}ページを待ち受けにしました。次にフォルダへ来た絵がここへ入ります" if on else None}

    def op_delete(self, a):
        sel = self._sel(a)
        rm = [p for p in sel if not self._present(p)]
        if not rm:
            raise OpError("消せるのは絵の無いページ（空白）だけです。絵のページは「除外」か「隔離」をお使いください")
        ids = {p["id"] for p in rm}
        self.doc["pages"] = [p for p in self.doc["pages"] if p["id"] not in ids]
        skipped = len(sel) - len(rm)
        t = f"{len(rm)}ページの空白を消しました" + (f"（絵のある {skipped} ページはそのまま）" if skipped else "")
        return {"label": f"空白を消す（{len(rm)}）", "toast": t}

    def op_exclude(self, a):
        sel = self._sel(a)
        if not sel:
            return {"changed": False}
        pages = self.doc["pages"]
        rm = {p["id"] for p in sel}
        n_img = 0
        for p in sel:
            if not (p.get("raw") or p.get("fin") or p.get("old")):
                continue
            idx = pages.index(p)
            prev = next((pages[j]["id"] for j in range(idx - 1, -1, -1) if pages[j]["id"] not in rm), "^")
            pg = copy.deepcopy(p)
            pg["new"] = False
            self.doc["excluded"].append({"id": self.new_id("e"), "page": pg, "after": prev, "at": now_iso()})
            n_img += 1
        self.doc["pages"] = [p for p in pages if p["id"] not in rm]
        t = f"{n_img}ページを除外しました（ファイルはそのまま）" if n_img else f"{len(sel)}ページの空白を消しました"
        return {"label": f"除外（{len(sel)}ページ）", "toast": t}

    def op_unexclude(self, a):
        want = set(a.get("ids") or [])
        ents = [e for e in self.doc["excluded"] if e["id"] in want]
        if not ents:
            return {"changed": False}
        after = a.get("after")
        ids = []
        for e in ents:
            p = copy.deepcopy(e["page"])
            if any(x["id"] == p["id"] for x in self.doc["pages"]):
                p["id"] = self.new_id("p")
            idx = self._index_after(after if after else e.get("after"))
            self.doc["pages"].insert(idx, p)
            if after:
                after = p["id"]
            ids.append(p["id"])
            self.doc["excluded"].remove(e)
        return {"label": f"除外から戻す（{len(ents)}ページ）", "select": ids,
                "toast": f"{len(ents)}ページを台割へ戻しました"}

    def op_quarantine(self, a):
        sel = self._sel(a)
        if not sel:
            return {"changed": False}
        qdir = self.doc["settings"]["quarantine_dir"]
        taken, pairs, per = set(), [], []
        for p in sel:
            mv = []
            for r in self._present(p):
                q = self._free_rel(qdir, rel_split(r)[1], taken)
                mv.append((r, q))
            pairs += mv
            per.append((p, mv))
        if pairs:
            self._do_moves(pairs)
        pages = self.doc["pages"]
        rm = {p["id"] for p in sel}
        for p, mv in per:
            if not mv:
                continue
            idx = pages.index(p)
            prev = next((pages[j]["id"] for j in range(idx - 1, -1, -1) if pages[j]["id"] not in rm), "^")
            pg = copy.deepcopy(p)
            pg["new"] = False
            self.doc["quarantined"].append({"id": self.new_id("q"), "moves": [list(x) for x in mv], "page": pg,
                                            "pid": p["id"], "role": "page", "after": prev, "at": now_iso(), "by": "app"})
        self.doc["pages"] = [p for p in pages if p["id"] not in rm]
        return {"label": f"隔離（{len(sel)}ページ）",
                "toast": f"{len(sel)}ページ（{len(pairs)}枚）を「{qdir}」フォルダへ移しました"}

    def op_unquarantine(self, a):
        reg = self._qreg()
        entries, loose = [], []
        for q in a.get("items") or []:
            e = reg.get(q)
            if e is not None:
                if all(e is not x for x in entries):
                    entries.append(e)
            elif q in self.qlisting:
                loose.append(q)
        taken, pairs, plans = set(), [], []
        s = self.doc["settings"]
        for e in entries:
            pm = {}
            for orig, q in e["moves"]:
                if q not in self.qlisting:
                    continue
                d, name = rel_split(orig)
                if d and d != s["fin_dir"] and not (self.folder / d).is_dir():
                    d = ""
                target = rel_join(d, name)
                if target.lower() in taken or self.abs(target).exists():
                    target = self._free_rel(d, name, taken)
                else:
                    taken.add(target.lower())
                pairs.append((q, target))
                pm[orig] = target
            plans.append((e, pm))
        for q in loose:
            target = self._free_rel("", rel_split(q)[1], taken)
            pairs.append((q, target))
            plans.append((None, {q: target}))
        if not pairs:
            raise OpError("戻せる絵がありません")
        self._do_moves(pairs)
        ids = []
        after = a.get("after")
        for e, pm in plans:
            if e is None:
                target = next(iter(pm.values()))
                p = self.new_page(raw=target)
                self.doc["pages"].insert(self._index_after(after), p)
                ids.append(p["id"])
            else:
                ids += self._restore_entry(e, pm, after, mark_new=False)
                if e in self.doc["quarantined"]:
                    self.doc["quarantined"].remove(e)
            if after and ids:
                after = ids[-1]
        return {"label": f"隔離から戻す（{len(pairs)}枚）", "select": ids,
                "toast": f"{len(pairs)}枚を台割へ戻しました"}

    def op_fill(self, a):
        b = self._page(a.get("blank"))
        if self._present(b):
            raise OpError("絵の入っているページには入れられません")
        if a.get("excluded"):
            ent = next((e for e in self.doc["excluded"] if e["id"] == a["excluded"]), None)
            if not ent:
                raise OpError("その除外の記録が見つかりません")
            src, remove = ent["page"], (lambda: self.doc["excluded"].remove(ent))
        else:
            src = self._page(a.get("src"))
            if src is b:
                return {"changed": False}
            remove = (lambda: self.doc["pages"].remove(src))
        if not (src.get("raw") or src.get("fin")):
            raise OpError("入れる絵がありません")
        b["raw"], b["fin"], b["old"] = src.get("raw"), src.get("fin"), list(src.get("old") or [])
        for f in TEXT_FIELDS:
            if not b[f].strip() and src.get(f, "").strip():
                b[f] = src[f]
        if b["status"] == "todo":
            b["status"] = src.get("status", "todo")
        b["wait"] = False
        b["new"] = False
        remove()
        return {"label": "空白に絵を入れる", "select": [b["id"]]}

    def op_pair(self, a):
        sel = self._sel(a)
        if len(sel) != 2:
            raise OpError("組にする2ページを選んでください")
        A, B = sel
        fa = [r for r in (A.get("raw"), A.get("fin")) if r]
        fb = [r for r in (B.get("raw"), B.get("fin")) if r]
        if len(fa) != 1 or len(fb) != 1:
            raise OpError("原画か完成のどちらか1枚だけのページを、2つ選んでください")
        ra, rb = fa[0], fb[0]
        rules = Rules(self.doc["settings"])
        finlike = {"findir", "vsave", "suffix"}
        ka, kb = rules.classify(ra)[0] in finlike, rules.classify(rb)[0] in finlike
        if ka and not kb:
            raw, fin = rb, ra
        elif kb and not ka:
            raw, fin = ra, rb
        else:
            ma = (self.listing.get(ra) or (0, 0))[1]
            mb = (self.listing.get(rb) or (0, 0))[1]
            raw, fin = (ra, rb) if mb >= ma else (rb, ra)
        A["raw"], A["fin"] = raw, fin
        A["old"] = [r for r in A.get("old", []) + B.get("old", []) if r not in (raw, fin)]
        for f in TEXT_FIELDS:
            av, bv = A.get(f, ""), B.get(f, "")
            if not av.strip():
                A[f] = bv
            elif bv.strip() and bv.strip() != av.strip():
                A[f] = av.rstrip() + ("　" if f == "scene" else "\n") + bv
        if A["status"] == "todo":
            A["status"] = B["status"]
        A["new"] = False
        self.doc["pages"].remove(B)
        return {"label": "原画と完成の組にする", "select": [A["id"]],
                "toast": f"原画「{rel_split(raw)[1]}」と完成「{rel_split(fin)[1]}」を1ページにしました"}

    def op_split(self, a):
        p = self._page(a.get("id"))
        role = a.get("role")
        if role not in ("raw", "fin") or not p.get(role):
            raise OpError("外す絵がありません")
        other = "fin" if role == "raw" else "raw"
        if not p.get(other):
            raise OpError("このページには絵が1枚しかありません")
        r = p[role]
        p[role] = None
        q = self.new_page(**{role: r})
        pages = self.doc["pages"]
        pages.insert(pages.index(p) + 1, q)
        what = "原画" if role == "raw" else "完成"
        return {"label": f"{what}だけ外す", "select": [q["id"]],
                "toast": f"{what}「{rel_split(r)[1]}」を、すぐ後ろの別のページに分けました"}

    def op_use_old(self, a):
        p = self._page(a.get("id"))
        r = a.get("rel")
        if r not in (p.get("old") or []):
            raise OpError("その旧版が見つかりません")
        p["old"].remove(r)
        if p.get("fin"):
            p["old"].append(p["fin"])
        p["fin"] = r
        return {"label": "旧版を完成に戻す", "toast": f"「{rel_split(r)[1]}」を完成にしました"}

    def op_notice(self, a):
        nid, act = a.get("id"), a.get("action")
        fl = self.doc["flags"]
        if isinstance(nid, str) and re.fullmatch(r"msg\d+", nid):
            self.messages = [m for m in self.messages if m["id"] != nid]
            return {"changed": True, "undo": False}
        if nid == "memo_split":
            if act == "accept":
                n = 0
                for p in self.doc["pages"]:
                    if not p["lines"].strip():
                        continue
                    lines, memo = split_dialogue(p["lines"])
                    if memo:
                        p["lines"] = lines
                        p["memo"] = (p["memo"].rstrip() + "\n" + memo).strip() if p["memo"].strip() else memo
                        n += 1
                fl["memo_split"] = "done"
                return {"label": "メモをセリフとメモに振り分け", "toast": f"{n}ページを振り分けました（Ctrl+Z で戻せます）"}
            fl["memo_split"] = "declined"
            return {"undo": False}
        if nid == "legacy_q":
            if act == "accept":
                qdir = self.doc["settings"]["quarantine_dir"]
                taken, pairs = set(), []
                for q in sorted(self.qlisting, key=natkey):
                    if rel_split(q)[0] == LEGACY_QDIR:
                        pairs.append((q, self._free_rel(qdir, rel_split(q)[1], taken)))
                if pairs:
                    self._do_moves(pairs)
                m = dict(pairs)
                for e in self.doc["quarantined"]:
                    for mv in e["moves"]:
                        if mv[1] in m:
                            mv[1] = m[mv[1]]
                fl["legacy_q"] = "merged"
                try:
                    (self.folder / LEGACY_QDIR).rmdir()   # 空になったときだけ消える
                except OSError:
                    pass
                return {"label": f"「{LEGACY_QDIR}」を「{qdir}」へまとめる",
                        "toast": f"{len(pairs)}枚を「{qdir}」へまとめました"}
            fl["legacy_q"] = "kept"
            return {"undo": False}
        raise OpError("そのお知らせは見つかりません")

    def op_suggest(self, a):
        s = next((x for x in self.suggestions if x["id"] == a.get("id")), None)
        if not s:
            raise OpError("その確認はもう済んでいます")
        self.suggestions.remove(s)
        if not a.get("accept"):
            return {"undo": False}
        page = next((p for p in self.doc["pages"] if p["id"] == s["page"]), None)
        cand = next((p for p in self.doc["pages"] if p["id"] == s["cand_page"]), None)
        if not page:
            raise OpError("元のページが見つかりません")
        role = "raw" if page.get("raw") == s["missing"] else "fin"
        page[role] = s["cand"]
        if cand is not None and cand is not page:
            if cand.get("raw") == s["cand"]:
                cand["raw"] = None
            if cand.get("fin") == s["cand"]:
                cand["fin"] = None
            if not cand.get("raw") and not cand.get("fin"):
                for f in TEXT_FIELDS:
                    if not page[f].strip() and cand[f].strip():
                        page[f] = cand[f]
                self.doc["pages"].remove(cand)
        page["new"] = True
        return {"label": "差し替えとして入れる", "select": [page["id"]],
                "toast": f"P.{self.doc['pages'].index(page) + 1} の絵を差し替えました"}

    def op_restore_backup(self, a):
        name = str(a.get("name") or "")
        if not re.fullmatch(r"(直近|日ごと)_[0-9-]+\.json", name):
            raise OpError("そのバックアップは見つかりません")
        path = self.bdir / name
        if not path.exists():
            raise OpError("そのバックアップは見つかりません")
        keep_next = self.doc["next_id"]
        keep_doc = self.doc
        try:
            doc2 = self._normalize(load_json(path))
        finally:
            self.doc = keep_doc
        for k in SNAP_KEYS:
            self.doc[k] = doc2[k]
        self.doc["next_id"] = max(keep_next, doc2["next_id"])
        self._after_restore()
        return {"label": "バックアップから戻す", "toast": "バックアップの内容に戻しました（Ctrl+Z で今の状態へ戻せます）"}

    def op_settings(self, a):
        patch = a.get("patch") if isinstance(a.get("patch"), dict) else {}
        cur = self.doc["settings"]
        new = merge_settings({**cur, **patch})
        if new == cur:
            return {"changed": False}
        rescan = any(new[k] != cur[k] for k in ("fin_dir", "quarantine_dir", "fin_suffixes", "use_vsave", "auto_pair"))
        self.doc["settings"] = new
        if rescan:
            self.need_reconcile = True
            self.wake.set()
        return {"changed": True}

    def op_rescan(self, a):
        self.need_reconcile = True
        self.wake.set()
        return {"changed": False, "toast": "フォルダを読み直しています"}

    # ───────── 書き出し ─────────

    def export(self, opts: dict, progress=None) -> dict:
        """台割の順に、別のフォルダへ連番でコピーする（元の画像には触らない）"""
        with self.lock:
            s = self.doc["settings"]
            pages = copy.deepcopy(self.doc["pages"])
            listing = dict(self.listing)
            title = self.doc["title"] or self.folder.name
        use = opts.get("use", s["export_use"])
        blank_mode = opts.get("blank", s["export_blank"])
        prefix = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", str(opts.get("prefix", s["export_prefix"])))[:60]
        pad = max(1, min(6, int(opts.get("pad", s["export_pad"]))))
        start = max(0, int(opts.get("start", s["export_start"])))
        strip = bool(opts.get("strip", s["export_strip"]))
        script = bool(opts.get("script", s["export_script"]))
        with_memo = bool(opts.get("memo", s["export_memo"]))
        dest = opts.get("dest")
        if dest:
            dest = Path(dest)
            if not dest.is_absolute():
                raise OpError("書き出し先が正しくありません")
            dest = dest / f"{safe_name(title)}_{stamp()}"
        else:
            dest = self.folder / "書き出し" / f"{safe_name(title)}_{stamp()}"
        try:
            dest.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            dest = dest.with_name(dest.name + "_" + secrets.token_hex(2))
            dest.mkdir(parents=True)
        except OSError as e:
            raise OpError(f"書き出し先のフォルダを作れません（{e.strerror or e}）")
        lines = [f"{APP_NAME} 台本", f"作品：{title}", f"書き出し：{dt.datetime.now().strftime('%Y-%m-%d %H:%M')}",
                 "─" * 36, ""]
        n = start
        count = stripped = skipped = 0
        total = len(pages)
        for i, p in enumerate(pages, 1):
            if progress:
                progress(i, total)
            raw = p.get("raw") if p.get("raw") in listing else None
            fin = p.get("fin") if p.get("fin") in listing else None
            if use == "raw":
                src = raw or fin
            elif use == "finonly":
                src = fin
            else:
                src = fin or raw
            label = STATUS_LABEL.get(p["status"], "")
            if not src:
                tag = "空白／未作成" if not (p.get("raw") or p.get("fin")) else ("完成なし" if use == "finonly" else "絵が見つからない")
                lines.append(f"── P.{n:0{pad}d}  [{tag}]" if blank_mode == "skip" else f"── （{tag}・番号なし）")
                self._script_body(lines, p, with_memo)
                if blank_mode == "skip":
                    n += 1
                skipped += 1
                continue
            ext = os.path.splitext(src)[1].lower()
            newname = f"{prefix}{n:0{pad}d}{ext}"
            data = self.abs(src).read_bytes()
            if strip:
                d2 = strip_metadata(data, ext)
                if d2 is not None and d2 != data:
                    data = d2
                    stripped += 1
            atomic_write(dest / newname, data)
            os.utime(dest / newname, ns=(time.time_ns(), listing[src][1]))
            lines.append(f"── P.{n:0{pad}d}  [{label}]  {src}  →  {newname}")
            if raw and fin:
                lines.append(f"   原画 {raw} ／ 完成 {fin}")
            self._script_body(lines, p, with_memo)
            n += 1
            count += 1
        if script:
            atomic_write(dest / "台本.txt", ("\r\n".join(lines) + "\r\n").encode("utf-8-sig"))
        return {"dest": str(dest), "count": count, "skipped": skipped, "stripped": stripped}

    @staticmethod
    def _script_body(lines, p, with_memo):
        if p.get("scene", "").strip():
            lines.append(f"   ◆ {p['scene'].strip()}")
        body = [ln for ln in (p.get("lines") or "").splitlines() if ln.strip()]
        if body:
            lines += [f"   {ln}" for ln in body]
        else:
            lines.append("   （セリフ未記入）")
        if with_memo:
            for ln in (p.get("memo") or "").splitlines():
                if ln.strip():
                    lines.append(f"   （メモ）{ln}")
            for ln in (p.get("fix") or "").splitlines():
                if ln.strip():
                    lines.append(f"   （修正）{ln}")
        lines.append("")


# ───────────────────────────── 裏で動く係（見張り・指紋・保存） ─────────────────────────────

class Workers:
    def __init__(self, proj: Project, stopping: threading.Event):
        self.proj = proj
        self.stopping = stopping
        self.idx_lock = threading.Lock()
        self.idx_queue: deque = deque()
        self.idx_total = 0
        self.idx_done = 0
        self.idx_pub = 0.0
        self.save_fail_at = 0.0

    def start(self):
        for name, fn in (("watch", self.watch_loop), ("index1", self.index_loop),
                         ("index2", self.index_loop), ("save", self.save_loop)):
            threading.Thread(target=fn, name=name, daemon=True).start()

    def watch_loop(self):
        p = self.proj
        applied = seen = None
        seen_at = 0.0
        while not self.stopping.is_set():
            try:
                listing, ql = p.scan()
                sig = (frozenset(listing.items()), frozenset(ql.items()))
                now = time.time()
                if sig != seen:
                    seen, seen_at = sig, now
                quiet = now - seen_at >= QUIET_SEC
                if (sig != applied and quiet) or (p.need_reconcile and sig == seen and (quiet or applied is None)):
                    p.need_reconcile = False
                    p.reconcile(listing, ql)
                    applied = sig
            except Exception:
                log.exception("フォルダの見張りで問題が起きました")
            p.wake.wait(POLL_SEC)
            p.wake.clear()

    def _next_index_item(self):
        with self.idx_lock:
            if not self.idx_queue:
                todo = self.proj.index_todo()
                self.idx_queue = deque(todo)
                self.idx_total = len(todo)
                self.idx_done = 0
            if not self.idx_queue:
                return None
            return self.idx_queue.popleft()

    def index_loop(self):
        p = self.proj
        while not self.stopping.is_set():
            try:
                item = self._next_index_item()
                if item is None:
                    if p.index_state.get("total"):
                        p.index_state = {"done": 0, "total": 0}
                        p.hub.publish("index", p.index_state)
                    self.stopping.wait(2.0)
                    continue
                rel, st = item
                pr = p.compute_print(rel, st)
                p.store_print(rel, pr)
                with self.idx_lock:
                    self.idx_done += 1
                    p.index_state = {"done": self.idx_done, "total": self.idx_total}
                if time.time() - self.idx_pub > 0.5 or self.idx_done >= self.idx_total:
                    self.idx_pub = time.time()
                    p.hub.publish("index", p.index_state)
            except Exception:
                log.exception("指紋づくりで問題が起きました")
                self.stopping.wait(1.0)

    def save_loop(self):
        p = self.proj
        last_prune = time.time()
        while not self.stopping.wait(0.25):
            try:
                if p.dirty:
                    delay = 0.3 if p.dirty >= 2 else 5.0
                    if time.time() - p.dirty_at >= delay and time.time() - self.save_fail_at > 5:
                        if not p.save():
                            self.save_fail_at = time.time()
                if time.time() - last_prune > 600:
                    last_prune = time.time()
                    p.prune_thumbs()
            except Exception:
                log.exception("保存で問題が起きました")
                self.save_fail_at = time.time()


# ───────────────────────────── 画面とのやり取り（HTTP） ─────────────────────────────

CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


def ui_file(name: str) -> str:
    if UI_FILES is not None:
        return UI_FILES[name]
    return (Path(__file__).resolve().parent / "ui" / name).read_text(encoding="utf-8")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, app, port=0):
        self.app = app
        super().__init__(("127.0.0.1", port), Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = f"{APP_ID}/{VERSION}"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    @property
    def app(self) -> "App":
        return self.server.app

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    def _host_ok(self):
        host = (self.headers.get("Host") or "").strip().lower()
        port = self.server.server_address[1]
        return host in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}")

    def _token_ok(self, q):
        t = self.headers.get("X-Token") or (q.get("t") or [""])[0]
        return bool(t) and secrets.compare_digest(t.encode(), self.app.token.encode())

    def do_GET(self):
        self._wrap(self._get)

    def do_HEAD(self):
        self._wrap(self._get)

    def do_POST(self):
        self._wrap(self._post)

    def _wrap(self, fn):
        try:
            fn()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except OpError as e:
            try:
                self._json({"ok": False, "error": str(e)}, 400)
            except OSError:
                pass
        except Exception:
            log.exception("画面とのやり取りで問題が起きました: %s", self.path.split("?")[0])
            try:
                self._json({"ok": False, "error": "台割工房の中で問題が起きました（.daiwari\\log.txt に記録しました）"}, 500)
            except OSError:
                pass

    def _get(self):
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        path = u.path
        if not self._host_ok():
            return self._send(403, b"forbidden")
        if path in ("/", "/index.html"):
            if not self._token_ok(q):
                return self._send(403, "この画面は、台割.daiwari から開いてください。".encode("utf-8"))
            html = ui_file("index.html").replace("{{TOKEN}}", self.app.token).replace("{{VERSION}}", VERSION)
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8",
                              {"Cache-Control": "no-store", "Content-Security-Policy": CSP,
                               "Referrer-Policy": "no-referrer"})
        if path in ("/app.js", "/style.css", "/icon.svg"):
            name = path[1:]
            ctype = {"js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8",
                     "svg": "image/svg+xml"}[name.rsplit(".", 1)[1]]
            return self._send(200, ui_file(name).encode("utf-8"), ctype, {"Cache-Control": "no-store"})
        if not self._token_ok(q):
            return self._json({"ok": False, "error": "合言葉が違います。台割.daiwari から開き直してください"}, 403)
        if path == "/api/ping":
            return self._json({"ok": True, "app": APP_ID, "folder": str(self.app.proj.folder), "pid": os.getpid()})
        if path == "/api/state":
            return self._json({"ok": True, "state": self.app.proj.state()})
        if path == "/api/events":
            return self._events()
        if path == "/api/backups":
            return self._json({"ok": True, "items": self.app.proj.backup_info()})
        if path in ("/thumb", "/img"):
            return self._image((q.get("f") or [""])[0], thumb=(path == "/thumb"))
        return self._send(404, b"not found")

    def _image(self, rel, thumb):
        proj = self.app.proj
        rel = clean_rel(rel)
        if not rel or not is_image_name(rel_split(rel)[1]):
            return self._send(404, b"not found")
        path = proj.abs(rel)
        try:
            if not path.resolve().is_relative_to(proj.folder):
                return self._send(403, b"forbidden")
        except OSError:
            return self._send(404, b"not found")
        if thumb:
            t = proj.thumb_file(rel)
            if not t:
                return self._send(404, b"not found")
            data, ctype = t.read_bytes(), "image/jpeg"
        else:
            try:
                data = path.read_bytes()
            except OSError:
                return self._send(404, b"not found")
            ctype = MIME.get(os.path.splitext(rel)[1].lower(), "application/octet-stream")
        self._send(200, data, ctype, {"Cache-Control": "private, max-age=31536000, immutable"})

    def _sse(self, kind, data):
        self.wfile.write(f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8"))

    def _events(self):
        hub = self.app.proj.hub
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        hub.connect()
        try:
            last = hub.seq
            self.wfile.write(b"retry: 1500\n\n")
            self._sse("hello", {"rev": self.app.proj.rev})
            self.wfile.flush()
            while not self.app.stopping.is_set():
                items, seq = hub.wait(last, 15)
                if not items:
                    if seq > last:
                        last = seq
                        self._sse("rev", self.app.proj.rev)
                    else:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                for s, kind, data in items:
                    self._sse(kind, data)
                    last = s
                self.wfile.flush()
        finally:
            hub.disconnect()

    def _post(self):
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        path = u.path
        if not self._host_ok():
            return self._send(403, b"forbidden")
        if not self._token_ok(q):
            return self._json({"ok": False, "error": "合言葉が違います"}, 403)
        n = int(self.headers.get("Content-Length") or 0)
        if n > 4_000_000:
            return self._json({"ok": False, "error": "大きすぎます"}, 413)
        body = self.rfile.read(n) if n else b""
        if path == "/api/bye":
            self.app.proj.hub.bye_at = time.time()
            return self._send(200, b"")
        try:
            req = json.loads(body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._json({"ok": False, "error": "読めない依頼です"}, 400)
        if not isinstance(req, dict):
            req = {}
        if path == "/api/op":
            res = self.app.op(str(req.get("op") or ""), req.get("args") or {})
            out = {"ok": True}
            for k in ("toast", "select", "label", "result"):
                if res.get(k) is not None:
                    out[k] = res[k]
            if req.get("state", True):
                out["state"] = self.app.proj.state()
            return self._json(out)
        if path == "/api/export":
            res = self.app.export(req.get("opts") or {})
            return self._json({"ok": True, "result": res})
        return self._send(404, b"not found")


# ───────────────────────────── 本体（1フォルダにつき1つ） ─────────────────────────────

class App:
    def __init__(self, folder: Path, port: int = 0):
        self.proj = Project(folder)
        self.token = secrets.token_urlsafe(24)
        self.port_wanted = port
        self.stopping = threading.Event()
        self.httpd = None
        self.port = 0
        self.started = time.time()
        self.export_lock = threading.Lock()
        self.exports: set = set()

    def start(self):
        self.proj.open()
        self.httpd = Server(self, self.port_wanted)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.5},
                         name="http", daemon=True).start()
        self.workers = Workers(self.proj, self.stopping)
        self.workers.start()
        self.write_instance()
        log.info("待ち受け開始 127.0.0.1:%d", self.port)
        return self

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    def op(self, name, args):
        proj = self.proj
        if name in ("reveal", "external"):
            rel = clean_rel(args.get("rel"))
            if not rel:
                raise OpError("ファイルが指定されていません")
            path = proj.abs(rel)
            if not path.exists():
                raise OpError("そのファイルは今フォルダにありません")
            if name == "reveal":
                reveal_in_explorer(path)
            else:
                open_external(path, proj.doc["settings"].get("external_cmd") or "")
            return {"changed": False}
        if name == "open_folder":
            open_path(proj.folder)
            return {"changed": False}
        if name == "window":
            save_window_geometry(args)
            return {"changed": False}
        if name == "new_window":
            open_ui(self.url, load_config().get("window"))
            return {"changed": False}
        if name == "pick_folder":
            return {"changed": False, "result": pick_folder_dialog(str(proj.folder))}
        if name == "open_dest":
            dest = str(args.get("path") or "")
            if dest not in self.exports:
                raise OpError("そのフォルダは開けません")
            open_path(Path(dest))
            return {"changed": False}
        return proj.run(name, args)

    def export(self, opts):
        if not self.export_lock.acquire(blocking=False):
            raise OpError("いま書き出し中です")
        try:
            def progress(i, n):
                if i == n or i % 4 == 0:
                    self.proj.hub.publish("export", {"done": i, "total": n})
            res = self.proj.export(opts, progress)
            self.exports.add(res["dest"])
            log.info("書き出し: %s", res)
            return res
        finally:
            self.export_lock.release()

    def run_forever(self):
        hub = self.proj.hub
        while not self.stopping.wait(1.0):
            if hub.clients > 0:
                continue
            now = time.time()
            if hub.ever:
                grace = 4 if hub.bye_at and now - hub.bye_at < 60 else 25
                if now - hub.changed_at >= grace:
                    break
            elif now - self.started > 120:
                log.info("画面がつながらないまま時間が過ぎたので終わります")
                break
        self.shutdown()

    def shutdown(self):
        self.stopping.set()
        try:
            self.proj.save()
        except Exception:
            log.exception("終わるときの保存で問題が起きました")
        try:
            if self.httpd:
                self.httpd.shutdown()
                self.httpd.server_close()
        except Exception:
            pass
        self.remove_instance()
        log.info("終了")

    def write_instance(self):
        data = {"pid": os.getpid(), "port": self.port, "token": self.token,
                "folder": str(self.proj.folder), "started": now_iso()}
        try:
            atomic_write(self.proj.ddir / "instance.json", dump_json(data))
        except OSError:
            log.exception("instance.json を書けません")

    def remove_instance(self):
        p = self.proj.ddir / "instance.json"
        try:
            if load_json(p).get("pid") == os.getpid():
                p.unlink()
        except Exception:
            pass


def find_running(folder: Path):
    """同じフォルダをもう開いている本体があれば、その情報"""
    try:
        d = load_json(folder / DATA_DIR / "instance.json")
        if os.path.normcase(str(d.get("folder"))) != os.path.normcase(str(folder)):
            return None
        port = int(d["port"])
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/ping", headers={"X-Token": d["token"]})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=1.5) as r:
            info = json.loads(r.read().decode("utf-8"))
        if info.get("ok") and os.path.normcase(info.get("folder", "")) == os.path.normcase(str(folder)):
            return d
    except Exception:
        return None
    return None


def post_running(d, op, args=None):
    try:
        body = json.dumps({"op": op, "args": args or {}, "state": False}).encode("utf-8")
        req = urllib.request.Request(f"http://127.0.0.1:{int(d['port'])}/api/op", data=body,
                                     headers={"X-Token": d["token"], "Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


# ───────────────────────────── Windows とのつなぎ ─────────────────────────────

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


def message_box(text, title=APP_NAME, error=False):
    if IS_WIN:
        try:
            flags = (0x10 if error else 0x40) | 0x10000 | 0x40000   # アイコン + 前面に出す
            ctypes.windll.user32.MessageBoxW(None, text, title, flags)
            return
        except Exception:
            pass
    out = sys.stderr or sys.stdout
    if out:
        print(f"[{title}] {text}", file=out)


def confirm_box(text, title=APP_NAME) -> bool:
    """はい／いいえ を聞く（Windows 以外では、はい）"""
    if IS_WIN:
        try:
            flags = 0x04 | 0x20 | 0x10000 | 0x40000      # はい/いいえ + ？ + 前面に出す
            return ctypes.windll.user32.MessageBoxW(None, text, title, flags) == 6
        except Exception:
            return True
    return True


def load_config() -> dict:
    try:
        d = load_json(appdata_dir() / "config.json")
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_window_geometry(a):
    try:
        w, h = int(a.get("w") or 0), int(a.get("h") or 0)
        aw, ah = int(a.get("aw") or 0), int(a.get("ah") or 0)
    except (TypeError, ValueError):
        return
    if w < 400 or h < 300:
        return
    cfg = load_config()
    cfg["window"] = {"w": w, "h": h, "aw": aw, "ah": ah}
    try:
        atomic_write(appdata_dir() / "config.json", dump_json(cfg))
    except OSError:
        pass


def find_edge():
    cands = []
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        b = os.environ.get(env)
        if b:
            cands.append(Path(b) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    if IS_WIN:
        try:
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe") as k:
                        v, _ = winreg.QueryValueEx(k, "")
                        if v:
                            cands.append(Path(v.strip('"')))
                except OSError:
                    pass
        except ImportError:
            pass
    for c in cands:
        try:
            if c.is_file():
                return c
        except OSError:
            pass
    return None


def open_ui(url, geom=None):
    """Edge のアプリ窓（タブもアドレス欄もない専用ウィンドウ）で開く。無ければいつものブラウザ"""
    edge = find_edge() if IS_WIN else None
    if edge:
        args = [str(edge), f"--app={url}", f"--user-data-dir={appdata_dir() / 'edge'}",
                "--no-first-run", "--no-default-browser-check"]
        if isinstance(geom, dict) and geom.get("w"):
            w, h = int(geom["w"]), int(geom["h"])
            if geom.get("aw"):
                w = min(w, int(geom["aw"]))
            if geom.get("ah"):
                h = min(h, int(geom["ah"]))
            args.append(f"--window-size={max(800, w)},{max(560, h)}")
        else:
            args.append("--window-size=1480,940")
        try:
            subprocess.Popen(args, close_fds=True, creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
            return "edge"
        except OSError:
            log.exception("Edge を開けませんでした")
    import webbrowser
    webbrowser.open(url)
    return "browser"


def focus_window(title) -> bool:
    """同じフォルダの窓を前に出す"""
    if not IS_WIN:
        return False
    try:
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [proto, wintypes.LPARAM]
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        found = []

        def cb(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                n = user32.GetWindowTextLengthW(hwnd)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(hwnd, buf, n + 1)
                    if buf.value.startswith(title):
                        found.append(hwnd)
            return True

        user32.EnumWindows(proto(cb), 0)
        for h in found:
            if user32.IsIconic(h):
                user32.ShowWindow(h, 9)
            if not user32.SetForegroundWindow(h):
                user32.keybd_event(0x12, 0, 0, 0)          # Alt を一瞬押すと前に出せるようになる
                user32.keybd_event(0x12, 0, 2, 0)
                user32.SetForegroundWindow(h)
            return True
    except Exception:
        log.exception("窓を前に出せませんでした")
    return False


def reveal_in_explorer(path: Path):
    if IS_WIN:
        subprocess.Popen(f'explorer /select,"{path}"')
    else:
        open_path(path.parent)


def open_path(path: Path):
    if IS_WIN:
        os.startfile(str(path))
    elif shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", str(path)])


def open_external(path: Path, cmd: str):
    cmd = (cmd or "").strip()
    if not cmd:
        open_path(path)
        return
    line = cmd.replace("{file}", str(path)) if "{file}" in cmd else f'{cmd} "{path}"'
    try:
        subprocess.Popen(line if IS_WIN else line.split(), close_fds=True)
    except OSError as e:
        raise OpError(f"外部アプリを起動できません（{e.strerror or e}）。設定のコマンドを確かめてください")


def ps_quote(s) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def run_powershell(script: str, timeout=120):
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-EncodedCommand", enc], capture_output=True, timeout=timeout,
                       creationflags=CREATE_NO_WINDOW if IS_WIN else 0)
    return r.returncode, r.stdout.decode("utf-8-sig", "replace").lstrip("﻿"), r.stderr.decode("utf-8", "replace")


def pick_folder_dialog(initial: str):
    if not IS_WIN:
        raise OpError("フォルダを選ぶ窓は Windows でだけ使えます")
    script = f"""
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = '書き出し先のフォルダを選んでください'
$d.SelectedPath = {ps_quote(initial)}
$d.ShowNewFolderButton = $true
$owner = New-Object System.Windows.Forms.Form -Property @{{TopMost=$true}}
if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {{ Write-Output $d.SelectedPath }}
"""
    try:
        code, out, err = run_powershell(script, timeout=900)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise OpError(f"フォルダを選ぶ窓を開けません（{e}）")
    out = out.strip()
    return out or None


def ask_folder():
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        d = filedialog.askdirectory(title=f"{APP_NAME}：開く画像フォルダを選んでください")
        root.destroy()
        return Path(d) if d else None
    except Exception:
        log.exception("フォルダを選ぶ窓を開けません")
        return None


# ───────────────────────────── セットアップ（setup.bat から） ─────────────────────────────

RELAY_TEMPLATE = '''# -*- coding: utf-8 -*-
# 台割工房への中継（古い「台割を開く.bat」から呼ばれます）。台割工房の setup が作りました。
import os
import subprocess
import sys

PYW = {pyw}
APP = {app}


def main():
    target = None
    for a in sys.argv[1:]:
        a = a.strip().strip('"')
        if a.startswith("-") or not a:
            continue
        if len(a) > 3:
            a = a.rstrip("\\\\/")
        if os.path.exists(a):
            target = a
    if target is None:
        target = os.getcwd()
    subprocess.Popen([PYW, APP, target], close_fds=True, creationflags=0x00000008 | 0x00000200)


if __name__ == "__main__":
    main()
'''


def _reg_set(path, value, name=""):
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, "Software\\Classes\\" + path, 0, winreg.KEY_WRITE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)


def _reg_delete_tree(path):
    import winreg
    root = winreg.HKEY_CURRENT_USER
    full = "Software\\Classes\\" + path
    try:
        with winreg.OpenKey(root, full, 0, winreg.KEY_ALL_ACCESS) as k:
            while True:
                try:
                    sub = winreg.EnumKey(k, 0)
                except OSError:
                    break
                _reg_delete_tree(path + "\\" + sub)
        winreg.DeleteKey(root, full)
    except FileNotFoundError:
        pass


def _notify_assoc():
    try:
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)
    except Exception:
        pass


def register_association(pyw: Path, app: Path, icon: Path):
    prog = "DaiwariKobo.Project"
    _reg_set(".daiwari", prog)
    _reg_set(".daiwari\\OpenWithProgids", "", prog)
    _reg_set(prog, f"{APP_NAME} の台割")
    _reg_set(prog + "\\DefaultIcon", f"{icon},0")
    _reg_set(prog + "\\shell", "open")
    _reg_set(prog + "\\shell\\open", f"{APP_NAME}で開く")
    _reg_set(prog + "\\shell\\open\\command", f'"{pyw}" "{app}" "%1"')
    _notify_assoc()


def make_shortcuts(pyw: Path, app: Path, icon: Path):
    script = f"""
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
$ws = New-Object -ComObject WScript.Shell
foreach ($d in @([Environment]::GetFolderPath('SendTo'), [Environment]::GetFolderPath('Desktop'))) {{
  $p = Join-Path $d {ps_quote(APP_NAME + '.lnk')}
  $s = $ws.CreateShortcut($p)
  $s.TargetPath = {ps_quote(pyw)}
  $s.Arguments = {ps_quote('"' + str(app) + '"')}
  $s.WorkingDirectory = {ps_quote(app.parent)}
  $s.IconLocation = {ps_quote(str(icon) + ',0')}
  $s.Description = {ps_quote(APP_NAME + '（画像フォルダを落とすと開きます）')}
  $s.Save()
  Write-Output $p
}}
"""
    code, out, err = run_powershell(script)
    if code != 0:
        raise RuntimeError(err.strip() or f"PowerShell の終了コード {code}")
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def remove_shortcuts():
    script = f"""
foreach ($d in @([Environment]::GetFolderPath('SendTo'), [Environment]::GetFolderPath('Desktop'))) {{
  $p = Join-Path $d {ps_quote(APP_NAME + '.lnk')}
  if (Test-Path -LiteralPath $p) {{ Remove-Item -LiteralPath $p -Force }}
}}
"""
    run_powershell(script)


def install_relay(pyw: Path, app: Path):
    base = Path(os.environ.get("LOCALAPPDATA") or appdata_dir().parent)
    relay = base / APP_ID / "relay"
    relay.mkdir(parents=True, exist_ok=True)
    (relay / "daiwari.py").write_text(RELAY_TEMPLATE.format(pyw=repr(str(pyw)), app=repr(str(app))),
                                      encoding="utf-8", newline="\r\n")
    mdir = base / "MangaDaiwari"
    mdir.mkdir(parents=True, exist_ok=True)
    home = mdir / "home.txt"
    bak = mdir / "home.txt.v2"
    try:
        if home.exists() and not bak.exists() and str(relay).encode("utf-8") not in home.read_bytes():
            shutil.copy2(home, bak)
    except OSError:
        pass
    text = str(relay)
    try:
        data = text.encode("cp932")          # バッチ（cmd）が読む文字コード
    except UnicodeEncodeError:
        data = text.encode("utf-8")
    home.write_bytes(data + b"\r\n")
    return relay


def setup_main():
    print(f"{APP_NAME} {VERSION} のセットアップ")
    print("-" * 40)
    if not IS_WIN:
        print("このセットアップは Windows 用です。")
        return 1
    app = Path(__file__).resolve()
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    if not pyw.exists():
        pyw = exe
    icon = app.with_name(f"{APP_NAME}.ico")
    if not icon.exists():
        print(f"！ {icon.name} が見つかりません。本体と同じフォルダに置いてから、もう一度実行してください。")
    ok = True
    try:
        register_association(pyw, app, icon)
        print("○ 「台割.daiwari」をダブルクリックで開けるようにしました")
    except Exception as e:
        ok = False
        print(f"× 関連付けに失敗しました：{e}")
    try:
        for p in make_shortcuts(pyw, app, icon):
            print(f"○ ショートカットを作りました：{p}")
        print("   （フォルダの右クリック →「送る」→「台割工房」。Windows 11 では、いちばん下の「その他のオプション」の中）")
    except Exception as e:
        ok = False
        print(f"× ショートカットを作れませんでした：{e}")
    try:
        relay = install_relay(pyw, app)
        print(f"○ 古い「台割を開く.bat」からも開けるよう、中継を置きました：{relay}")
    except Exception as e:
        print(f"△ 古い bat 用の中継を置けませんでした：{e}")
    if not have_pillow():
        ok = False
        print("× Pillow が入っていません（setup.bat から入れ直してください）")
    edge = find_edge()
    print(f"○ Edge：{edge}" if edge else "△ Edge が見つからないので、いつものブラウザのタブで開きます")
    print("-" * 40)
    print("セットアップが終わりました。" if ok else "うまくいかなかった項目があります。上の × をご確認ください。")
    print("本体の場所を移したときは、移した先でもう一度 setup.bat を実行してください。")
    return 0 if ok else 1


def uninstall_main():
    if not IS_WIN:
        return 1
    for k in ("DaiwariKobo.Project", ".daiwari"):
        try:
            _reg_delete_tree(k)
        except Exception as e:
            print(f"△ {k}: {e}")
    _notify_assoc()
    try:
        remove_shortcuts()
    except Exception as e:
        print(f"△ ショートカット: {e}")
    base = Path(os.environ.get("LOCALAPPDATA") or appdata_dir().parent)
    bak = base / "MangaDaiwari" / "home.txt.v2"
    if bak.exists():
        shutil.copy2(bak, base / "MangaDaiwari" / "home.txt")
    shutil.rmtree(base / APP_ID / "relay", ignore_errors=True)
    print("関連付けとショートカットを外しました。各フォルダの 台割.daiwari と .daiwari はそのまま残っています。")
    return 0


# ───────────────────────────── 起動 ─────────────────────────────

def setup_logging(folder: Path | None):
    log.setLevel(logging.INFO)
    for h in list(log.handlers):
        log.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s")
    target = (folder / DATA_DIR / "log.txt") if folder else (appdata_dir() / "log.txt")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(target, maxBytes=1_000_000, backupCount=1, encoding="utf-8")
        h.setFormatter(fmt)
        log.addHandler(h)
    except OSError:
        pass
    if sys.stderr is not None and os.environ.get("DAIWARI_STDERR_LOG"):
        log.addHandler(logging.StreamHandler())


def resolve_target(a: str):
    p = Path(a.strip().strip('"'))
    try:
        if p.is_file():
            return p.resolve().parent
        if p.is_dir():
            return p.resolve()
    except OSError:
        return None
    return None


def run_folder(folder: Path, port=0, window=True):
    setup_logging(folder)
    log.info("起動 %s %s / Python %s / %s", APP_NAME, VERSION, sys.version.split()[0], folder)
    if not have_pillow():
        message_box("画像を扱う部品（Pillow）が入っていません。\n台割工房のフォルダの setup.bat をもう一度実行してください。", error=True)
        return 2
    if not (folder / PROJECT_FILE).exists() and not (folder / LEGACY_FILE).exists():
        n = len(list_images(folder))
        if n > 1500 and not confirm_box(
                f"このフォルダには画像が {n:,d} 枚あります。\n\n{folder}\n\n"
                "ここに台割を作りますか？（生成の出力フォルダなどを開いたときは「いいえ」を）"):
            return 0
    running = find_running(folder)
    if running:
        if not focus_window(f"{folder.name} — {APP_NAME}"):
            post_running(running, "new_window")
        return 0
    app = App(folder, port=port)
    try:
        app.start()
    except PermissionError as e:
        log.exception("書き込めません")
        message_box(f"このフォルダに書き込めないため、台割工房を開けません。\n\n{folder}\n\n（{e}）", error=True)
        return 3
    if window:
        open_ui(app.url, load_config().get("window"))
    else:
        print(app.url, flush=True)
    app.run_forever()
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--version" in argv:                  # update.bat が版を見せるのに使う
        if sys.stdout:
            print(f"{APP_NAME} {VERSION}")
        return 0
    if "--setup" in argv:
        return setup_main()
    if "--uninstall" in argv:
        return uninstall_main()
    port, window, paths = 0, True, []
    it = iter(argv)
    for a in it:
        if a == "--port":
            port = int(next(it, "0") or 0)
        elif a == "--no-window":
            window = False
        elif a.startswith("--"):
            continue
        else:
            paths.append(a)
    if not paths:
        setup_logging(None)
        f = ask_folder()
        if not f:
            return 0
        paths = [str(f)]
    for extra in paths[1:]:          # 「送る」で複数のフォルダを選んだとき
        t = resolve_target(extra)
        if t:
            subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(t)], close_fds=True,
                             creationflags=(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP) if IS_WIN else 0)
    target = resolve_target(paths[0])
    if target is None:
        message_box(f"開くフォルダが見つかりません。\n\n{paths[0]}", error=True)
        return 1
    return run_folder(target, port=port, window=window)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        try:
            log.exception("起動できませんでした")
        except Exception:
            pass
        message_box(f"台割工房で問題が起きました。\n\n{exc}\n\n{traceback.format_exc(limit=3)}", error=True)
        sys.exit(1)
