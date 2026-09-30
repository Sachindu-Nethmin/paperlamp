"""Stage: the "document camera" — render each sentence as video over the real PDF.

For every sentence segment the camera glides (same page) or crossfades (new
page) to the sentence's focus region, then drifts slowly while a highlighter
sweeps across the matched lines. Figures named in the narration are outlined.
Frames are drawn with Pillow and piped to ffmpeg; no browser needed.
"""
import pathlib, subprocess
from concurrent.futures import ProcessPoolExecutor

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1920, 1080, 30
BG = (14, 23, 38)
ACCENT = (240, 168, 104)
HILITE = (255, 214, 74)
DPI = 200
PX = DPI / 72                                   # page pixels per PDF point
PAD = 700                                       # dark margin around each page (px) so the camera can overshoot
FONT = "/System/Library/Fonts/Helvetica.ttc"
FONT_B = "/System/Library/Fonts/HelveticaNeue.ttc"


def font(size, bold=False):
    for path, idx in ((FONT_B, 1 if bold else 0), (FONT, 1 if bold else 0)):
        try:
            return ImageFont.truetype(path, size, index=idx)
        except Exception:
            continue
    return ImageFont.load_default()


def page_canvas(pdf, page, cache):
    """The page at 200 dpi on a dark padded canvas (cached)."""
    cache = pathlib.Path(cache); cache.mkdir(parents=True, exist_ok=True)
    out = cache / f"c{page:03d}.png"
    if out.exists():
        return Image.open(out).convert("RGB")
    raw = cache / f"r{page:03d}"
    subprocess.run(["pdftoppm", "-r", str(DPI), "-png", "-singlefile", "-f", str(page), "-l", str(page),
                    str(pdf), str(raw)], check=True)
    pg = Image.open(str(raw) + ".png").convert("RGB")
    cv = Image.new("RGB", (pg.width + 2 * PAD, pg.height + 2 * PAD), BG)
    shadow = Image.new("RGBA", cv.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rectangle([PAD + 10, PAD + 14, PAD + pg.width + 10, PAD + pg.height + 14], fill=(0, 0, 0, 150))
    cv.paste(shadow.filter(ImageFilter.GaussianBlur(18)), (0, 0), shadow.filter(ImageFilter.GaussianBlur(18)))
    cv.paste(pg, (PAD, PAD))
    cv.save(out)
    (pathlib.Path(str(raw) + ".png")).unlink(missing_ok=True)
    return cv


def to_px(box):
    return [box[0] * PX + PAD, box[1] * PX + PAD, box[2] * PX + PAD, box[3] * PX + PAD]


def camera_rect(focus, page_w_px, page_h_px):
    """A 16:9 view that frames the focus box, placed a little above centre
    (burned-in captions cover the bottom of the frame)."""
    fx0, fy0, fx1, fy1 = to_px(focus)
    fw, fh = fx1 - fx0, fy1 - fy0
    w = min(max(fw * 1.3, page_w_px * 0.5), page_w_px * 1.08)
    h = w * H / W
    if fh * 1.35 > h:
        h = min(fh * 1.35, page_h_px * 1.02)
        w = h * W / H
    cx = (fx0 + fx1) / 2
    cy = (fy0 + fy1) / 2 + h * 0.08                # focus sits at ~42% of frame height
    # keep the view over the page where possible
    left, right = PAD - 40, PAD + page_w_px + 40
    if w < right - left:
        cx = min(max(cx, left + w / 2), right - w / 2)
    else:
        cx = PAD + page_w_px / 2
    top, bot = PAD - 60, PAD + page_h_px + 60
    if h < bot - top:
        cy = min(max(cy, top + h / 2), bot - h / 2)
    return [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]


def ease(t):
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def lerp(a, b, t):
    return [x + (y - x) * t for x, y in zip(a, b)]


def view(canvas, rect):
    return canvas.transform((W, H), Image.EXTENT, tuple(rect), Image.BILINEAR)


def overlay(frame, rect, spec, t_hl, hud):
    """Highlighter, figure outline and HUD, drawn in frame coordinates."""
    rx0, ry0, rx1, ry1 = rect
    sx, sy = W / (rx1 - rx0), H / (ry1 - ry0)
    f = lambda b: [(b[0] - rx0) * sx, (b[1] - ry0) * sy, (b[2] - rx0) * sx, (b[3] - ry0) * sy]
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    hl = [f(to_px(b)) for b in spec.get("highlight", [])]
    if hl and t_hl > 0:
        # gentle dim everywhere except the highlighted lines
        dim = Image.new("RGBA", (W, H), (14, 23, 38, int(70 * min(t_hl * 2, 1))))
        md = ImageDraw.Draw(dim)
        for b in hl:
            md.rounded_rectangle([b[0] - 10, b[1] - 6, b[2] + 10, b[3] + 6], 8, fill=(0, 0, 0, 0))
        layer = Image.alpha_composite(layer, dim)
        d = ImageDraw.Draw(layer)
        total = sum(b[2] - b[0] for b in hl) or 1
        reach = total * ease(t_hl)                  # sweep left→right, line by line
        for b in hl:
            span = b[2] - b[0]
            if reach <= 0:
                break
            x1 = b[0] + min(span, reach)
            d.rounded_rectangle([b[0] - 6, b[1] - 4, x1 + 6, b[3] + 4], 6, fill=HILITE + (95,))
            reach -= span
    if spec.get("outline") and t_hl > 0:
        b = f(to_px(spec["outline"]))
        d.rounded_rectangle([b[0] - 12, b[1] - 12, b[2] + 12, b[3] + 12], 14,
                            outline=ACCENT + (int(255 * min(t_hl * 2, 1)),), width=6)
    # HUD: chapter pill (top-left) and page number (top-right)
    ft, fs = font(28, True), font(24)
    label = hud.get("chapter", "")
    if label:
        tw = d.textlength(label, font=ft)
        d.rounded_rectangle([56, 44, 56 + tw + 44, 44 + 56], 28, fill=(14, 23, 38, 215))
        d.text((78, 57), label, font=ft, fill=ACCENT + (255,))
    pg = hud.get("page")
    if pg:
        txt = f"p. {pg} / {hud.get('pages', '')}"
        tw = d.textlength(txt, font=fs)
        d.rounded_rectangle([W - 56 - tw - 40, 46, W - 56, 46 + 50], 25, fill=(14, 23, 38, 200))
        d.text((W - 56 - tw - 20, 58), txt, font=fs, fill=(200, 210, 225, 255))
    return Image.alpha_composite(frame.convert("RGBA"), layer).convert("RGB")


def card(lines, sub=""):
    """A plain title/citation card."""
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    y = 330
    for text, size, col, bold in lines:
        ft = font(size, bold)
        for row in wrap(d, text, ft, 1500):
            tw = d.textlength(row, font=ft)
            d.text(((W - tw) / 2, y), row, font=ft, fill=col)
            y += int(size * 1.3)
        y += 18
    if sub:
        ft = font(24)
        tw = d.textlength(sub, font=ft)
        d.text(((W - tw) / 2, 800), sub, font=ft, fill=(110, 125, 150))
    return im


def wrap(d, text, ft, width):
    words, rows, cur = text.split(), [], ""
    for w in words:
        t = f"{cur} {w}".strip()
        if d.textlength(t, font=ft) > width and cur:
            rows.append(cur); cur = w
        else:
            cur = t
    return rows + ([cur] if cur else [])


def render_segment(args):
    """One sentence → one mp4 segment."""
    (out, pdf, cache, n_frames, spec, prev, hud, card_spec) = args
    x264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", "-r", str(FPS)]
    proc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                             "-r", str(FPS), "-i", "-", *x264, str(out)], stdin=subprocess.PIPE)
    if card_spec:
        im = card(card_spec["lines"], card_spec.get("sub", ""))
        raw = im.tobytes()
        for _ in range(n_frames):
            proc.stdin.write(raw)
        proc.stdin.close(); proc.wait()
        return out
    canvas = page_canvas(pdf, spec["page"], cache)
    pw, ph = canvas.width - 2 * PAD, canvas.height - 2 * PAD
    r1 = camera_rect(spec["focus"], pw, ph)
    same_page = prev and prev.get("page") == spec["page"]
    r0 = camera_rect(prev["focus"], pw, ph) if same_page else r1
    old = page_canvas(pdf, prev["page"], cache) if prev and not same_page else None
    r_old = camera_rect(prev["focus"], old.width - 2 * PAD, old.height - 2 * PAD) if old else None
    moving = r0 != r1 or old is not None
    t_move = min(0.9, n_frames / FPS * 0.35) if moving else 0.0
    dur = n_frames / FPS
    for k in range(n_frames):
        t = k / FPS
        m = ease(t / t_move) if t_move else 1.0
        rect = lerp(r0, r1, m)
        # slow drift after the move: 3% push-in over the rest of the segment
        drift = 0.03 * ease((t - t_move) / max(dur - t_move, 0.5)) if t > t_move else 0.0
        cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
        hw, hh = (rect[2] - rect[0]) / 2 * (1 - drift), (rect[3] - rect[1]) / 2 * (1 - drift)
        rect = [cx - hw, cy - hh, cx + hw, cy + hh]
        fr = view(canvas, rect)
        if old is not None and t < t_move:           # page change: crossfade from the old view
            fr = Image.blend(view(old, r_old), fr, m)
        t_hl = (t - t_move) / 0.7 if t > t_move else 0.0
        fr = overlay(fr, rect, spec, t_hl, dict(hud, page=spec["page"]))
        proc.stdin.write(fr.tobytes())
    proc.stdin.close(); proc.wait()
    return out


def render_all(jobs, progress, workers=4):
    """jobs: list of render_segment args. Renders in parallel, reports progress."""
    done = 0
    with ProcessPoolExecutor(workers) as ex:
        for _ in ex.map(render_segment, jobs):
            done += 1
            progress(done, len(jobs), f"segment {done}/{len(jobs)}")
