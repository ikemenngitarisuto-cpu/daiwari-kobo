#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""試験用の画像フォルダを作る（すべて機械で描いた架空の絵。実在の作品は使わない）

  python tools/make_sample.py OUT --pages 200 --v2

作るもの
  NNN.png            原画（生成画像のつもり。PNG に架空の生成情報を埋め込む）
  台詞入り/NNN.png    台詞工房のフォルダ作業で保存した完成（吹き出し入り）
  NNN_V2.png / _V3   台詞工房のふだんの保存
  NNN_s.png          末尾の決まりで見分ける完成
  _除外/  未使用/     現行版と番付工房の隔離フォルダ
  .daiwari.json      現行版（v2）の管理データ（--v2 のとき）
"""
from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from PIL.PngImagePlugin import PngInfo

FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "C:/Windows/Fonts/YuGothB.ttc",
    "C:/Windows/Fonts/meiryob.ttc",
]

SCENES = [
    ("屋上・放課後", ((255, 183, 120), (120, 96, 168)), "sunset"),
    ("教室・朝", ((176, 214, 245), (236, 244, 252)), "day"),
    ("駅のホーム", ((70, 86, 128), (186, 160, 196)), "dusk"),
    ("夜の公園", ((18, 24, 52), (58, 66, 112)), "night"),
    ("商店街", ((246, 210, 160), (250, 236, 210)), "day"),
    ("海辺", ((126, 196, 236), (240, 226, 196)), "day"),
    ("部室", ((210, 196, 170), (236, 226, 206)), "indoor"),
    ("雨の交差点", ((90, 104, 120), (150, 160, 170)), "rain"),
]

LINES = [
    ("サキ", "あのさ"), ("ハル", "なに？"), ("サキ", "今日、時間ある？"), ("ハル", "あるけど…"),
    ("サキ", "じゃあ、ちょっと付き合って"), ("ハル", "どこに？"), ("サキ", "ないしょ"),
    ("先生", "もうすぐ下校時刻だぞ"), ("ハル", "すぐ帰ります！"), ("サキ", "走って！"),
    ("ハル", "待ってってば"), ("サキ", "ほら、見て"), ("ハル", "……きれい"), ("サキ", "でしょ"),
    ("ハル", "ずっとここにいたの？"), ("サキ", "うん、ひとりで"), ("ハル", "次は呼んでよ"),
    ("サキ", "約束ね"), ("店主", "いらっしゃい"), ("ハル", "これ、ください"),
]
NOTES = ["引きの絵。逆光", "表情アップ", "間を取る", "見開きで見せたい", "背景を少し暗く",
         "モノローグ多め", "ここで場面転換", "効果音を入れる"]


def font(size):
    for f in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def draw_scene(seed: int, w: int, h: int) -> Image.Image:
    rng = random.Random(seed)
    name, (top, bottom), mood = SCENES[seed % len(SCENES)]
    g = Image.linear_gradient("L").resize((w, h))
    img = Image.composite(Image.new("RGB", (w, h), bottom), Image.new("RGB", (w, h), top), g)
    d = ImageDraw.Draw(img, "RGBA")
    # 光（太陽・月・照明）
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    cx, cy = rng.randint(w // 5, w * 4 // 5), rng.randint(h // 10, h // 3)
    r = rng.randint(w // 10, w // 5)
    col = (255, 240, 200, 200) if mood != "night" else (230, 236, 255, 190)
    gd.ellipse((cx - r * 2, cy - r * 2, cx + r * 2, cy + r * 2), fill=col[:3] + (60,))
    gd.ellipse((cx - r, cy - r, cx + r, cy + r), fill=col)
    img.paste(glow.filter(ImageFilter.GaussianBlur(r // 2)), (0, 0), glow.filter(ImageFilter.GaussianBlur(r // 2)))
    # 遠景（建物・山）
    horizon = int(h * rng.uniform(0.55, 0.7))
    far = lerp(top, (30, 30, 50), 0.55)
    x = 0
    while x < w:
        bw = rng.randint(w // 14, w // 6)
        bh = rng.randint(h // 12, h // 3)
        d.rectangle((x, horizon - bh, x + bw, horizon), fill=far + (230,))
        for wy in range(horizon - bh + 12, horizon - 8, 22):
            for wx in range(x + 8, x + bw - 8, 18):
                if rng.random() < (0.5 if mood in ("night", "dusk") else 0.15):
                    d.rectangle((wx, wy, wx + 7, wy + 9), fill=(255, 226, 150, 200))
        x += bw + rng.randint(0, 12)
    # 地面
    ground = lerp(bottom, (40, 40, 48), 0.5)
    d.rectangle((0, horizon, w, h), fill=ground + (255,))
    for k in range(12):
        y = horizon + int((h - horizon) * (k / 12) ** 1.6)
        d.line((0, y, w, y), fill=(255, 255, 255, 18), width=1)
    # 人物（シルエット）
    for k in range(rng.choice((1, 1, 2))):
        px = int(w * rng.uniform(0.28, 0.72)) + (k * w // 4 if k else 0) - (w // 8 if k else 0)
        scale = rng.uniform(0.85, 1.25)
        hr = int(w * 0.075 * scale)
        py = int(h * rng.uniform(0.42, 0.55))
        body = lerp(ground, (20, 22, 30), 0.6)
        hair = rng.choice([(40, 30, 32), (90, 60, 50), (30, 34, 60), (150, 120, 90)])
        d.rounded_rectangle((px - int(hr * 1.6), py + hr, px + int(hr * 1.6), py + int(hr * 6.5)),
                            radius=hr, fill=body + (255,))
        d.ellipse((px - hr, py - hr, px + hr, py + hr), fill=(244, 222, 204, 255))
        d.pieslice((px - int(hr * 1.15), py - int(hr * 1.25), px + int(hr * 1.15), py + int(hr * 1.05)),
                   180, 360, fill=hair + (255,))
        if rng.random() < 0.6:
            d.polygon([(px - int(hr * 1.1), py - hr // 3), (px - int(hr * 1.5), py + hr * 2),
                       (px - int(hr * 0.7), py + hr)], fill=hair + (255,))
        rim = (255, 236, 200, 90) if mood != "night" else (190, 210, 255, 90)
        d.arc((px - hr, py - hr, px + hr, py + hr), 200, 320, fill=rim, width=3)
    # 雨・粒
    if mood == "rain":
        for _ in range(260):
            x0, y0 = rng.randint(0, w), rng.randint(0, h)
            d.line((x0, y0, x0 - 6, y0 + 22), fill=(220, 230, 240, 90), width=1)
    else:
        for _ in range(40):
            x0, y0 = rng.randint(0, w), rng.randint(0, horizon)
            d.ellipse((x0, y0, x0 + 3, y0 + 3), fill=(255, 255, 255, rng.randint(30, 90)))
    # 隅の通し番号（目で区別しやすいように）
    f = font(max(14, w // 34))
    d.text((w - 12, h - 10), f"#{seed:03d}", font=f, fill=(255, 255, 255, 110), anchor="rd")
    return img


def vertical_text(d, x, y, text, f, size, fill=(20, 20, 24)):
    cy = y
    for ch in text:
        if ch in "ー―…":
            d.text((x, cy + size // 2), "｜" if ch in "ー―" else "︙", font=f, fill=fill, anchor="mm")
        else:
            d.text((x, cy + size // 2), ch, font=f, fill=fill, anchor="mm")
        cy += int(size * 1.08)
    return cy


def add_bubbles(img: Image.Image, seed: int, lines) -> Image.Image:
    rng = random.Random(seed * 7 + 3)
    img = img.copy()
    w, h = img.size
    d = ImageDraw.Draw(img)
    size = max(16, w // 28)
    f = font(size)
    slots = [(0.78, 0.08), (0.2, 0.12), (0.72, 0.62), (0.24, 0.66)]
    rng.shuffle(slots)
    for (sx, sy), (_, text) in zip(slots, lines):
        cols = [text[i:i + 7] for i in range(0, len(text), 7)]
        bw = int(size * 1.35 * len(cols) + size * 1.6)
        bh = int(size * 1.08 * max(len(c) for c in cols) + size * 1.6)
        x0 = int(w * sx - bw / 2)
        y0 = int(h * sy)
        x0 = max(8, min(w - bw - 8, x0))
        y0 = max(8, min(h - bh - 8, y0))
        tail = (x0 + bw // 2 + rng.randint(-bw // 3, bw // 3), y0 + bh + size)
        d.polygon([(x0 + bw // 2 - size // 2, y0 + bh - 4), (x0 + bw // 2 + size // 2, y0 + bh - 4), tail],
                  fill=(255, 255, 255), outline=(20, 20, 24))
        d.ellipse((x0, y0, x0 + bw, y0 + bh), fill=(255, 255, 255), outline=(20, 20, 24), width=3)
        cx = x0 + bw - int(size * 1.15)
        for c in cols:
            vertical_text(d, cx, y0 + int(size * 0.8), c, f, size)
            cx -= int(size * 1.35)
    return img


def png_info(seed, scene):
    info = PngInfo()
    info.add_text("parameters", f"(試験用の架空の生成情報) {scene}, 2girls, school uniform, cinematic lighting\n"
                                f"Negative prompt: lowres\nSteps: 28, Sampler: Euler a, CFG scale: 6, Seed: {1000 + seed}, "
                                f"Size: 832x1216, Model: sample-model")
    info.add_text("prompt", json.dumps({"3": {"class_type": "KSampler", "inputs": {"seed": 1000 + seed}}}))
    return info


def make(out: Path, pages: int, v2: bool, size=(640, 936), seed0=1):
    out.mkdir(parents=True, exist_ok=True)
    fin_dir = out / "台詞入り"
    fin_dir.mkdir(exist_ok=True)
    w, h = size
    rng = random.Random(42)
    meta = []
    for n in range(1, pages + 1):
        s = seed0 + n - 1
        raw = draw_scene(s, w, h)
        name = f"{n:03d}.png"
        raw.save(out / name, pnginfo=png_info(s, SCENES[s % len(SCENES)][0]), compress_level=3)
        ln = [LINES[(n * 3 + k) % len(LINES)] for k in range(rng.choice((1, 2, 2, 3)))]
        kind = None
        if n <= int(pages * 0.30):
            add_bubbles(raw, s, ln).save(fin_dir / name, compress_level=3)
            kind = "findir"
        elif n <= int(pages * 0.40):
            add_bubbles(raw, s, ln[:1]).save(out / f"{n:03d}_V2.png", compress_level=3)
            kind = "v2"
            if n % 3 == 0:
                add_bubbles(raw, s, ln).save(out / f"{n:03d}_V3.png", compress_level=3)
                kind = "v3"
        elif n <= int(pages * 0.45):
            add_bubbles(raw, s, ln).save(out / f"{n:03d}_s.png", compress_level=3)
            kind = "suffix"
        meta.append({"n": n, "name": name, "lines": ln, "kind": kind, "scene": SCENES[s % len(SCENES)][0]})
    # 現行版の隔離フォルダと、番付工房の隔離フォルダ
    old_q = out / "_除外"
    old_q.mkdir(exist_ok=True)
    for k in range(3):
        draw_scene(900 + k, w, h).save(old_q / f"ボツ{k + 1}.png", compress_level=3)
    unused = out / "未使用"
    unused.mkdir(exist_ok=True)
    for k in range(2):
        draw_scene(950 + k, w, h).save(unused / f"候補{k + 1}.png", compress_level=3)
    if v2:
        write_v2(out, meta, rng)
    return meta


def write_v2(out: Path, meta, rng):
    """現行版（v2）の管理データ。ページ順の入れ替え・空白・除外・メモ入り"""
    order = [m for m in meta]
    if len(order) > 12:
        order[4], order[9] = order[9], order[4]
    pages = []
    statuses = ["todo", "art", "text", "done", "fix"]
    for i, m in enumerate(order):
        if m["n"] == 17:
            continue                        # 017 は除外
        fin = None
        if m["kind"] == "suffix":
            fin = f"{m['n']:03d}_s.png"     # 現行版でも末尾の決まりで組にしていた
        memo_lines = [f"{who}「{t}」" for who, t in m["lines"]]
        if i % 4 == 0:
            memo_lines.append(NOTES[i % len(NOTES)])
        st = "done" if m["kind"] in ("findir", "v3") else statuses[i % 5]
        pages.append({"id": f"v2p{i}", "file": m["name"], "fin": fin, "status": st,
                      "scene": m["scene"] if i % 6 == 0 else "", "memo": "\n".join(memo_lines)})
        if i in (7, 30):
            pages.append({"id": f"v2b{i}", "blank": True, "status": "todo", "memo": "ここに1枚足す（引きの絵）"})
    data = {"version": 2, "title": "放課後の屋上（試験用）", "pages": pages, "excluded": ["017.png"],
            "settings": {"spread": False, "thumb": 168}}
    (out / ".daiwari.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--pages", type=int, default=200)
    ap.add_argument("--v2", action="store_true")
    ap.add_argument("--size", default="640x936")
    a = ap.parse_args()
    w, h = (int(x) for x in a.size.lower().split("x"))
    make(Path(a.out), a.pages, a.v2, (w, h))
    print("作りました:", a.out)


if __name__ == "__main__":
    main()
