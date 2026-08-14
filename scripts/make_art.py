#!/usr/bin/env python3
"""Generate the four Stranger Things release-cycle announcement banners.

Output: 1200x628 PNG per weekday, styled with the NRG NEXT Design System
palette (brand purple / yellow / red) over a Stranger Things still.
"""

import hashlib
import json
import tempfile
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageChops, ImageEnhance

W, H = 1200, 628

# --- NEXT Design System tokens -------------------------------------------
NRG_PURPLE = (0x4C, 0x00, 0x3E)
NRG_PURPLE_BRIGHT = (0x89, 0x11, 0x9F)
NRG_YELLOW = (0xFF, 0xE2, 0x1A)
NRG_RED = (0xFF, 0x4D, 0x42)
NRG_BLACK = (0x04, 0x0A, 0x12)
WHITE = (0xFF, 0xFF, 0xFF)

# --- Fonts ----------------------------------------------------------------
F_TITLE = ("/System/Library/Fonts/Supplemental/Impact.ttf", None)  # heavy condensed poster face
F_SANS_B = ("/Users/dehbair/Library/Fonts/MessinaSans-Bold.otf", None)
F_SANS_R = ("/Users/dehbair/Library/Fonts/MessinaSans-Regular.otf", None)

MARGIN = 72


def font(spec, size):
    path, idx = spec
    return ImageFont.truetype(path, size, index=idx) if idx is not None else ImageFont.truetype(path, size)


def cover_crop(im, w, h):
    """Scale to fill w x h, centre-cropped, preserving aspect."""
    im = im.convert("RGB")
    sw, sh = im.size
    scale = max(w / sw, h / sh)
    nw, nh = round(sw * scale), round(sh * scale)
    im = im.resize((nw, nh), Image.LANCZOS)
    return im.crop(((nw - w) // 2, (nh - h) // 2, (nw - w) // 2 + w, (nh - h) // 2 + h))


def linear_gradient(size, stops, horizontal=False):
    """Single-channel gradient mask. stops = [(pos 0-1, value 0-255), ...]."""
    w, h = size
    n = w if horizontal else h
    g = Image.new("L", (1, n) if not horizontal else (n, 1))
    px = g.load()
    stops = sorted(stops)
    for i in range(n):
        t = i / max(n - 1, 1)
        for j in range(len(stops) - 1):
            p0, v0 = stops[j]
            p1, v1 = stops[j + 1]
            if p0 <= t <= p1:
                f = (t - p0) / max(p1 - p0, 1e-6)
                v = int(round(v0 + (v1 - v0) * f))
                break
        else:
            v = stops[-1][1] if t > stops[-1][0] else stops[0][1]
        if horizontal:
            px[i, 0] = v
        else:
            px[0, i] = v
    return g.resize((w, h), Image.BILINEAR)


def vignette(size, strength=118):
    w, h = size
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    inset_x, inset_y = int(w * 0.13), int(h * 0.13)
    d.ellipse([-inset_x, -inset_y, w + inset_x, h + inset_y], fill=255)
    m = m.filter(ImageFilter.GaussianBlur(min(w, h) * 0.22))
    inv = ImageChops.invert(m).point(lambda v: int(v * strength / 255))
    return inv


def grain(size, sigma=11, opacity=32):
    n = Image.effect_noise(size, sigma).convert("L")
    return n.point(lambda v: int(abs(v - 128) * opacity / 128))


def track_text(draw, xy, text, fnt, fill, tracking=0, anchor_ls=True):
    """Draw text with manual letter-spacing. Returns total advance width."""
    x, y = xy
    total = 0
    for ch in text:
        if draw is not None:
            draw.text((x + total, y), ch, font=fnt, fill=fill)
        total += draw_len(fnt, ch) + tracking
    return max(total - tracking, 0)


def draw_len(fnt, ch):
    return fnt.getlength(ch)


def track_width(text, fnt, tracking=0):
    if not text:
        return 0
    return sum(fnt.getlength(c) for c in text) + tracking * (len(text) - 1)


def wrap(text, fnt, max_w, tracking=0):
    words, lines, cur = text.split(), [], ""
    for wd in words:
        trial = f"{cur} {wd}".strip()
        if track_width(trial, fnt, tracking) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    return lines


def balanced_wrap(text, fnt, max_w, tracking=0):
    """Wrap, then even out a two-line result so no line is left stranded short.

    "TODAY IS DEPLOYMENT / DAY!" becomes "TODAY IS / DEPLOYMENT DAY!".
    """
    lines = wrap(text, fnt, max_w, tracking)
    if len(lines) != 2:
        return lines
    words = text.split()
    best, best_score = lines, None
    for i in range(1, len(words)):
        a = " ".join(words[:i])
        b = " ".join(words[i:])
        wa = track_width(a, fnt, tracking)
        wb = track_width(b, fnt, tracking)
        if wa > max_w or wb > max_w:
            continue
        score = abs(wa - wb)
        if best_score is None or score < best_score:
            best, best_score = [a, b], score
    return best


def glow_text(base, lines, fnt, x, y, line_h, colour, tracking=0,
              glow_colour=None, glow_radius=18, glow_passes=3):
    """Neon title: blurred coloured glow underneath, crisp text on top."""
    glow_colour = glow_colour or colour
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    cy = y
    for ln in lines:
        track_text(ld, (x, cy), ln, fnt, glow_colour + (255,), tracking)
        cy += line_h

    glow = layer.filter(ImageFilter.GaussianBlur(glow_radius))
    for _ in range(glow_passes):
        base.alpha_composite(glow)
    # tight inner halo
    base.alpha_composite(layer.filter(ImageFilter.GaussianBlur(glow_radius / 4)))

    sharp = Image.new("RGBA", base.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(sharp)
    cy = y
    for ln in lines:
        track_text(sd, (x, cy), ln, fnt, colour + (255,), tracking)
        cy += line_h
    base.alpha_composite(sharp)


def build(day, src, title, desc, out):
    photo = cover_crop(Image.open(src), W, H)

    # Cool, desaturated, moody grade
    photo = ImageEnhance.Color(photo).enhance(0.72)
    photo = ImageEnhance.Contrast(photo).enhance(1.10)
    photo = ImageEnhance.Brightness(photo).enhance(1.18)

    canvas = photo.convert("RGBA")

    # Deep purple/black wash for the Upside Down feel
    wash = Image.new("RGBA", (W, H), NRG_PURPLE + (255,))
    wash.putalpha(48)
    canvas.alpha_composite(wash)

    # Darken bottom-left so the type has a bed to sit on
    shade = Image.new("RGBA", (W, H), NRG_BLACK + (255,))
    shade.putalpha(linear_gradient((W, H), [(0.0, 8), (0.45, 52), (1.0, 214)]))
    canvas.alpha_composite(shade)

    side = Image.new("RGBA", (W, H), NRG_BLACK + (255,))
    side.putalpha(linear_gradient((W, H), [(0.0, 105), (0.62, 22), (1.0, 0)], horizontal=True))
    canvas.alpha_composite(side)

    # Vignette + grain
    vig = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    vig.putalpha(vignette((W, H)))
    canvas.alpha_composite(vig)

    gr = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    gr.putalpha(grain((W, H)))
    canvas.alpha_composite(gr)

    d = ImageDraw.Draw(canvas)

    # --- Eyebrow: weekday, yellow, letterspaced -------------------------
    f_eyebrow = font(F_SANS_B, 19)
    eyebrow = day.upper()
    ey_y = MARGIN - 8
    track_text(d, (MARGIN, ey_y), eyebrow, f_eyebrow, NRG_YELLOW + (255,), tracking=5.5)
    ew = track_width(eyebrow, f_eyebrow, 5.5)
    d.line([(MARGIN, ey_y + 30), (MARGIN + ew, ey_y + 30)], fill=NRG_YELLOW + (200,), width=2)

    # --- Title ------------------------------------------------------------
    max_text_w = W - MARGIN * 2 - 40
    size = 96
    while size > 40:
        f_title = font(F_TITLE, size)
        lines = balanced_wrap(title.upper(), f_title, max_text_w, tracking=2.5)
        if len(lines) <= 2:
            break
        size -= 5
    f_title = font(F_TITLE, size)
    lines = balanced_wrap(title.upper(), f_title, max_text_w, tracking=2.5)
    line_h = int(size * 1.02)

    f_desc = font(F_SANS_R, 23)
    desc_lines = wrap(desc, f_desc, max_text_w - 30)
    desc_h = len(desc_lines) * 32

    block_h = len(lines) * line_h + 24 + desc_h
    title_y = H - MARGIN - 26 - block_h

    glow_text(canvas, lines, f_title, MARGIN, title_y, line_h,
              NRG_RED, tracking=2.5, glow_radius=20, glow_passes=3)

    # --- Description ------------------------------------------------------
    d = ImageDraw.Draw(canvas)
    dy = title_y + len(lines) * line_h + 22
    for ln in desc_lines:
        d.text((MARGIN + 3, dy), ln, font=f_desc, fill=(235, 232, 238, 240))
        dy += 32

    # --- Bottom brand bar -------------------------------------------------
    bar_h = 7
    bar = Image.new("RGBA", (W, bar_h))
    bd = ImageDraw.Draw(bar)
    seg = [(NRG_PURPLE, 0.46), (NRG_PURPLE_BRIGHT, 0.30), (NRG_RED, 0.16), (NRG_YELLOW, 0.08)]
    cx = 0
    for colour, frac in seg:
        wpx = int(W * frac)
        bd.rectangle([cx, 0, cx + wpx, bar_h], fill=colour + (255,))
        cx += wpx
    if cx < W:
        bd.rectangle([cx, 0, W, bar_h], fill=NRG_YELLOW + (255,))
    canvas.alpha_composite(bar, (0, H - bar_h))

    # --- NRG logo, top right ---------------------------------------------
    logo_path = REPO_ROOT / CONFIG.get("logo", "brand/nrg-logo.png")
    if logo_path.exists():
        with Image.open(logo_path) as raw:
            logo = raw.convert("RGBA")
        target_h = 38
        logo = logo.resize((round(logo.width * target_h / logo.height), target_h), Image.LANCZOS)
        # Slight knock-back so the mark sits in the image rather than on top of it.
        alpha = logo.getchannel("A").point(lambda v: int(v * 0.93))
        logo.putalpha(alpha)
        canvas.alpha_composite(logo, (W - MARGIN - logo.width, MARGIN - 14))
    else:
        print(f"  warning: logo missing at {logo_path}; skipping the mark.")

    canvas.convert("RGB").save(out, "PNG", optimize=True)
    print(f"{out}  {size}px title, {len(lines)} line(s), {len(desc_lines)} desc line(s)")


REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((REPO_ROOT / "announcements.json").read_text(encoding="utf-8"))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def fetch(url, dest):
    """Download a source still, reusing the cached copy when present."""
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        dest.write_bytes(resp.read())
    return dest


if __name__ == "__main__":
    out_dir = REPO_ROOT / "assets"
    out_dir.mkdir(exist_ok=True)
    cache = Path(tempfile.gettempdir()) / "st-announce-sources"
    cache.mkdir(exist_ok=True)

    for day, cfg in CONFIG["days"].items():
        # Key the cache on the URL, not the weekday, so swapping a source
        # image actually re-downloads instead of reusing the stale file.
        digest = hashlib.sha256(cfg["source_image"].encode("utf-8")).hexdigest()[:16]
        src_path = fetch(cfg["source_image"], cache / f"{day.lower()}-{digest}.jpg")
        with Image.open(src_path) as probe:
            if probe.size[0] < W or probe.size[1] < H:
                print(f"  note: {day} source is {probe.size[0]}x{probe.size[1]}, "
                      f"below {W}x{H}; the grade will mask the upscale.")
        build(day, src_path, cfg["title"], cfg["description"], out_dir / cfg["asset"])
