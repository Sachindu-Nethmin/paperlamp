"""Draw PaperLamp's app icon (a page with one line under a highlighter) as a macOS iconset.

    python3 make_icon.py OUT.iconset   →   iconutil -c icns OUT.iconset
"""
import pathlib, sys

from PIL import Image, ImageDraw, ImageFilter

S = 1024
NAVY, PAGE, INK, HILITE, GLOW = (14, 23, 38), (246, 244, 238), (150, 158, 172), (255, 214, 74), (240, 168, 104)


def icon():
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([40, 40, S - 40, S - 40], 220, fill=NAVY)
    # lamp glow from the top right
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([520, 60, 980, 520], fill=GLOW + (120,))
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([40, 40, S - 40, S - 40], 220, fill=255)
    im.paste(glow, (0, 0), Image.composite(glow.getchannel("A"), Image.new("L", (S, S), 0), mask))
    d = ImageDraw.Draw(im)
    # the page, with a folded corner
    x0, y0, x1, y1 = 250, 190, 774, 860
    d.rounded_rectangle([x0, y0, x1, y1], 28, fill=PAGE)
    d.polygon([(x1 - 120, y0), (x1, y0 + 120), (x1 - 120, y0 + 120)], fill=(214, 210, 200))
    # text lines; one of them highlighted
    y = y0 + 170
    for i, w in enumerate([0.78, 0.92, 0.85, 0.92, 0.6, 0.88, 0.7]):
        lx1 = x0 + 60 + (x1 - x0 - 120) * w
        if i == 3:
            d.rounded_rectangle([x0 + 46, y - 22, lx1 + 14, y + 30], 14, fill=HILITE)
        d.rounded_rectangle([x0 + 60, y - 6, lx1, y + 14], 10, fill=INK if i != 3 else (90, 80, 40))
        y += 82
    return im


def main(out):
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    big = icon()
    for size in (16, 32, 128, 256, 512):
        big.resize((size, size), Image.LANCZOS).save(out / f"icon_{size}x{size}.png")
        big.resize((size * 2, size * 2), Image.LANCZOS).save(out / f"icon_{size}x{size}@2x.png")


if __name__ == "__main__":
    main(sys.argv[1])
