"""Stage: the "document camera" — render each sentence as video over the real PDF.

For every sentence segment the camera glides (same page) or crossfades (new
page) to the sentence's focus region, then drifts slowly while a highlighter
sweeps across the matched sentence. Figures named in the narration stay bright while
the rest of the page dims a little.
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
    # soft drop shadow: blurred at 1/8 scale then enlarged (full-size blur takes seconds)
    k = 8
    small = Image.new("L", (cv.width // k, cv.height // k), 0)
    ImageDraw.Draw(small).rectangle([(PAD + 10) // k, (PAD + 14) // k, (PAD + pg.width + 10) // k,
                                     (PAD + pg.height + 14) // k], fill=150)
    mask = small.filter(ImageFilter.GaussianBlur(18 / k)).resize(cv.size, Image.BILINEAR)
    cv.paste((0, 0, 0), (0, 0), mask)
    cv.paste(pg, (PAD, PAD))
    cv.save(out, compress_level=1)
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


def clamp(rect, size):
    """Shift a view so it stays inside the canvas (Pillow's box resize needs that)."""
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    x0 = min(max(x0, 0), size[0] - w) if w < size[0] else (size[0] - w) / 2
    y0 = min(max(y0, 0), size[1] - h) if h < size[1] else (size[1] - h) / 2
    return (max(x0, 0), max(y0, 0), min(x0 + w, size[0]), min(y0 + h, size[1]))


def view(canvas, rect):
    return canvas.resize((W, H), Image.BILINEAR, box=clamp(rect, canvas.size))


def hl_box(b):
    """A highlighted line's box on the canvas (px), with the marker's overhang."""
    x0, y0, x1, y1 = [int(v) for v in to_px(b)]
    return (x0 - 12, y0 - 8, x1 + 12, y1 + 8)


def marked(canvas, spec, lines=True):
    """The page as it looks once the sentence is highlighted: everything dimmed a
    little, the matched lines washed with highlighter yellow (multiply blend, so
    the ink stays crisp), and any figure kept at full brightness. Built once per segment.
    lines=False gives the same page before the highlighter has touched it."""
    hl, outline = spec.get("highlight") or [], spec.get("outline")
    if not hl and not outline:
        return None
    from PIL import ImageChops
    out = Image.blend(canvas, Image.new("RGB", canvas.size, BG), 0.25)
    if outline:                                             # the figure stays bright; no frame drawn round it
        x0, y0, x1, y1 = [int(v) for v in to_px(outline)]
        out.paste(canvas.crop((x0 - 20, y0 - 20, x1 + 20, y1 + 20)), (x0 - 20, y0 - 20))
    for b in (hl if lines else []):                         # after the figure, so its caption stays marked
        box = hl_box(b)
        region = canvas.crop(box)
        region = ImageChops.multiply(region, Image.new("RGB", region.size, (255, 232, 120)))
        out.paste(region, box[:2])
    return out


def sweep_mask(boxes, p, rect, size):
    """Frame-space mask of how far the highlighter has travelled (p = 0..1) along the
    highlighted lines, in reading order, like a pen drawn across each line in turn."""
    x0c, y0c, x1c, y1c = clamp(rect, size)
    sx, sy = W / (x1c - x0c), H / (y1c - y0c)
    mask = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(mask)
    left = p * sum(b[2] - b[0] for b in boxes)
    for b in boxes:
        w = min(b[2] - b[0], left)
        if w <= 0:
            break
        d.rectangle([(b[0] - x0c) * sx, (b[1] - y0c) * sy, (b[0] + w - x0c) * sx, (b[3] - y0c) * sy], fill=255)
        left -= w
    return mask


def hud(frame, info):
    """Chapter pill (top-left) and page number (top-right), drawn opaque (cheap)."""
    d = ImageDraw.Draw(frame)
    ft, fs = font(28, True), font(24)
    label = info.get("chapter", "")
    if label:
        tw = d.textlength(label, font=ft)
        d.rounded_rectangle([56, 44, 56 + tw + 44, 100], 28, fill=(22, 35, 58))
        d.text((78, 57), label, font=ft, fill=ACCENT)
    pg = info.get("page")
    if pg:
        txt = f"p. {pg} / {info.get('pages', '')}"
        tw = d.textlength(txt, font=fs)
        d.rounded_rectangle([W - 56 - tw - 40, 46, W - 56, 96], 25, fill=(22, 35, 58))
        d.text((W - 56 - tw - 20, 58), txt, font=fs, fill=(200, 210, 225))
    return frame


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


def quiz_card(question, options, answer, index, total, reveal=False, explanation="", think=0):
    """A 'Check yourself' question. Before the reveal all four options look alike; after
    it the correct one is filled with the accent colour and the others fade back.
    Returns the card and the y where a thinking-time bar can go."""
    white, muted, dim, box, box_dim = (245, 247, 250), (170, 185, 210), (95, 110, 135), (22, 35, 58), (17, 27, 45)
    x0, width = 210, 1500

    def draw(scale, top, reveal):
        im = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(im)
        fh, fq, fo, fe = font(int(26 * scale), True), font(int(50 * scale), True), font(int(36 * scale)), font(int(30 * scale))
        y = top
        d.text((x0, y), f"CHECK YOURSELF  ·  QUESTION {index} OF {total}", font=fh, fill=ACCENT)
        y += int(62 * scale)
        for row in wrap(d, question, fq, width):
            d.text((x0, y), row, font=fq, fill=white)
            y += int(fq.size * 1.25)
        y += int(34 * scale)
        for k, opt in enumerate(options):
            right = reveal and k == answer
            rows = wrap(d, opt, fo, width - 150)
            h = max(int(84 * scale), len(rows) * int(fo.size * 1.25) + int(34 * scale))
            d.rounded_rectangle([x0, y, x0 + width, y + h], 18, fill=ACCENT if right else (box_dim if reveal else box))
            r, cx, cy = int(25 * scale), x0 + int(52 * scale), y + h // 2
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(27, 18, 6) if right else (38, 55, 85))
            lt = "ABCD"[k]
            d.text((cx - d.textlength(lt, font=fh) / 2, cy - fh.size * 0.62), lt, font=fh,
                   fill=ACCENT if right else (white if not reveal else dim))
            ty = y + (h - len(rows) * int(fo.size * 1.25)) // 2
            for row in rows:
                d.text((x0 + int(104 * scale), ty), row, font=fo, fill=(27, 18, 6) if right else (dim if reveal else white))
                ty += int(fo.size * 1.25)
            y += h + int(16 * scale)
        if reveal and explanation:
            y += int(16 * scale)
            for row in wrap(d, explanation, fe, width):
                d.text((x0, y), row, font=fe, fill=muted)
                y += int(fe.size * 1.3)
        return im, d, y

    # measure the revealed layout (explanation included) for both cards, so the question
    # and its answer share one position and the options don't jump at the reveal
    for scale in (1.0, 0.92, 0.84, 0.76):                  # shrink until everything fits above the bar
        _, _, y = draw(scale, 110, True)
        if y <= 960:
            break
    im, d, _ = draw(scale, 110 + max(0, (960 - y) // 2), reveal)
    if think and not reveal:
        d.text((x0, 1000), "Pause and think", font=font(24), fill=dim)
    return im, 1040


def quiz_frames(spec, n_frames):
    """Frames for a quiz card: static, except a thin bar that fills while the viewer
    thinks (the silent pause after the question is read)."""
    im, bar_y = quiz_card(**spec)
    raw = im.tobytes()
    think = spec.get("think") or 0
    start = n_frames - int(round(think * FPS)) if think and not spec.get("reveal") else n_frames
    for k in range(n_frames):
        if k < start:
            yield raw
            continue
        fr = im.copy()
        d = ImageDraw.Draw(fr)
        p = (k - start + 1) / max(n_frames - start, 1)
        d.rounded_rectangle([210, bar_y, 1710, bar_y + 8], 4, fill=(38, 55, 85))
        d.rounded_rectangle([210, bar_y, 210 + max(int(1500 * p), 8), bar_y + 8], 4, fill=ACCENT)
        yield fr.tobytes()


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
    (out, pdf, cache, n_frames, spec, prev, hud_info, card_spec) = args
    x264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", "-r", str(FPS)]
    proc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                             "-r", str(FPS), "-i", "-", *x264, str(out)], stdin=subprocess.PIPE)
    if card_spec:
        if "quiz" in card_spec:
            for raw in quiz_frames(card_spec["quiz"], n_frames):
                proc.stdin.write(raw)
        else:
            raw = card(card_spec["lines"], card_spec.get("sub", "")).tobytes()
            for _ in range(n_frames):
                proc.stdin.write(raw)
        proc.stdin.close(); proc.wait()
        return out
    canvas = page_canvas(pdf, spec["page"], cache)
    hi = marked(canvas, spec)
    boxes = [hl_box(b) for b in spec.get("highlight") or []]
    base = marked(canvas, spec, lines=False) if boxes else None
    pw, ph = canvas.width - 2 * PAD, canvas.height - 2 * PAD
    r1 = camera_rect(spec["focus"], pw, ph)
    same_page = prev and prev.get("page") == spec["page"]
    r0 = camera_rect(prev["focus"], pw, ph) if same_page else r1
    old = page_canvas(pdf, prev["page"], cache) if prev and not same_page else None
    r_old = camera_rect(prev["focus"], old.width - 2 * PAD, old.height - 2 * PAD) if old else None
    moving = r0 != r1 or old is not None
    t_move = min(0.9, n_frames / FPS * 0.35) if moving else 0.0
    dur = n_frames / FPS
    # the pen starts just after the camera settles and crosses the lines during roughly
    # the first half of the sentence, so the mark keeps pace with the narration
    t_pen = t_move + 0.15
    pen = min(max((dur - t_pen) * 0.55, 0.8), 5.0)
    info = dict(hud_info, page=spec["page"])
    for k in range(n_frames):
        t = k / FPS
        m = ease(t / t_move) if t_move else 1.0
        rect = lerp(r0, r1, m)
        # slow drift after the move: 3% push-in over the rest of the segment
        drift = 0.03 * ease((t - t_move) / max(dur - t_move, 0.5)) if t > t_move else 0.0
        cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
        hw, hh = (rect[2] - rect[0]) / 2 * (1 - drift), (rect[3] - rect[1]) / 2 * (1 - drift)
        rect = [cx - hw, cy - hh, cx + hw, cy + hh]
        if old is not None and t < t_move:           # page change: crossfade from the old view
            fr = Image.blend(view(old, r_old), view(canvas, rect), m)
        elif hi is not None and t >= t_move and base is not None:     # dim, then the pen sweeps
            e = ease((t - t_move) / 0.35)
            p = min(max((t - t_pen) / pen, 0.0), 1.0)
            if e >= 1 and p >= 1:
                fr = view(hi, rect)
            else:
                fr = view(base, rect)
                if e < 1:
                    fr = Image.blend(view(canvas, rect), fr, e)
                if p > 0:
                    fr = Image.composite(view(hi, rect), fr, sweep_mask(boxes, p, rect, hi.size))
        elif hi is not None and t >= t_move:        # figure outline only: fade in once the camera settles
            e = ease((t - t_move) / 0.5)
            fr = view(hi, rect) if e >= 1 else Image.blend(view(canvas, rect), view(hi, rect), e)
        else:
            fr = view(canvas, rect)
        proc.stdin.write(hud(fr, info).tobytes())
    proc.stdin.close(); proc.wait()
    return out


def render_all(jobs, progress, workers=4):
    """jobs: list of render_segment args. Renders in parallel, reports progress."""
    done = 0
    with ProcessPoolExecutor(workers) as ex:
        for _ in ex.map(render_segment, jobs):
            done += 1
            progress(done, len(jobs), f"segment {done}/{len(jobs)}")
