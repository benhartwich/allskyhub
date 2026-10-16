"""The Play Store graphics from the app icon: icon (512 x 512) and one feature graphic
(1024 x 500, no transparency) per language, written to app/store/play/.

    uv run python tools/store_graphics.py
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

APP = Path(__file__).resolve().parents[1] / "app"
OUT = APP / "store" / "play"
FONT = APP / "assets" / "fonts" / "SpaceGrotesk.ttf"
W, H = 1024, 500
CLAIMS = {
    "de-DE": "Dein ganzer Himmel,\nTag und Nacht.",
    "en-US": "Your whole sky,\nday and night.",
}


def _gradient() -> Image.Image:
    """The icon's night sky: deep blue above, near black below, two soft aurora glows."""
    sky = Image.new("RGB", (W, H))
    top, mid, bottom = (10, 18, 48), (4, 6, 15), (2, 3, 10)
    draw = ImageDraw.Draw(sky)
    for y in range(H):
        t = y / (H - 1)
        a, b, f = (top, mid, t / 0.6) if t < 0.6 else (mid, bottom, (t - 0.6) / 0.4)
        draw.line([(0, y), (W, y)], fill=tuple(round(a[i] + (b[i] - a[i]) * f) for i in range(3)))
    glow = Image.new("RGB", (W, H))
    g = ImageDraw.Draw(glow)
    g.ellipse((-200, -330, 520, 260), fill=(79, 227, 176))
    g.ellipse((560, -320, 1240, 230), fill=(180, 140, 255))
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    return Image.blend(sky, glow, 0.28)


def _font(size: int, weight: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(FONT), size)
    with contextlib.suppress(OSError):  # not a variable font
        font.set_variation_by_axes([weight])
    return font


def feature(claim: str) -> Image.Image:
    image = _gradient()
    icon = Image.open(APP / "assets" / "icon" / "foreground.png").convert("RGBA")
    icon = icon.resize((460, 460), Image.Resampling.LANCZOS)
    image.paste(icon, (40, 20), icon)
    draw = ImageDraw.Draw(image)
    draw.text((470, 150), "allskyhub", font=_font(96, 700), fill=(236, 240, 255))
    draw.text(
        (474, 270),
        claim,
        font=_font(40, 500),
        fill=(167, 176, 204),
        spacing=10,
    )
    return image


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    icon = Image.open(APP / "assets" / "icon" / "icon.png").convert("RGB")
    icon.resize((512, 512), Image.Resampling.LANCZOS).save(OUT / "icon.png", optimize=True)
    print(OUT / "icon.png")
    for language, claim in CLAIMS.items():
        feature(claim).save(OUT / language / "feature.png", optimize=True)
        print(OUT / language / "feature.png")


if __name__ == "__main__":
    main()
