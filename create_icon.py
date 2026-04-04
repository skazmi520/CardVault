#!/usr/bin/env python3
"""
Generate CardVault.icns and icon.png using Pillow.
Run from the CardVaultMac directory:  python3 create_icon.py
"""

import os
import shutil
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


# ── palette ───────────────────────────────────────────────────────────────────
BG_BLUE   = (26,  109, 212, 255)   # #1A6DD4  background
MID_BLUE  = (15,   80, 170, 255)   # darker shadow edge
CARD_WHITE = (255, 255, 255, 255)
CARD_TINT  = (220, 234, 255, 255)  # very light blue for back cards


def _rounded_rect_mask(size, radius):
    """Return an 'L'-mode mask with a filled rounded rectangle."""
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255
    )
    return mask


def _card(width, height, corner, fill):
    """Return an RGBA card image."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle(
        [0, 0, width - 1, height - 1], radius=corner, fill=fill
    )
    return img


def make_icon(size: int) -> Image.Image:
    s  = size
    bg_r = max(4, int(s * 0.18))   # background corner radius

    # ── base canvas (transparent) ─────────────────────────────────────────────
    img  = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ── gradient background (top → bottom via per-row lines) ──────────────────
    top = BG_BLUE[:3]
    bot = MID_BLUE[:3]
    for y in range(s):
        t   = y / max(s - 1, 1)
        col = tuple(int(top[i] * (1 - t) + bot[i] * t) for i in range(3))
        draw.line([(0, y), (s - 1, y)], fill=col)

    # Clip gradient to rounded rectangle
    mask = _rounded_rect_mask((s, s), bg_r)
    img.putalpha(mask)

    # ── card dimensions ───────────────────────────────────────────────────────
    cw = int(s * 0.40)          # card width  (~2.5 : 3.5 ratio)
    ch = int(s * 0.56)          # card height
    cr = max(2, int(s * 0.04))  # card corner radius

    # Centre of composition (slightly above middle)
    cx = int(s * 0.50)
    cy = int(s * 0.50)

    def paste_card(card_img, angle, off_x, off_y):
        """Rotate card_img and composite onto img at offset from (cx, cy)."""
        if angle != 0:
            rot = card_img.rotate(angle, expand=True, resample=Image.BICUBIC)
        else:
            rot = card_img
        rw, rh = rot.size
        dest = (cx - rw // 2 + off_x, cy - rh // 2 + off_y)
        img.alpha_composite(rot, dest)

    # ── back card (most rotated, most transparent) ────────────────────────────
    back_fill = (*CARD_TINT[:3], 170)
    paste_card(_card(cw, ch, cr, back_fill),
               angle=18,
               off_x=-int(s * 0.10),
               off_y=int(s * 0.02))

    # ── mid card ──────────────────────────────────────────────────────────────
    mid_fill = (*CARD_TINT[:3], 210)
    paste_card(_card(cw, ch, cr, mid_fill),
               angle=7,
               off_x=-int(s * 0.02),
               off_y=int(s * 0.00))

    # ── front card (upright, fully opaque) ───────────────────────────────────
    paste_card(_card(cw, ch, cr, CARD_WHITE),
               angle=0,
               off_x=int(s * 0.07),
               off_y=-int(s * 0.02))

    # ── badge on front card ───────────────────────────────────────────────────
    # Centre of front card in img coordinates
    fcx = cx + int(s * 0.07)
    fcy = cy - int(s * 0.02)
    br  = int(s * 0.14)

    draw.ellipse(
        [fcx - br, fcy - br, fcx + br, fcy + br],
        fill=BG_BLUE
    )

    # ── "CV" text inside badge ────────────────────────────────────────────────
    target_font_size = int(br * 1.1)
    font = None
    for path in [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNSDisplay.ttf",
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    ]:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, target_font_size)
                break
            except Exception:
                pass

    text = "CV"
    if font:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw   = bbox[2] - bbox[0]
        th   = bbox[3] - bbox[1]
        tx   = fcx - tw // 2 - bbox[0]
        ty   = fcy - th // 2 - bbox[1]
        draw.text((tx, ty), text, font=font, fill=(255, 255, 255, 255))
    else:
        # Fallback: two bold rectangles forming "CV" shape
        bw = int(br * 0.28)
        bh = int(br * 0.90)
        gap = int(br * 0.15)
        lx  = fcx - bw - gap // 2
        ly  = fcy - bh // 2
        draw.rectangle([lx, ly, lx + bw, ly + bh],
                       fill=(255, 255, 255, 255))
        draw.rectangle([lx + bw + gap, ly, lx + bw * 2 + gap, ly + bh],
                       fill=(255, 255, 255, 255))

    return img


# ── iconset sizes required by Apple ──────────────────────────────────────────
ICONSET_SIZES = [16, 32, 128, 256, 512]


def build_icns(out_dir: Path) -> Path:
    iconset = out_dir / "CardVault.iconset"
    iconset.mkdir(exist_ok=True)

    for sz in ICONSET_SIZES:
        img = make_icon(sz)
        img.save(iconset / f"icon_{sz}x{sz}.png")
        img2x = make_icon(sz * 2)
        img2x.save(iconset / f"icon_{sz}x{sz}@2x.png")

    icns_path = out_dir / "CardVault.icns"
    result = subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(icns_path)],
        capture_output=True, text=True,
    )
    shutil.rmtree(iconset)

    if result.returncode != 0:
        raise RuntimeError(f"iconutil failed: {result.stderr.strip()}")
    return icns_path


if __name__ == "__main__":
    here = Path(__file__).parent

    # Save a 512×512 PNG for the window / README
    png_path = here / "icon.png"
    make_icon(512).save(png_path)
    print(f"✅  icon.png saved")

    # Build .icns
    icns_path = build_icns(here)
    print(f"✅  {icns_path.name} saved")
