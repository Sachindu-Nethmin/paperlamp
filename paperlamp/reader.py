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
APPENDIX = re.compile(r"^(appendix|appendices|supplementarymaterial|neuripspaperchecklist|paperchecklist|"
                      r"reproducibilitychecklist|checklist$)")
# front-matter blocks that are not sentences: keyword lists, the ACM/IEEE copyright box
META = re.compile(r"^(ccsconcepts|keywords|indexterms|acmreferenceformat|additionalkeywords)")
COPYRIGHT = re.compile(r"permission to make digital|acm isbn|©|copyright held|rights licensed|"
                       r"https?://doi\.org/10\.\d+/\S*$", re.I)
# the authors' footnotes on the first pages: equal contribution, who to write to, where they work
FOOTNOTE = re.compile(r"[\w.}+-]@[\w-]+\.[a-z]{2,}|^\s*[∗*†‡§¶]|equal contribution|correspondence to|corresponding author|"
                      r"primary advising|work done (while|during)|authors[’'] (contact|addresses)", re.I)
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


GREEK_CH = set("αβγδεζηθικλμνξπρστυφχψωΓΔΘΛΞΠΣΦΨΩϵϑϕ")
MATH_OPS = set("=∑∏∫≤≥≠≈∈∉⊂⊆∪∩±×·→←↦∀∃∂∇∞∝∼⊤∥‖√⋅")
MATH_WORDS = {"arg", "max", "min", "log", "exp", "sin", "cos", "tanh", "softmax", "sigmoid", "where", "subject",
              "s.t.", "for", "and", "if", "otherwise", "with"}
EQ_NUM = re.compile(r"\(\s*[A-Z]?\d{1,3}(?:\.\d{1,3})?[a-z]?\s*\)")
CODE = re.compile(r"[A-Za-z]{2,}_[A-Za-z_]{2,}|==|!=|\+=|←|:=\s*\w+\(|\b(def|return|elif|lambda|import|print)\b|"
                  r"^\s*\d{0,3}:?\s*(if|else|elif|for ?each|while|repeat|until|return|end)\b|\b(then|do)\s*$|"
                  r"^\s*\d{1,4}:|\w_\s|\w\.\w+\s*\[|\b[A-Z][a-z]+[A-Z]\w*\(")


def _line_text(ln):
    return " ".join(w[4] for w in ln["words"])


def _mathish(text):
    """A typeset formula, not prose with a symbol in it: an operator, mostly one- or two-character
    tokens (variables, subscripts, operators), and hardly any real words."""
    toks = text.split()
    if not toks or not any(c in MATH_OPS for c in text) or re.search(r"https?://|www\.|\w=\w+&", text):
        return False
    if CODE.search(text) or re.search(r"[a-z]-$", text.rstrip()):   # code, pseudo-code, or prose wrapped mid-word
        return False
    short = sum(1 for t in toks if len(t) <= 2 or any(c in GREEK_CH or c in MATH_OPS for c in t))
    real = sum(1 for t in toks if re.fullmatch(r"[A-Za-z][a-z]{3,}[.,;:]?", t) and t.lower().strip(".,;:") not in MATH_WORDS)
    return short >= 0.5 * len(toks) and real <= 3


def _union_lines(lines):
    return [min(l["box"][0] for l in lines), min(l["box"][1] for l in lines),
            max(l["box"][2] for l in lines), max(l["box"][3] for l in lines)]


def split_math(pages):
    """Display equations as blocks of their own. Poppler often puts an equation inside the
    paragraph around it, with its subscripts and its number "(3)" as separate lines or blocks;
    then the whole paragraph looks like non-prose and was skipped. Each block is split into its
    prose lines and its formula lines; a formula block gets eq=True, the equation numbers beside
    it (nums) and the stray subscript fragments inside its box. Works in place, page by page."""
    for pg in pages:
        lines = [ln for b in pg["blocks"] for ln in b["lines"]]
        starts = Counter(round(ln["box"][0] / 3) for ln in lines)
        margins = [k * 3 for k, c in starts.items() if c >= 4]
        widths = sorted(ln["box"][2] - ln["box"][0] for ln in lines if any(abs(ln["box"][0] - m) <= 4 for m in margins))
        full = widths[int(len(widths) * 0.75)] if widths else pg["w"] * 0.7
        at_margin = lambda ln: any(abs(ln["box"][0] - m) <= 4 for m in margins)

        def kind(ln):
            t = _line_text(ln).strip()
            if EQ_NUM.fullmatch(t):
                return "N"
            if len(t.replace(" ", "")) <= 3 and ln["box"][3] - ln["box"][1] <= 10:
                return "T"                                  # a subscript or superscript on its own line
            if _mathish(t) and (not at_margin(ln) or ln["box"][2] - ln["box"][0] < 0.75 * full):
                return "M"
            return "P"
        out = []
        for b in pg["blocks"]:
            ks = [kind(ln) for ln in b["lines"]]
            if "M" not in ks:
                out.append(b)
                continue
            # a run of formula lines, with the numbers and fragments touching it, is one equation
            runs, cur = [], None
            for ln, k in zip(b["lines"], ks):
                tag = "eq" if k in ("M", "N", "T") else "text"
                if cur and cur["tag"] == tag:
                    cur["lines"].append(ln); cur["kinds"].append(k)
                else:
                    cur = dict(tag=tag, lines=[ln], kinds=[k]); runs.append(cur)
            for r in runs:
                if r["tag"] == "eq" and "M" not in r["kinds"]:
                    r["tag"] = "text"                            # a lone "(2)" or fragment inside prose
                box = _union_lines(r["lines"])
                if r["tag"] == "eq":
                    out.append(dict(box=box, lines=[ln for ln, k in zip(r["lines"], r["kinds"]) if k != "N"],
                                    eq=True, nums=[_line_text(ln).strip() for ln, k in zip(r["lines"], r["kinds"]) if k == "N"],
                                    all_lines=r["lines"]))
                else:
                    out.append(dict(box=box, lines=r["lines"]))
        # numbers and fragments that poppler put in blocks of their own join the equation they belong to
        eqs = [b for b in out if b.get("eq")]
        keep = []
        for b in out:
            t = block_text(b).strip()
            host = None
            if not b.get("eq") and eqs and (EQ_NUM.fullmatch(t) or (len(t.replace(" ", "")) <= 3 and len(b["lines"]) == 1)):
                for e in eqs:
                    y_hit = b["box"][1] <= e["box"][3] + 6 and b["box"][3] >= e["box"][1] - 6
                    x_hit = b["box"][0] >= e["box"][0] - 6 and (EQ_NUM.fullmatch(t) or b["box"][2] <= e["box"][2] + 6)
                    if y_hit and x_hit:
                        host = e
                        break
            if host is None:
                keep.append(b)
                continue
            if EQ_NUM.fullmatch(t):
                host["nums"].append(t)
            host["all_lines"] = host.get("all_lines", host["lines"]) + b["lines"]
            host["box"] = _union_lines(host["all_lines"])
        # rows of one equation that poppler gave separate blocks become one equation again
        merged = []
        for b in keep:
            p = merged[-1] if merged else None
            if p is not None and b.get("eq") and p.get("eq") and 0 <= b["box"][1] - p["box"][3] <= 8 \
                    and b["box"][0] < p["box"][2] and b["box"][2] > p["box"][0]:
                p["lines"] += b["lines"]; p["nums"] += b["nums"]
                p["all_lines"] = p.get("all_lines", []) + b.get("all_lines", b["lines"])
                p["box"] = _union_lines(p["all_lines"])
                continue
            merged.append(b)
        pg["blocks"] = merged
    return pages


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
            elif pg["page"] <= 2 and b["box"][1] > 0.6 * pg["h"] and FOOTNOTE.search(t):
                kind = "skip"                                      # the authors' footnote, not the paper's text
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
            if sq.startswith("abstract") and state == "front":     # not "Abstract ..." in a checklist
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
        if b["kind"] in ("heading", "caption", "equation"):
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
    blocks = classify(split_math(layout(pdf_path)), figs, heads)
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
