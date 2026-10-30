#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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
VERSION = "3.0.0"
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

UI_FILES = None  # @@UI_BUNDLE@@  （build.py が画面のファイルをここへ埋め込む）

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
