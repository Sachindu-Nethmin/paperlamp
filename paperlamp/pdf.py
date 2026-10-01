"""PDF → text, sections, metadata, and cropped figures/tables. Offline (poppler).

Uses poppler's command-line tools (pdfinfo, pdftotext, pdftoppm), which ship with
`brew install poppler`. Coordinates are PDF points, origin top-left.
"""
import html, json, pathlib, re, subprocess, unicodedata

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
NUM_HEAD = re.compile(r"^((?:\d{1,2}|[A-H])(?:\.\d{1,2}){0,2})\.?\s+(\S.{1,90})$")
SMALL = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "via", "vs", "with"}


STYLED_FONT = re.compile(r"Bold|Bd|BX|Medi|Semi|Demi|Black|Heavy|Ital|Oblique|Slant|T[BI]$|-B$|-BI$")


def squash(s):
    """Letters and digits only, lower case; maths letters (𝑅𝑄1) become plain (rq1)."""
    return re.sub(r"[^0-9a-z]", "", unicodedata.normalize("NFKC", s).lower())


def headings(pdf):
    """Section headings: from the PDF's own outline (bookmarks) when it has one,
    otherwise found by font rather than by text alone.

    A heading is a numbered line whose number is set at least at body-text size (so
    labels inside figures don't count) and which is styled like a heading: bold,
    italic, larger than the body, or in capitals. Small-caps headings, which
    pdftotext splits into "I NTRODUCTION", are joined back from the glyph positions.
    Returns [{page, num, title, key}] in reading order, plus {page, key: "appendix"}
    where an Appendix heading starts. Empty if pdftohtml is unavailable.
    """
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(run(["pdftohtml", "-xml", "-i", "-q", "-nodrm", "-stdout", str(pdf)]))
    except Exception:
        return []
    marks = []
    for it in root.iter("item"):                          # the outline, in document order
        t = re.sub(r"\s+", " ", "".join(it.itertext())).strip()
        m = re.match(r"^((?:\d{1,2}|[A-H])(?:\.\d{1,2}){0,2})\.?\s+(\S.{1,200})$", t)
        if m:
            marks.append(dict(page=int(it.get("page", 0)), num=m.group(1), title=m.group(2),
                              key=squash(m.group(1) + m.group(2))))
        elif re.fullmatch(r"(?i)appendix|appendices|supplementary material", t):
            marks.append(dict(page=int(it.get("page", 0)), key="appendix"))
    if sum(1 for h in marks if h.get("num")) >= 3:
        return marks
    fonts, els = {}, []
    for page in root.iter("page"):
        for fs in page.iter("fontspec"):
            fonts[fs.get("id")] = (float(fs.get("size")), fs.get("family", ""))
        for t in page.iter("text"):
            txt = "".join(t.itertext())
            if not txt.strip():
                continue
            size, fam = fonts.get(t.get("font"), (0.0, ""))
            styled = t.find(".//b") is not None or t.find(".//i") is not None or \
                bool(STYLED_FONT.search(fam.split("+")[-1]))
            els.append(dict(page=int(page.get("number")), top=float(t.get("top")), left=float(t.get("left")),
                            right=float(t.get("left")) + float(t.get("width")), size=size, styled=styled, text=txt))
    if not els:
        return []
    by_size = {}
    for e in els:
        by_size[e["size"]] = by_size.get(e["size"], 0) + len(e["text"])
    body = max(by_size, key=by_size.get)
    # lines: same page, tops within 4 px (small caps sit a few px lower than their capitals)
    els.sort(key=lambda e: (e["page"], e["top"], e["left"]))
    lines, cur = [], []
    for e in els:
        if cur and (e["page"] != cur[0]["page"] or e["top"] - cur[0]["top"] > 4):
            lines.append(cur); cur = []
        cur.append(e)
    lines.append(cur)
    out = []
    for ln in lines:
        ln.sort(key=lambda e: e["left"])
        seg = [ln[0]]
        for e in ln[1:]:                                   # first column only: stop at a wide gap
            if e["left"] - seg[-1]["right"] > 30:
                break
            seg.append(e)
        text = seg[0]["text"]
        for a, b in zip(seg, seg[1:]):
            text += ("" if b["left"] - a["right"] <= 2.5 else " ") + b["text"]
        text = re.sub(r"\s+", " ", text).strip()
        if re.fullmatch(r"(?i)appendix|appendices|supplementary material", text) and seg[0]["size"] >= body:
            out.append(dict(page=seg[0]["page"], key="appendix"))
            continue
        m = NUM_HEAD.match(text)
        if not m or seg[0]["size"] < body * 0.95:
            continue
        title = m.group(2).strip()
        letters = [c for c in title if c.isalpha()]
        if (title.endswith(".") or len(title.split()) > 12 or len(letters) < 3 or re.search(r"\d{3,}", title)
                or re.search(r"(\.\s?){4,}", title) or not (title[0].isupper() or ord(title[0]) > 127)):
            continue
        caps = all(c.isupper() for c in letters)
        # the whole title must be in the heading style: a bold lead-in followed by plain
        # text ("4. Slide Matching: Do the slides ...") is a list item, not a heading
        worded = [e for e in seg if re.search(r"[^\W\d_]", e["text"])]
        if not (caps or all(e["styled"] for e in worded) or all(e["size"] > body * 1.05 for e in worded)):
            continue
        out.append(dict(page=seg[0]["page"], num=m.group(1), title=title, key=squash(m.group(1) + title)))
    return out


def nice_title(title, text):
    """'PAPERTALKER AGENT' → 'PaperTalker Agent': an all-caps heading takes each word's
    usual spelling from the paper body (so names and acronyms keep their case)."""
    if not title.isupper():
        return title
    words = []
    for i, token in enumerate(title.split()):
        lead, w, tail = re.match(r"^(\W*)(.*?)(\W*)$", token).groups()   # keep "(", ":" etc. around the word
        forms = re.findall(rf"(?<![\w-]){re.escape(w)}(?![\w-])", text, re.I) if w else []
        mixed = [f for f in forms if not f.isupper() and not f.islower()]
        if w.lower() in SMALL and i:
            w = w.lower()
        elif forms and sum(f.isupper() for f in forms) > len(forms) / 2 and len(w) <= 5:
            pass                                           # an acronym: VLM, IP, AI
        elif mixed:
            w = max(set(mixed), key=mixed.count)           # PaperTalker, Paper2Video, SWE-Llama
        else:
            w = w.capitalize()
        words.append(lead + w + tail)
    return " ".join(words)


def sections(pages, heads=None):
    """Split the paper into numbered sections; drop references. Appendix kept, flagged.

    With `heads` (from headings()), a line starts a section only if it is one of those
    font-checked headings; without them, a text pattern is used (older behaviour).
    """
    out, cur = [], dict(num="0", title="Front matter", lines=[], parent="Front matter")
    in_appendix, parent, last_top = False, "Front matter", 0
    full = "\n".join(pages)
    pending = {}
    for h in heads or []:
        pending.setdefault(h["page"], []).append(h)

    def start(num, title, rest=""):
        nonlocal cur, parent, last_top
        top = int(num.split(".")[0]) if num[0].isdigit() else None
        if top is not None:
            last_top = top
        if cur["lines"] or cur["num"] != "0":
            out.append(cur)
        title = re.sub(r"\s+", " ", title).strip()
        if "." not in num:
            parent = title                              # remember the top-level section
        cur = dict(num=num, title=title, lines=[rest] if rest.strip() else [], parent=parent,
                   appendix=in_appendix or (num[0].isalpha() and heads is None))

    for pno, page in enumerate(pages, 1):
        todo = pending.get(pno, [])
        for ln in page.splitlines():
            s = ln.strip()
            if STOP_RE.match(s):
                out.append(cur)
                cur = dict(num="R", title="References", lines=[], skip=True)
                in_appendix = in_appendix or heads is not None   # anything headed after references is appendix
                continue
            if re.match(r"^\s*A\s?PPENDIX|^\s*Appendix\s*$", s):
                in_appendix = True
            if heads is not None:
                key = squash(s)
                # a long heading may wrap: its first line is then a prefix of the heading
                h = next((h for h in todo if h.get("num") and len(h["key"]) >= 4 and (
                    key.startswith(h["key"]) or (len(key) >= 12 and h["key"].startswith(key)))), None)
                if h:
                    todo.remove(h)
                    top = int(h["num"].split(".")[0]) if h["num"][0].isdigit() else None
                    if not (top is not None and not in_appendix and top < last_top):
                        # text after the heading on the same line belongs to another column
                        n, cut = 0, len(s)
                        for i, c in enumerate(s):
                            n += len(squash(c))
                            if n >= len(h["key"]):
                                cut = i + 1
                                break
                        start(h["num"], nice_title(h["title"], full), s[cut:])
                        continue
                cur["lines"].append(ln)
                continue
            m = HEAD_RE.match(ln)
            # section numbers only move forward: a "1" after section 2.1 is text in a figure
            top = int(m.group(1).split(".")[0]) if m and m.group(1)[0].isdigit() else None
            backwards = top is not None and not in_appendix and top < last_top
            if m and not backwards and len(s) < 90 and not s.endswith(".") and not re.search(r"\d{3,}", m.group(2)) \
                    and sum(c.isalpha() for c in m.group(2)) >= 3 and len(m.group(2).split()) <= 12:
                start(m.group(1), m.group(2))
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
        res.append(dict(num=sct["num"], title=sct["title"], parent=sct.get("parent", sct["title"]),
                        appendix=sct.get("appendix", False), text=text))
    # a repeated section number means one "heading" was really a numbered list item
    # (e.g. inside a figure): keep the longer section, fold the shorter into its neighbour
    i = 0
    while i + 1 < len(res):
        a, b = res[i], res[i + 1]
        if a["num"] == b["num"]:
            if len(a["text"]) < len(b["text"]):
                if i > 0:
                    res[i - 1]["text"] += f"\n{a['num']}. {a['title']}\n{a['text']}"
                res.pop(i)
            else:
                a["text"] += f"\n{b['num']}. {b['title']}\n{b['text']}"
                res.pop(i + 1)
            continue
        i += 1
    return res


def chunks(secs, max_words=1500):
    """Group/split sections into LLM-sized chunks, keeping section titles."""
    out = []
    for s in secs:
        words = s["text"].split()
        for i in range(0, len(words), max_words):
            part = " ".join(words[i:i + max_words])
            out.append(dict(title=f'{s["num"]} {s["title"]}' + (f" (part {i // max_words + 1})" if i else ""),
                            parent=s.get("parent", s["title"]), appendix=s["appendix"], text=part))
    return out


# ── figures and tables ───────────────────────────────────────────────────────
def words(pdf, page):
    xml = run(["pdftotext", "-bbox", "-f", str(page), "-l", str(page), str(pdf), "-"])
    return [(float(a), float(b), float(c), float(d), html.unescape(t)) for a, b, c, d, t in re.findall(
        r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', xml)]


def lines_of(ws):
    """Text lines: words on the same visual line (tops within 2.5 pt), in x order, split
    where a gap of 12 pt or more separates columns (or text from a float beside it)."""
    rows = []
    for w in sorted(ws, key=lambda w: (w[1], w[0])):
        if rows and abs(rows[-1][0][1] - w[1]) < 2.5:
            rows[-1].append(w)
        else:
            rows.append([w])
    segs = []
    for r in rows:
        r.sort(key=lambda w: w[0])
        seg = [r[0]]
        for w in r[1:]:
            if w[0] - seg[-1][2] < 12:
                seg.append(w)
            else:
                segs.append(seg); seg = [w]
        segs.append(seg)
    # gaps wider than word spacing: where text wrapped beside a float meets the float
    return [dict(x0=min(w[0] for w in r), y0=min(w[1] for w in r), x1=max(w[2] for w in r),
                 y1=max(w[3] for w in r), text=" ".join(w[4] for w in r), n=len(r),
                 gaps=[(a[2], b[0]) for a, b in zip(r, r[1:]) if b[0] - a[2] > 7]) for r in segs]


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
        # a caption starts its line (or its column, for a float with text wrapped beside
        # it): "...see Appendix Table 18." is a mention, not a caption. Words in a line are
        # ~2-4 pt apart; a wrapped float sits a column gap (8 pt or more) away.
        if any(abs(u[1] - w[1]) < 2.5 and w[0] - 6 < u[2] <= w[0] + 0.5 for u in ws if u is not w):
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
            # lines in this column: mostly inside it (text wrapped beside a side figure only
            # touches the column's edge, and is not part of it)
            in_col = [l for l in lns if min(l["x1"], cx1) - max(l["x0"], cx0) >
                      0.5 * min(l["x1"] - l["x0"], cx1 - cx0)]
            # caption block: continuation lines directly below (same column)
            cap_bot = cap["y1"]
            for l in sorted(in_col, key=lambda l: l["y0"]):
                if 0 < l["y0"] - cap_bot < 4 and l["x0"] >= cap["x0"] - 6:
                    cap_bot = l["y1"]
            # a "line" that runs from wrapped text into the float (a gap at the column edge)
            # belongs to the float, so it doesn't bound it
            split_by_edge = lambda l: any((g0 < cx0 + 12 and g1 > cx0 - 4) or (g0 < cx1 + 4 and g1 > cx1 - 12)
                                          for g0, g1 in l.get("gaps", []))
            prose = [l for l in in_col if _is_prose(l, typ, margins) and not split_by_edge(l)]
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
            if cx1 - cx0 < 0.7 * W:                            # a side float: it may be wider than its caption
                box = widen(pdf, page, box, cache, 0.02 * W, 0.98 * W)
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


def widen(pdf, page, box, cache, lo, hi, gap_pt=3.5, pad=4):
    """A side table is often wider than its caption: extend the box left/right while ink
    continues, stopping at the first white gap (the space before wrapped text)."""
    img = np.asarray(page_image(pdf, page, cache).convert("L"))
    x0, y0, x1, y1 = (int(round(v * SCALE)) for v in box)
    ink = (img[max(y0, 0):y1, :] < 245).any(axis=0)
    gap = int(gap_pt * SCALE)

    def edge(x, step, limit):
        run, last = 0, x
        while 0 <= x + step < len(ink) and (x + step) * step <= limit * step:
            x += step
            if ink[x]:
                run, last = 0, x
            else:
                run += 1
                if run >= gap:
                    break
        return last

    nx0 = edge(x0 + int(pad * SCALE), -1, int(lo * SCALE))
    nx1 = edge(x1 - int(pad * SCALE), 1, int(hi * SCALE))
    return (min(box[0], nx0 / SCALE - pad), box[1], max(box[2], nx1 / SCALE + pad), box[3])


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
