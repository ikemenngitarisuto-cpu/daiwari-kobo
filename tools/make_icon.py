#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""台割工房のアイコンを描く（複数サイズ入りの .ico と、確認用の .png）

暗い角丸の板に台割表のマス。ひとつだけ紙の色に光るマスと、下に進行の色の帯。
画面の icon.svg と同じ図柄です。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
PAPER = (239, 232, 216)
TILE = (56, 66, 79)
STATUS = [(77, 171, 120), (77, 171, 120), (198, 160, 82), (109, 141, 214), (77, 86, 101)]


def draw(size: int) -> Image.Image:
    ss = 4
    S = size * ss
    u = S / 64.0                       # 64 マスの方眼で描く（icon.svg と同じ座標）
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # 板（上から下へ暗くなる）
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    top, bot = (29, 35, 44), (10, 12, 16)
    for y in range(S):
        t = y / max(1, S - 1)
        gd.line((0, y, S, y), fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((2 * u, 2 * u, 62 * u, 62 * u), radius=13 * u, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2.5 * u, 2.5 * u, 61.5 * u, 61.5 * u), radius=12.5 * u, outline=(56, 66, 79, 255),
                        width=max(1, int(1 * u)))
    small = size <= 24
    tiles = [(11, 10), (26.5, 10), (42, 10), (11, 29), (42, 29)]
    tw, th = (11, 15)
    if small:                          # 小さいときはマスを太らせて見やすく
        tiles = [(9, 9), (25.5, 9), (42, 9), (9, 28), (42, 28)]
        tw, th = (13, 16)
    for x, y in tiles:
        d.rounded_rectangle((x * u, y * u, (x + tw) * u, (y + th) * u), radius=1.6 * u, fill=TILE + (255,))
    # 光るマス
    px, py = (25.5, 28) if small else (26.5, 29)
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(((px - 4) * u, (py - 4) * u, (px + tw + 4) * u, (py + th + 4) * u),
                                 fill=PAPER + (110,))
    glow = glow.filter(ImageFilter.GaussianBlur(5 * u))
    img = Image.alpha_composite(img, Image.composite(glow, Image.new("RGBA", (S, S), (0, 0, 0, 0)), mask))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((px * u, py * u, (px + tw) * u, (py + th) * u), radius=1.6 * u, fill=PAPER + (255,))
    # 進行の帯
    xs = [(11, 8), (20.5, 8), (30, 8), (39.5, 6), (47, 6)]
    y0, h = (50, 4)
    if small:
        xs = [(9, 9), (19.5, 9), (30, 9), (40.5, 6.5), (48, 7)]
        y0, h = (49, 6)
    for (x, w), col in zip(xs, STATUS):
        d.rounded_rectangle((x * u, y0 * u, (x + w) * u, (y0 + h) * u), radius=1 * u, fill=col + (255,))
    return img.resize((size, size), Image.Resampling.LANCZOS)


def make(out_dir: Path, name="台割工房"):
    out_dir.mkdir(parents=True, exist_ok=True)
    imgs = [draw(s) for s in SIZES]
    big = imgs[-1]
    ico = out_dir / f"{name}.ico"
    big.save(ico, format="ICO", sizes=[(s, s) for s in SIZES], append_images=imgs[:-1])
    big.save(out_dir / "icon.png")
    sheet = Image.new("RGBA", (sum(SIZES) + 12 * len(SIZES), 256), (10, 12, 16, 255))
    x = 6
    for s, im in zip(SIZES, imgs):
        sheet.paste(im, (x, 256 - s - 10), im)
        x += s + 12
    sheet.save(out_dir / "icon_sizes.png")
    return ico


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "assets"
    print(make(out))
