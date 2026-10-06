"""Whole-paper videos: every sentence of the paper, from the title to the last section,
explained in plain words while the highlighter sits on that exact sentence.

The paper's sentences come from poppler's layout (`pdftotext -bbox-layout`: blocks, lines and
words with their boxes), put in reading order column by column, so each narration sentence is
tied to the source sentence it explains and needs no matching afterwards. Left out, because
nobody reads them aloud: running headers and footers, page numbers, the arXiv stamp in the
margin, the publisher's copyright box and keyword lists, the insides of figures and tables
(their captions are explained, with the figure on screen), display equations, the reference
list and, unless asked for, the appendix. Section headings are read as they are, and the
authors are read by name, every one of them.
"""
import html, re
from collections import Counter

from . import llm
from .align import ABBREV
from .pdf import nice_title, run, squash
from .script import author_sentences, fig_label, paper_line

WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
BOX = r'xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)"'
CAPTION = re.compile(r"^(Figure|Fig\.|Table)\s*(\d+|[A-Z]\d*)\s*[:.]", re.I)
REFS = re.compile(r"^(references|bibliography)$")
APPENDIX = re.compile(r"^(appendix|appendices|supplementarymaterial)")
# front-matter blocks that are not sentences: keyword lists, the ACM/IEEE copyright box
META = re.compile(r"^(ccsconcepts|keywords|indexterms|acmreferenceformat|additionalkeywords)")
COPYRIGHT = re.compile(r"permission to make digital|acm isbn|©|copyright held|rights licensed|"
                       r"https?://doi\.org/10\.\d+/\S*$", re.I)
BATCH = 8                       # sentences the local model explains in one go


def layout(pdf):
    """[{page, w, h, blocks: [{box, lines: [{box, words: [[x0, y0, x1, y1, text]]}]}]}],
    each page's blocks in reading order (see reading_order)."""
    xml = run(["pdftotext", "-bbox-layout", str(pdf), "-"])
    pages = []
    for pm in re.finditer(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', xml, re.S):
        blocks = []
        for bm in re.finditer(rf"<block {BOX}>(.*?)</block>", pm.group(3), re.S):
            lines = []
            for lm in re.finditer(rf"<line {BOX}>(.*?)</line>", bm.group(5), re.S):
                ws = [[float(a), float(b), float(c), float(d), html.unescape(t)] for a, b, c, d, t in WORD.findall(lm.group(5))]
                if ws:
                    lines.append(dict(box=[float(x) for x in lm.groups()[:4]], words=ws))
            if lines:
                blocks.append(dict(box=[float(x) for x in bm.groups()[:4]], lines=lines))
        w = float(pm.group(1))
        pages.append(dict(page=len(pages) + 1, w=w, h=float(pm.group(2)), blocks=reading_order(blocks, w)))
    return pages


def reading_order(blocks, width):
    """Poppler's own block order can alternate between the columns of a two-column page.
    Blocks spanning the page (the title, a wide figure) cut it into bands; inside a band the
    left column is read top to bottom, then the right one."""
    mid = width / 2
    side = lambda b: "L" if b["box"][2] <= mid + 15 else "R" if b["box"][0] >= mid - 15 else "F"
    full = sorted((b for b in blocks if side(b) == "F"), key=lambda b: b["box"][1])
    cuts = [b["box"][1] for b in full] + [float("inf")]
    out, done = [], set()
    top = float("-inf")
    for k, cut in enumerate(cuts):
        band = [b for b in blocks if side(b) != "F" and top <= b["box"][1] < cut and id(b) not in done]
        for b in sorted(band, key=lambda b: (side(b), b["box"][1], b["box"][0])):
            out.append(b); done.add(id(b))
        if k < len(full):
            out.append(full[k]); top = full[k]["box"][1]
    return out + [b for b in blocks if id(b) not in done and b not in out]


def block_text(b):
    return " ".join(w[4] for ln in b["lines"] for w in ln["words"])


def _inside(b, box, pad=2):
    cx, cy = (b["box"][0] + b["box"][2]) / 2, (b["box"][1] + b["box"][3]) / 2
    return box[0] - pad <= cx <= box[2] + pad and box[1] - pad <= cy <= box[3] + pad


def _is_prose(text):
    """Running text, not an equation, a table row or a code listing: mostly real words."""
    toks = text.split()
    if not toks:
        return False
    wordy = sum(1 for t in toks if re.fullmatch(r"[(\[\"'“‘•]?[A-Za-z][A-Za-z\-’']+[.,;:)\]\"'”’?!]*", t))
    nums = sum(1 for t in toks if re.fullmatch(r"[\d.,%±×()\-–]+", t))
    return wordy >= 0.5 * len(toks) and not (nums > 0.4 * len(toks) and not re.search(r"[a-z]\.\s|[a-z]\.$", text))


def _heading(b, sq, heads, page):
    if len(b["lines"]) > 2:
        return None
    for h in heads:
        t, n = squash(h.get("title", "")), squash(h.get("num", "") or "")
        if h["page"] == page and len(t) >= 3 and (sq in (t, n + t) or (sq.startswith(n + t) and len(sq) - len(n + t) < 6)):
            return h
    return None


def classify(pages, figs, heads):
    """Each block gets a kind (skip, title, authors, heading, caption, text) and whether it is in
    the appendix. Blocks before the abstract on page 1 are the title and the authors."""
    edge = Counter()                                  # running headers and footers repeat on many pages
    for pg in pages:
        for b in pg["blocks"]:
            if b["box"][1] < 0.08 * pg["h"] or b["box"][3] > 0.92 * pg["h"]:
                edge[re.sub(r"\d+", "#", squash(block_text(b)))] += 1
    out, state, title_done, after_meta = [], "front", False, False
    for pg in pages:
        pfigs = [f for f in figs if f["page"] == pg["page"]]
        for b in pg["blocks"]:
            t = block_text(b)
            sq = squash(t)
            kind, h = "text", None
            at_edge = b["box"][1] < 0.08 * pg["h"] or b["box"][3] > 0.92 * pg["h"]
            if not sq or (b["box"][2] < 45 and b["box"][3] - b["box"][1] > 3 * (b["box"][2] - b["box"][0])) \
                    or re.match(r"^arxiv\d{4}\d+v\d", sq):
                kind = "skip"                                      # the arXiv stamp in the margin
            elif at_edge and (edge[re.sub(r"\d+", "#", sq)] >= 3 or re.fullmatch(r"(page)?\d{1,3}(of\d+)?", sq)):
                kind = "skip"                                      # running header, footer, page number
            elif CAPTION.match(t) and any(fig_label(t) == f["label"] for f in pfigs):
                kind = "caption"
            elif any(_inside(b, f["box"]) for f in pfigs):
                kind = "skip"                                      # inside a figure or table
            elif REFS.match(sq):
                state, kind = "refs", "skip"
            elif META.match(sq) or COPYRIGHT.search(t) or (after_meta and len(b["lines"]) <= 3):
                kind = "skip"
            elif APPENDIX.match(sq) and len(sq) < 40:
                state, kind = "appendix", "heading"
            elif (h := _heading(b, sq, heads, pg["page"])):
                kind = "heading"
                state = "appendix" if state == "refs" or (state == "appendix") or h.get("appendix") else "body"
            elif state == "refs" or not _is_prose(t):
                kind = "skip"
            after_meta = bool(META.fullmatch(sq))                 # "Keywords" alone: its list follows
            if sq.startswith("abstract"):
                kind, state = ("text" if len(sq) > 12 else "skip"), "abstract"
            elif state == "front" and kind == "text":
                kind = "title" if not title_done else "authors"
                title_done = True
            out.append(dict(b, page=pg["page"], kind=kind, head=h, part=state))
    return out


def _ends(word):
    t = word.rstrip("\"'”’)]")
    return t.endswith((".", "?", "!")) and not ABBREV.match(t) and not re.fullmatch(r"[A-Z]\.", t) \
        and not re.fullmatch(r"\(?\d+[.)]", t)


def _join(words):
    """The sentence's text: a word broken at a line end ("individ-" "ual") is joined; a real
    hyphen at a line end ("state-of-" "the-art") is kept."""
    toks = []
    for i, x in enumerate(words):
        t = x["w"][4]
        if toks and toks[-1].endswith("-") and words[i - 1]["eol"] and t[:1].islower():
            toks[-1] = toks[-1][:-1] + t if "-" not in toks[-1][:-1] else toks[-1] + t
        else:
            toks.append(t)
    text = " ".join(toks)
    return re.sub(r"^(?:A ?BSTRACT|Abstract)\s*[.:—–-]?\s*", "", text)


def items(blocks, appendix=False):
    """The paper as a stream: ("heading", block), ("caption", block) and ("sentence", words).
    A sentence that runs on into the next column or page continues there."""
    out, cur = [], []

    def flush():
        nonlocal cur
        if cur:
            out.append(("sentence", cur))
        cur = []
    for bi, b in enumerate(blocks):
        if b["part"] == "appendix" and not appendix:
            continue
        if b["kind"] in ("heading", "caption"):
            flush(); out.append((b["kind"], b)); continue
        if b["kind"] not in ("text",):
            continue
        for ln in b["lines"]:
            for k, w in enumerate(ln["words"]):
                cur.append(dict(w=w, page=b["page"], block=bi, eol=k == len(ln["words"]) - 1))
                if _ends(w[4]):
                    flush()
    flush()
    return out


def boxes_of(words):
    """Page, one box per line, for the page that holds most of the sentence."""
    page = Counter(x["page"] for x in words).most_common(1)[0][0]
    rows = []
    for x in words:
        if x["page"] != page:
            continue
        w = x["w"]
        if rows and abs(rows[-1][1] - w[1]) < 3 and w[0] >= rows[-1][2] - 1:
            r = rows[-1]; rows[-1] = [r[0], min(r[1], w[1]), max(r[2], w[2]), max(r[3], w[3])]
        else:
            rows.append(list(w[:4]))
    return page, rows


def _union(boxes, pad=0):
    return [min(b[0] for b in boxes) - pad, min(b[1] for b in boxes) - pad,
            max(b[2] for b in boxes) + pad, max(b[3] for b in boxes) + pad]


NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def explain(batch, section, paper, model, before=""):
    """One plain-words explanation per source sentence. A missing, empty or number-changing
    explanation falls back to the paper's own sentence, so nothing is skipped or invented."""
    listing = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(batch))
    try:
        res = llm.chat(llm.skill("explain_sentences", paper=paper, section=section, before=before or "(start)",
                                 n=str(len(batch)), sentences=listing), model=model, max_tokens=80 + 70 * len(batch))
        got = [x if isinstance(x, str) else "" for x in (res.get("explanations") or [])]
    except RuntimeError:
        got = []
    out = []
    for i, src in enumerate(batch):
        e = re.sub(r"^\s*\d+[.)]\s*", "", got[i]).strip() if i < len(got) else ""
        e = re.sub(r"^(This|The) sentence (says|means|explains|states) that\s+", "", e, flags=re.I)
        ok = len(e.split()) >= 4 and set(x.replace(",", "") for x in NUM.findall(e)) <= \
            set(x.replace(",", "") for x in NUM.findall(src))
        out.append((e[:1].upper() + e[1:]) if ok else src)
    return out


def write(pdf_path, meta, figs, heads, model, progress, appendix=False):
    """The whole-paper script: chapters of explanations, each with its exact source spot."""
    blocks = classify(layout(pdf_path), figs, heads)
    stream = items(blocks, appendix)
    body_text = " ".join(block_text(b) for b in blocks if b["kind"] == "text")
    figs_by = {f["label"]: f for f in figs}
    title_b = next((b for b in blocks if b["kind"] == "title"), None)
    paper = paper_line(meta)
    chapters = [dict(key="paper", title="The paper", sentences=[dict(
        text=f"This video explains \"{meta.get('title', '')}\" sentence by sentence, from beginning to end.",
        **({"exact": True, "align": dict(page=title_b["page"], focus=_union([title_b["box"]], 60),
                                         highlight=[ln["box"] for ln in title_b["lines"]])} if title_b else {}))]
        + author_sentences(meta.get("authors") or [])),
        dict(key="abstract", title="Abstract", sentences=[])]
    # first gather every source sentence with its spot, then explain them section by section
    for kind, x in stream:
        if kind == "heading":
            h = x.get("head") or {}
            title = nice_title(re.sub(r"\s+", " ", h.get("title") or block_text(x)).strip(), body_text)
            num = h.get("num") or ""
            chapters.append(dict(key=f"s{num or len(chapters)}", title=title, sentences=[dict(
                text=f"Section {num}: {title}." if num else f"{title}.", fixed=True, exact=True,
                align=dict(page=x["page"], focus=_union([x["box"]], 40), highlight=[ln["box"] for ln in x["lines"]]))]))
        elif kind == "caption":
            t = _join([dict(w=w, eol=k == len(ln["words"]) - 1) for ln in x["lines"] for k, w in enumerate(ln["words"])])
            f = figs_by.get(fig_label(t) or "")
            hl = [ln["box"] for ln in x["lines"]]
            box = _union(hl + ([f["box"]] if f else []))
            chapters[-1]["sentences"].append(dict(source=t, figure=f["label"] if f else None, exact=True,
                                                  align=dict(page=x["page"], focus=box, outline=box, highlight=hl,
                                                             **({"figure": f["label"]} if f else {}))))
        else:
            t = _join(x)
            if len(t.split()) < 3 and not t.endswith((".", "?", "!")):
                continue                                           # a stray label or number
            page, hl = boxes_of(x)
            para = _union([blocks[w["block"]]["box"] for w in x if w["page"] == page] + hl)
            chapters[-1]["sentences"].append(dict(source=t, exact=True, align=dict(page=page, focus=para, highlight=hl)))
    todo = [s for c in chapters for s in c["sentences"] if "source" in s]
    done = 0
    for c in chapters:
        srcs = [s for s in c["sentences"] if "source" in s]
        before = ""
        for k in range(0, len(srcs), BATCH):
            part = srcs[k:k + BATCH]
            progress(done, len(todo), f"explaining: {c['title']}")
            texts = explain([("Figure caption: " if s.get("figure") else "") + s["source"] for s in part],
                            c["title"], paper, model, before)
            for s, e in zip(part, texts):
                s["text"] = re.sub(r"^Figure caption:\s*", "", e)
            before = " ".join(s["source"] for s in part[-2:])
            done += len(part)
    chapters = [c for c in chapters if c["sentences"]]
    chapters.append(dict(key="outro", title="Source", sentences=[dict(
        text="The paper is linked in the description. Thanks for watching.", fixed=True)]))
    n = sum(len(c["sentences"]) for c in chapters)
    progress(len(todo), len(todo), f"{len(todo)} sentences of the paper explained · {n} in the video")
    return dict(meta=meta, chapters=chapters, source="ollama", model=model, depth="read",
                appendix_included=bool(appendix))
