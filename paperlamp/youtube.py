"""Everything YouTube needs besides the video: a title, a 1280x720 thumbnail, tags and the
upload settings, written next to the video so nothing has to be made up at upload time.

    out/youtube/
        thumbnail.png       the paper's own first page beside the short title, in PaperLamp's colours
        details.txt         title, description, tags and the settings to choose in YouTube Studio
        youtube.json        the same, for the app's Library

The export (pipeline.Job.export) copies these into a "YouTube" folder named for people, the way
FlowCast does: "<title>.mp4" (YouTube takes the file name as the first title), "<title> - Thumbnail.png",
"<title> - YouTube details.txt" and "<title> - Captions.srt".
"""
import json, pathlib, re, subprocess

from PIL import Image, ImageDraw, ImageFilter

from . import render
from .pdf import LICENSE_NOTICE, video_allowed

# what each kind of video is called in its title and on its thumbnail
KIND = {"study": ("Study Guide", "STUDY GUIDE"), "read": ("Every Sentence Explained", "EVERY SENTENCE"),
        "core": ("Method, Results and Discussion", "KEY SECTIONS"), "auto": ("Explained", "EXPLAINED"),
        "summary": ("Explained", "EXPLAINED"), "full": ("Section by Section", "EVERY SECTION"),
        "pass1": ("First Read: Is It Worth Reading?", "FIRST READ"), "pass2": ("The Evidence", "THE EVIDENCE"),
        "pass3": ("The Method in Depth", "METHOD IN DEPTH")}
STOP = set("a an the of for and or to in on with by from is are can do does how what why we our via using "
           "towards toward its their into at as".split())
W, H = 1280, 720


def short_title(title):
    """The name a viewer remembers: the part before a colon ("SWE-bench"), else the title."""
    head = re.split(r"\s*[:?]\s+", title.strip(), maxsplit=1)[0].strip()
    return head if 2 <= len(head) <= 40 else title.strip()


def youtube_title(meta, depth):
    """At most 100 characters (YouTube's limit): "<paper title> | <kind>"; a long title is
    shortened to the part before its colon, then cut at a word."""
    kind = KIND.get(depth, KIND["auto"])[0]
    title = re.sub(r"\s+", " ", meta.get("title") or "A research paper").strip().rstrip(".")
    for t in (title, short_title(title)):
        if len(t) + len(kind) + 3 <= 100:
            return f"{t} | {kind}"
    t = short_title(title)[: 100 - len(kind) - 6].rsplit(" ", 1)[0]
    return f"{t}... | {kind}"


def tags(meta, depth):
    """Tags, at most 500 characters together (YouTube's limit): what the video is, the paper's
    own key words, where it was published, and its first authors."""
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]+", meta.get("title", "")) if w.lower() not in STOP]
    out = ["research paper", "paper explained", KIND.get(depth, KIND["auto"])[0].lower(),
           short_title(meta.get("title", "")), *[w.lower() for w in words[:8]],
           str(meta.get("venue") or ""), *[a for a in (meta.get("authors") or [])[:3]]]
    seen, keep, total = set(), [], 0
    for t in out:
        t = re.sub(r"[<>,]", "", str(t)).strip()
        if t and t.lower() not in seen and total + len(t) + 1 <= 500:
            seen.add(t.lower()); keep.append(t); total += len(t) + 1
    return keep


def monetisation(lic, permission=""):
    if permission:
        return "Follow the permission you were given (" + permission + ")."
    n = LICENSE_NOTICE.get(re.sub(r"\s*\(.*?\)\s*", " ", lic or "").strip())
    return n["may"] if n else "Do not publish until the authors or the publisher give permission."


def _wrap(d, text, ft, width, lines):
    words, rows, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=ft) <= width:
            cur = t
        else:
            if cur:
                rows.append(cur)
            cur = w
    if cur:
        rows.append(cur)
    if len(rows) > lines:
        rows = rows[:lines]
        while rows[-1] and d.textlength(rows[-1] + "...", font=ft) > width:
            rows[-1] = rows[-1].rsplit(" ", 1)[0]
        rows[-1] += "..."
    return rows


def title_boxes(index):
    """The paper title's lines on page 1 (PDF points): the tallest upright lines near the top."""
    # lines starting in the margin are merged with an arXiv stamp there, and look tall
    top = [ln for ln in index if ln["page"] == 1 and ln["box"][1] < 792 * 0.4 and ln["box"][0] > 45
           and (ln["box"][2] - ln["box"][0]) > 3 * (ln["box"][3] - ln["box"][1])]
    hmax = max((ln["box"][3] - ln["box"][1]) for ln in top) if top else 0
    tall = sorted((ln["box"] for ln in top if (ln["box"][3] - ln["box"][1]) >= 0.8 * hmax), key=lambda b: b[1])
    group = []                                        # the first group of tall lines is the title
    for b in tall:
        if group and b[1] - group[-1][3] > 2.5 * (b[3] - b[1]):
            break
        group.append(b)
    return group[:3]


def _page_card(pdf, cache, index, w, h):
    """The top of the paper's first page as a straight white card, w x h px: cropped to the text
    column (no margins, no arXiv stamp in the margin), from just above the title down, with the
    title washed in highlighter yellow the way the video marks a sentence, fading out at the
    bottom edge. None if the page can't be drawn."""
    from PIL import ImageChops
    try:
        page = render.page_canvas(pdf, 1, cache)
    except Exception:
        return None
    lines = [ln for ln in (index or []) if ln["page"] == 1 and ln["box"][0] > 45 and ln["box"][2] - ln["box"][0] > 120]
    titles = title_boxes(index or [])
    if lines:
        left, right = min(l["box"][0] for l in lines) - 14, max(l["box"][2] for l in lines) + 14
        top = (min(b[1] for b in titles) if titles else min(l["box"][1] for l in lines)) - 22
    else:                                             # no text positions: trim a typical margin
        pw = (page.width - 2 * render.PAD) / render.PX
        left, right, top = pw * 0.12, pw * 0.88, 60
    page_h = (page.height - 2 * render.PAD) / render.PX
    span = (right - left) * h / w
    top = max(0, min(top, page_h - span))             # stay on the page: no dark margin at the bottom
    bottom = top + span
    px = lambda v: v * render.PX + render.PAD
    crop = page.crop((int(px(left)), int(px(top)), int(px(right)), int(px(bottom)))).resize((w, h), Image.LANCZOS)
    k = w / ((right - left) * render.PX)
    for b in titles:                                  # multiply, so the ink stays crisp under the yellow
        box = (int((b[0] - left) * render.PX * k) - 8, int((b[1] - top) * render.PX * k) - 5,
               int((b[2] - left) * render.PX * k) + 8, int((b[3] - top) * render.PX * k) + 5)
        region = crop.crop(box)
        crop.paste(ImageChops.multiply(region, Image.new("RGB", region.size, render.HILITE)), box[:2])
    fade = Image.linear_gradient("L").resize((w, 90))   # 0 at the top, 255 at the bottom
    white = Image.new("RGB", (w, 90), (255, 255, 255))
    crop.paste(white, (0, h - 90), fade)
    return crop


def thumbnail(pdf, meta, depth, out_png, cache, index=None):
    """1280x720 in PaperLamp's colours. Left: what kind of video it is, the paper's short name
    in large type (readable at phone size), the rest of the title, and where and by whom it was
    published. Right: the paper's own first page, straight, its title highlighted, on a card."""
    img = Image.new("RGB", (W, H))
    top_c, bot_c = (12, 20, 35), (24, 39, 66)
    grad = Image.linear_gradient("L").resize((W, H))
    img.paste(Image.composite(Image.new("RGB", (W, H), bot_c), Image.new("RGB", (W, H), top_c), grad))
    d = ImageDraw.Draw(img)
    M = 64                                            # outer margin
    # right: the paper card with a soft shadow
    cw, ch = 500, H - 2 * M
    cx, cy = W - M - cw, M
    card = _page_card(pdf, cache, index, cw, ch)
    if card is not None:
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([cx, cy + 14, cx + cw, cy + ch + 14], radius=16, fill=(0, 0, 0, 150))
        sh = sh.filter(ImageFilter.GaussianBlur(22))
        img.paste(sh, (0, 0), sh)
        mask = Image.new("L", (cw, ch), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, cw - 1, ch - 1], radius=16, fill=255)
        img.paste(card, (cx, cy), mask)
    text_w = (cx - M - 48) if card is not None else W - 2 * M
    # left: badge, headline, rest of the title, venue and authors
    badge = KIND.get(depth, KIND["auto"])[1]
    fb = render.font(28, True)
    bw = int(d.textlength(badge, font=fb)) + 40
    d.rounded_rectangle([M, M, M + bw, M + 52], radius=26, fill=render.ACCENT)
    d.text((M + 20, M + 10), badge, font=fb, fill=(20, 16, 10))
    head = short_title(meta.get("title") or "")
    rest = (meta.get("title") or "")[len(head):].lstrip(" :?").strip()
    size = 132
    while True:                                       # the largest size that fits two lines of the width
        fh = render.font(size, True)
        rows = _wrap(d, head, fh, text_w, 2)
        fits = max(d.textlength(r, font=fh) for r in rows) <= text_w
        if (fits and len(rows) <= 2 and not rows[-1].endswith("...")) or size <= 56:
            break
        size -= 6
    y = M + 52 + 44
    for r in rows:
        d.text((M - 4, y), r, font=fh, fill=(247, 249, 252))
        y += int(size * 1.08)
    if rest:
        fs = render.font(38)
        y += 14
        for r in _wrap(d, rest, fs, text_w, 3):
            d.text((M, y), r, font=fs, fill=(178, 192, 214))
            y += 50
    # foot: where and when it was published, in the accent colour; a long journal name gives
    # way to its abbreviation ("TOSEM 2026"), then to a smaller size
    venue, year = str(meta.get("venue") or ""), str(meta.get("year") or "")
    short = re.search(r"\(([A-Z][A-Za-z&\- ]{1,14})\)", venue)
    where = " ".join(x for x in (venue, "" if year and year in venue else year) if x)
    d.line([M, H - M - 66, M + 80, H - M - 66], fill=render.ACCENT, width=4)
    if where:
        fv = render.font(34, True)
        if d.textlength(where, font=fv) > text_w and short:
            where = " ".join(x for x in (short.group(1), "" if year and year in short.group(1) else year) if x)
        size = 34
        while d.textlength(where, font=fv) > text_w and size > 22:
            size -= 2
            fv = render.font(size, True)
        if d.textlength(where, font=fv) > text_w:
            where = _wrap(d, where, fv, text_w, 1)[0]
        d.text((M, H - M - 44), where, font=fv, fill=render.ACCENT)
    img.save(out_png)
    return out_png


def details(meta, depth, description, lic, permission="", captions_name="captions.srt", seconds=0):
    """The details file: everything to type or choose in YouTube Studio, in upload order."""
    title = youtube_title(meta, depth)
    t = tags(meta, depth)
    allowed = bool(permission) or video_allowed(lic)
    lines = [
        "TITLE", title, "",
        "DESCRIPTION", description.strip(), "",
        "TAGS", ", ".join(t), "",
        "SETTINGS IN YOUTUBE STUDIO",
        "Thumbnail: upload the thumbnail file from this folder.",
        f"Subtitles: upload {captions_name} (English).",
        "Audience: No, it's not made for kids.",
        "Category: Education.",
        "Altered or synthetic content: No. Narration in your own cloned voice, or a system voice that "
        "imitates no real person, does not need this label (YouTube Help: Disclosing use of altered or "
        "synthetic content).",
        f"License shown on YouTube: Standard YouTube License, unless the paper's license asks for "
        "the same license on adaptations (then say so in the description, as it already does).",
        f"Monetisation: {monetisation(lic, permission)}",
        "", "BEFORE YOU PUBLISH",
        "The paper's license allows this video." if allowed else
        "The paper's license does NOT allow this video and no permission is recorded. Do not publish.",
    ]
    if seconds:
        lines.insert(2, f"(length {int(seconds // 60)}:{int(seconds % 60):02d})")
    return "\n".join(lines) + "\n", dict(title=title, tags=t, allowed=allowed)


def package(job_dir, meta, depth, lic, permission="", seconds=0):
    """Write out/youtube/ for a finished job. Cheap (no model), so it can be redone any time."""
    job_dir = pathlib.Path(job_dir)
    out = job_dir / "out" / "youtube"
    out.mkdir(parents=True, exist_ok=True)
    desc = (job_dir / "out" / "description.txt").read_text(encoding="utf-8") \
        if (job_dir / "out" / "description.txt").exists() else ""
    index = json.loads((job_dir / "index.json").read_text()) if (job_dir / "index.json").exists() else []
    thumbnail(job_dir / "paper.pdf", meta, depth, out / "thumbnail.png", job_dir / "cam", index)
    text, info = details(meta, depth, desc, lic, permission, seconds=seconds)
    (out / "details.txt").write_text(text, encoding="utf-8")
    info.update(description=desc, depth=depth, kind=KIND.get(depth, KIND["auto"])[0], license=lic,
                permission=permission, seconds=seconds, paper=meta.get("title", ""),
                authors=meta.get("authors") or [], venue=meta.get("venue", ""), year=meta.get("year", ""))
    (out / "youtube.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    return info


def clone(src, dst):
    """A copy-on-write clone on APFS (no extra disk space), else a normal copy."""
    dst = pathlib.Path(dst)
    if dst.exists():
        dst.unlink()
    if subprocess.run(["cp", "-c", str(src), str(dst)], capture_output=True).returncode != 0:
        import shutil
        shutil.copyfile(src, dst)
    return dst
