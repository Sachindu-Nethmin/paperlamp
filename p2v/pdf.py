"""PDF → text, sections, metadata, and cropped figures/tables. Offline (poppler).

Uses poppler's command-line tools (pdfinfo, pdftotext, pdftoppm), which ship with
`brew install poppler`. Coordinates are PDF points, origin top-left.
"""
import html, json, pathlib, re, subprocess

import numpy as np
from PIL import Image

DPI = 300
SCALE = DPI / 72
CAPTION_RE = re.compile(r"^(Figure|Fig\.|Table|TABLE|FIGURE)$")


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


# ── text ─────────────────────────────────────────────────────────────────────
def info(pdf):
    out = {}
    for line in run(["pdfinfo", str(pdf)]).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    size = re.findall(r"[\d.]+", out.get("Page size", "612 x 792"))
    return dict(pages=int(out.get("Pages", 0)), title=out.get("Title", ""),
                width=float(size[0]), height=float(size[1]))


def page_texts(pdf):
    """Layout-preserving text per page (pdftotext separates pages with form feeds)."""
    return run(["pdftotext", "-layout", str(pdf), "-"]).split("\f")


HEAD_RE = re.compile(r"^\s{0,60}((?:\d{1,2}|[A-H])(?:\.\d{1,2}){0,2})\.?\s{1,16}((?:[A-Z]|[^\x00-\x7F])[^\n]{2,90})$")
STOP_RE = re.compile(r"^\s*(R\s?EFERENCES|References|REFERENCES|Bibliography)\s*$")


def sections(pages):
    """Split the paper into numbered sections; drop references. Appendix kept, flagged."""
    lines = "\n".join(pages).splitlines()
    out, cur = [], dict(num="0", title="Front matter", lines=[])
    in_appendix = False
    for ln in lines:
        s = ln.strip()
        if STOP_RE.match(s):
            out.append(cur)
            cur = dict(num="R", title="References", lines=[], skip=True)
            continue
        if re.match(r"^\s*A\s?PPENDIX|^\s*Appendix\s*$", s):
            in_appendix = True
        m = HEAD_RE.match(ln)
        if m and len(s) < 90 and not s.endswith(".") and not re.search(r"\d{3,}", m.group(2)) \
                and sum(c.isalpha() for c in m.group(2)) >= 3 and len(m.group(2).split()) <= 12:
            if cur["lines"] or cur["num"] != "0":
                out.append(cur)
            cur = dict(num=m.group(1), title=re.sub(r"\s+", " ", m.group(2)).strip(), lines=[],
                       appendix=in_appendix or m.group(1)[0].isalpha())
            continue
        cur["lines"].append(ln)
    out.append(cur)
    res = []
    for sct in out:
        if sct.get("skip"):
            continue
        text = re.sub(r"[ \t]{2,}", " ", "\n".join(sct["lines"])).strip()
        if len(text.split()) < 25:
            continue
        res.append(dict(num=sct["num"], title=sct["title"], appendix=sct.get("appendix", False), text=text))
    return res


def chunks(secs, max_words=1500):
    """Group/split sections into LLM-sized chunks, keeping section titles."""
    out = []
    for s in secs:
        words = s["text"].split()
        for i in range(0, len(words), max_words):
            part = " ".join(words[i:i + max_words])
            out.append(dict(title=f'{s["num"]} {s["title"]}' + (f" (part {i // max_words + 1})" if i else ""),
                            appendix=s["appendix"], text=part))
    return out


# ── figures and tables ───────────────────────────────────────────────────────
def words(pdf, page):
    xml = run(["pdftotext", "-bbox", "-f", str(page), "-l", str(page), str(pdf), "-"])
    return [(float(a), float(b), float(c), float(d), html.unescape(t)) for a, b, c, d, t in re.findall(
        r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', xml)]


def lines_of(ws):
    rows = []
    for w in sorted(ws, key=lambda w: (round(w[1] / 2.5), w[0])):
        if rows and abs(rows[-1][-1][1] - w[1]) < 2.5 and w[0] - rows[-1][-1][2] < 12:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [dict(x0=min(w[0] for w in r), y0=min(w[1] for w in r), x1=max(w[2] for w in r),
                 y1=max(w[3] for w in r), text=" ".join(w[4] for w in r), n=len(r)) for r in rows]


def page_image(pdf, page, cache):
    pathlib.Path(cache).mkdir(parents=True, exist_ok=True)
    out = pathlib.Path(cache) / f"p{page:03d}.png"
    if not out.exists():
        subprocess.run(["pdftoppm", "-r", str(DPI), "-png", "-singlefile", "-f", str(page), "-l", str(page),
                        str(pdf), str(out.with_suffix(""))], check=True)
    return Image.open(out)


def _margins(lns):
    """Left edges where running text starts on this page (one per column)."""
    xs = sorted(round(l["x0"]) for l in lns if l["n"] >= 8)
    groups = []
    for x in xs:
        if groups and x - groups[-1][-1] <= 3:
            groups[-1].append(x)
        else:
            groups.append([x])
    return [g[0] for g in groups if len(g) >= 4]


def _typical_width(lns):
    ws = sorted(l["x1"] - l["x0"] for l in lns if l["n"] >= 8)
    return ws[int(len(ws) * 0.75)] if ws else 0


def _is_prose(ln, typ, margins):
    """Running text: many words, starts at a text margin, about as wide as the
    page's normal text lines. (Table cells with long descriptions start
    mid-column, so they don't count.)"""
    wide = (ln["x1"] - ln["x0"]) > 0.8 * typ or ln["text"].rstrip().endswith((".", ":", ";", ")"))
    return ln["n"] >= 6 and wide and any(abs(ln["x0"] - m) <= 4 for m in margins)


def _captions(ws):
    """Caption starts: a 'Figure/Fig./Table' word followed by 'N:' or 'N.' — several may share a line."""
    ws = sorted(ws, key=lambda w: (round(w[1] / 2.5), w[0]))
    out = []
    for i, w in enumerate(ws[:-1]):
        if not CAPTION_RE.match(w[4]):
            continue
        # a caption starts its line: "...see Appendix Table 18." is a mention, not a caption
        if any(abs(u[1] - w[1]) < 2.5 and w[0] - 40 < u[2] <= w[0] + 0.5 for u in ws if u is not w):
            continue
        nxt = ws[i + 1]
        m = re.match(r"^(\d+|[A-Z]\d*)([:.])?$", nxt[4])
        if not m or abs(nxt[1] - w[1]) > 2.5:
            continue
        if not m.group(2):                                    # "Table 2 :" / "Fig. 3 ." split tokens
            if i + 2 >= len(ws) or ws[i + 2][4] not in (":", "."):
                continue
        # the caption's own words: same line, until another caption word or a big gap
        line, last = [w, nxt], nxt
        for u in ws[i + 2:]:
            if abs(u[1] - w[1]) > 2.5 or u[0] - last[2] > 12 or CAPTION_RE.match(u[4]):
                break
            line.append(u); last = u
        kind = "table" if w[4].lower().startswith("tab") else "figure"
        out.append(dict(kind=kind, label=f"{'Table' if kind == 'table' else 'Figure'} {m.group(1)}",
                        x0=w[0], y0=min(u[1] for u in line), x1=max(u[2] for u in line),
                        y1=max(u[3] for u in line), text=" ".join(u[4] for u in line)))
    return out


def find_figures(pdf, cache, progress=lambda i, n: None):
    """Detect 'Figure N:' / 'Table N.' captions and crop the graphic they label.

    The region runs from the caption to the nearest line of running prose (or
    the page margin), limited to the caption's column. Figures sit above their
    captions; tables may sit either side, so both are tried and the larger kept.
    The crop is then trimmed of whitespace.
    """
    meta = info(pdf)
    W, H = meta["width"], meta["height"]
    found, seen = [], set()
    for page in range(1, meta["pages"] + 1):
        progress(page - 1, meta["pages"])
        ws = words(pdf, page)
        lns = lines_of(ws)
        margins = _margins(lns)
        typ = _typical_width(lns)
        caps = _captions(ws)
        for cap in caps:
            if cap["label"] in seen:                           # a later mention, not the caption
                continue
            # column: the caption's own width (figures/tables are as wide as their
            # captions, incl. side-by-side layouts), or the full text width
            cap_x1 = max([cap["x1"]] + [l["x1"] for l in lns if 0 < l["y0"] - cap["y1"] < 30
                                         and l["x0"] >= cap["x0"] - 4 and l["x0"] < cap["x1"]])
            centred = abs((cap["x0"] + cap_x1) / 2 - W / 2) < 0.06 * W
            if cap_x1 - cap["x0"] > 0.55 * W or centred:
                cx0, cx1 = min(margins + [cap["x0"]]) - 8, max(cap_x1, W - min(margins + [cap["x0"]])) + 8
            else:
                cx0, cx1 = cap["x0"] - 8, cap_x1 + 8
            cx0, cx1 = max(cx0, 0.02 * W), min(cx1, 0.98 * W)
            in_col = [l for l in lns if l["x1"] > cx0 + 2 and l["x0"] < cx1 - 2]
            # caption block: continuation lines directly below (same column)
            cap_bot = cap["y1"]
            for l in sorted(in_col, key=lambda l: l["y0"]):
                if 0 < l["y0"] - cap_bot < 4 and l["x0"] >= cap["x0"] - 6:
                    cap_bot = l["y1"]
            prose = [l for l in in_col if _is_prose(l, typ, margins)]
            def in_column(c):
                ov = min(c["x1"], cx1) - max(c["x0"], cx0)
                return ov > 0.5 * (c["x1"] - c["x0"])
            others = [c for c in caps if c is not cap and in_column(c)]
            up = [l["y1"] for l in prose if l["y1"] < cap["y0"] - 2]
            up_caps = [c for c in others if c["y1"] < cap["y0"] - 2]
            dn = [l["y0"] for l in prose if l["y0"] > cap_bot + 2]
            dn_caps = [c for c in others if c["y0"] > cap_bot + 2]
            above_top = max(up + [c["y1"] for c in up_caps] + [H * 0.06]) + 3
            below_bot = min(dn + [c["y0"] for c in dn_caps] + [H * 0.94]) - 3
            # a table caption above us owns the space below it; a figure caption
            # below us owns the space above it — then split at the largest blank gap
            nearest_up = max(up_caps, key=lambda c: c["y1"], default=None)
            nearest_dn = min(dn_caps, key=lambda c: c["y0"], default=None)
            # "far": skip the other caption's table, which starts at the far edge;
            # "near": we are the table, keep our dense block next to the caption
            split_up = "far" if (nearest_up and nearest_up["kind"] == "table"
                                 and nearest_up["y1"] + 3 >= above_top - 0.5) else ""
            split_dn = "near" if (nearest_dn and nearest_dn["kind"] == "figure" and cap["kind"] == "table"
                                  and nearest_dn["y0"] - 3 <= below_bot + 0.5) else ""
            above = (cx0, above_top, cx1, cap["y0"] - 2)
            below = (cx0, cap_bot + 2, cx1, below_bot)
            box = None
            order = ([("below", below, split_dn), ("above", above, split_up)] if cap["kind"] == "table"
                     else [("above", above, split_up)])
            for side, c, split in order:
                if c[3] - c[1] < 20:
                    continue
                b = grow(pdf, page, c, cache, anchor="bottom" if side == "above" else "top", split=split)
                if b and (b[3] - b[1]) >= 18 and (b[2] - b[0]) * (b[3] - b[1]) >= 4000:
                    box = b
                    break
            if not box:
                continue
            fid = re.sub(r"\W+", "_", cap["label"].lower())
            path = pathlib.Path(cache).parent / "figs" / f"{fid}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            page_image(pdf, page, cache).crop(tuple(int(round(v * SCALE)) for v in box)).save(path)
            seen.add(cap["label"])
            found.append(dict(id=fid, label=cap["label"], kind=cap["kind"], page=page,
                              box=[round(v, 1) for v in box], caption=re.sub(r"\s+", " ", cap["text"])[:400],
                              file=f"figs/{fid}.png", size=Image.open(path).size))
    progress(meta["pages"], meta["pages"])
    return found


def grow(pdf, page, box, cache, anchor, split="", lead_pt=30, gap_pt=10, pad=4):
    """Content of `box` attached to the caption side (anchor).

    Blank space next to the caption (up to lead_pt) is skipped. When a table of
    another caption shares the region, it is separated at the table's first blank
    gap of gap_pt (table rows are dense; figures may have large internal gaps):
      split="near" - we are that table: keep the block next to our caption
      split="far"  - the other table sits at the far edge: drop it"""
    img = np.asarray(page_image(pdf, page, cache).convert("L"))
    x0, y0, x1, y1 = (int(round(v * SCALE)) for v in box)
    x0, y0 = max(x0, 0), max(y0, 0)
    ink = (img[y0:y1, x0:x1] < 245).any(axis=1)
    idx = list(range(len(ink) - 1, -1, -1)) if anchor == "bottom" else list(range(len(ink)))
    seq = [bool(ink[r]) for r in idx]
    if True not in seq or seq.index(True) > lead_pt * SCALE:
        return None
    first = seq.index(True)
    last = len(seq) - 1 - seq[::-1].index(True)
    gap = int(gap_pt * SCALE)

    def first_gap(order):
        run, seen_ink = 0, False
        for k in order:
            if seq[k]:
                if seen_ink and run >= gap:
                    return k - run if order[0] < order[-1] else k + run
                seen_ink, run = True, 0
            elif seen_ink:
                run += 1
        return None

    if split == "near":
        cut = first_gap(list(range(first, last + 1)))
        if cut is not None:
            last = cut - 1
    elif split == "far":
        cut = first_gap(list(range(last, first - 1, -1)))
        if cut is not None:
            k = cut
            while k > first and not seq[k]:
                k -= 1
            last = k
    ya, yb = sorted((idx[first], idx[last]))
    return trim(pdf, page, (box[0], (y0 + ya) / SCALE - 1, box[2], (y0 + yb + 1) / SCALE + 1), cache, pad)


def trim(pdf, page, box, cache, pad=4):
    """Shrink a box to its non-white content (never beyond the box)."""
    img = np.asarray(page_image(pdf, page, cache).convert("L"))
    x0, y0, x1, y1 = (int(round(v * SCALE)) for v in box)
    x0, y0 = max(x0, 0), max(y0, 0)
    sub = img[y0:y1, x0:x1] < 245
    ys, xs = np.nonzero(sub)
    if not len(xs):
        return None
    return (max(box[0], (x0 + xs.min()) / SCALE - pad), max(box[1], (y0 + ys.min()) / SCALE - pad),
            min(box[2], (x0 + xs.max()) / SCALE + pad), min(box[3], (y0 + ys.max()) / SCALE + pad))


def license_hint(text):
    """Best-effort: does the paper say it is openly licensed? (offline, text only)"""
    t = text[:20000]
    if re.search(r"creativecommons\.org/licenses/by|CC[- ]BY(?![- ]?N[CD])|Creative Commons Attribution(?! ?-? ?Non)", t):
        return "CC BY"
    if re.search(r"CC[- ]BY[- ]N[CD]|NonCommercial|NoDerivatives", t):
        return "CC BY-NC/ND"
    return ""


def extract(pdf, job_dir, progress=lambda stage, i, n, msg="": None):
    """Stage 1+2: text, sections, chunks, metadata and figures → job_dir."""
    job = pathlib.Path(job_dir)
    cache = job / "pages"; cache.mkdir(parents=True, exist_ok=True)
    meta = info(pdf)
    progress("parse", 0, 3, "reading text")
    pages = page_texts(pdf)
    full = "\n".join(pages)
    (job / "paper.txt").write_text(full, encoding="utf-8")
    progress("parse", 1, 3, "finding sections")
    secs = sections(pages)
    progress("parse", 2, 3, f"{len(secs)} sections")
    doc = dict(pages=meta["pages"], pdf_title=meta["title"], sections=[dict(s, words=len(s["text"].split()))
               for s in secs], license=license_hint(full), first_page=pages[0][:4000] if pages else "")
    (job / "doc.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    (job / "chunks.json").write_text(json.dumps(chunks([s for s in secs if not s["appendix"]]), indent=1))
    progress("parse", 3, 3, f"{meta['pages']} pages, {len(full.split()):,} words")
    figs = find_figures(pdf, cache, lambda i, n: progress("figures", i, n, f"page {i}/{n}"))
    (job / "figs.json").write_text(json.dumps(figs, indent=1), encoding="utf-8")
    progress("figures", 1, 1, f"{len(figs)} figures/tables")
    return doc, figs
