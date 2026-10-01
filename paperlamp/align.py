"""Stage: find where in the PDF each narration sentence belongs.

For every sentence we pick
  page       - which page to show
  highlight  - the exact lines to mark with a highlighter (1-3 lines)
  focus      - the region the camera frames (the paragraph, or a whole figure)

A sentence that names "Figure N" / "Table N" goes to that figure (caption
highlighted, figure outlined). Otherwise lines are scored by shared numbers
(weighted most), rare words and phrases, searching the sentence's own section
first. Numbers and names come straight from the text, so this needs no model.
"""
import math, re
from collections import Counter

from .pdf import lines_of, squash, words

STOP = set("""a an the and or of to in on for with by from as at is are was were be been being this that these those it its
their they them we our us you your he she his her which who whom whose what when where why how than then there here
also into over under about after before between during each both more most other some such only own same so too very
can will just not no nor but if because while do does did done have has had having one two three four five six seven
eight nine ten paper authors author study show shows shown result results use used using""".split())
REF = re.compile(r"\b(Figure|Fig\.|Table)\s+(\d+|[A-Z]\d*)\b", re.I)


CITE = re.compile(r"\[\s*\d+(?:\s*[,\u2013-]\s*\d+)*\s*\]")    # [10], [29, 38], [4-6]: citations, not data


def tokens(s):
    s = CITE.sub(" ", s)
    return [w for w in re.findall(r"[a-z][a-z\-]{2,}|\d[\d,]*(?:\.\d+)?", s.lower()) if w not in STOP]


def build_index(pdf, pages):
    """Every text line of the paper with its page and box (points)."""
    idx = []
    for p in range(1, pages + 1):
        for ln in lines_of(words(pdf, p)):
            idx.append(dict(page=p, box=[ln["x0"], ln["y0"], ln["x1"], ln["y1"]], text=ln["text"],
                            toks=tokens(ln["text"]), words=ln["words"]))
    return idx


def _section_pages(idx, chapter_title):
    """Page where a chapter's section heading appears (search starts there). Compared
    letters-only, so small-caps headings ("C URSOR B UILDER") still match; a merged
    chapter ("A & B") looks for its first section."""
    t = squash(chapter_title.split(" & ")[0].split(": ")[-1])[:30]
    for ln in idx:
        if len(ln["text"]) < 100 and len(t) >= 4 and t in squash(ln["text"]) and _is_heading(ln):
            return ln["page"]
    return None


def _paragraph(idx, i):
    """Expand a line to its paragraph: neighbours on the same page with small gaps."""
    base = idx[i]
    a = b = i
    while a > 0 and idx[a - 1]["page"] == base["page"] and 0 <= idx[a]["box"][1] - idx[a - 1]["box"][3] < 5 \
            and abs(idx[a - 1]["box"][0] - base["box"][0]) < 40:
        a -= 1
    while b + 1 < len(idx) and idx[b + 1]["page"] == base["page"] and 0 <= idx[b + 1]["box"][1] - idx[b]["box"][3] < 5 \
            and abs(idx[b + 1]["box"][0] - base["box"][0]) < 40:
        b += 1
    a, b = max(a, i - 6), min(b, i + 6)
    boxes = [idx[k]["box"] for k in range(a, b + 1)]
    return [min(x[0] for x in boxes), min(x[1] for x in boxes), max(x[2] for x in boxes), max(x[3] for x in boxes)]


ABBREV = re.compile(r"^(e\.g|i\.e|et al|al|fig|figs|eq|eqs|sec|vs|cf|approx|resp|no|vol|pp|ref|refs)\.$", re.I)


def _ends_sentence(word):
    t = word.rstrip("\"'\u201d\u2019)]")
    if not t.endswith((".", "?", "!")) or ABBREV.match(t):
        return False
    return not re.fullmatch(r"[A-Z]\.", t)                    # initials ("A.") don't end a sentence


def _is_heading(line):
    """A section heading or a stray label: short, no closing punctuation, and numbered,
    in capitals, or just a word or two. Headings separate sentences."""
    text, n = line["text"].strip(), len(line.get("words") or [])
    if n > 8 or text.endswith((".", ",", ";", ":", ")", "?", "!")):
        return False
    letters = [c for c in text if c.isalpha()]
    caps = letters and sum(c.isupper() for c in letters) > 0.7 * len(letters)
    return bool(re.match(r"^([A-H]|\d+)(\.\d+)*\s+\S", text) or caps or n <= 2)


def _next_line(idx, i, step):
    """The line directly above (step -1) or below (+1) in the same column, or None.
    Sub- and superscripts (much shorter boxes) are not lines of their own."""
    a = idx[i]
    h = a["box"][3] - a["box"][1]
    best, best_gap = None, None
    for j in range(max(0, i - 40), min(len(idx), i + 41)):
        bl = idx[j]
        if j == i or bl["page"] != a["page"] or not bl.get("words") or bl["box"][3] - bl["box"][1] < 0.6 * h:
            continue
        gap = (a["box"][1] - bl["box"][3]) if step < 0 else (bl["box"][1] - a["box"][3])
        ov = min(a["box"][2], bl["box"][2]) - max(a["box"][0], bl["box"][0])
        if -1 <= gap <= 0.9 * h and ov > 0.5 * min(a["box"][2] - a["box"][0], bl["box"][2] - bl["box"][0]):
            if best_gap is None or gap < best_gap:
                best, best_gap = j, gap
    return best


def sentence_span(idx, i, qset, idf=None, reach=6, max_lines=8):
    """Boxes covering the paper's own sentence that the narration matches best.

    The column's lines around line i are split into the paper's sentences (full stops,
    and headings as boundaries); the sentence sharing the most (rare) words with the
    narration is highlighted from its first word to its full stop, one box per line.
    None when the index has no word positions or line i is not prose (a table cell)."""
    if len(idx[i].get("words") or []) < 5:
        return None
    up, down, j = [], [], i
    for _ in range(reach):
        j = _next_line(idx, j, -1)
        if j is None:
            break
        up.append(j)
    j = i
    for _ in range(reach):
        j = _next_line(idx, j, 1)
        if j is None:
            break
        down.append(j)
    sents, cur = [], []
    for line in up[::-1] + [i] + down:
        if _is_heading(idx[line]):
            if cur:
                sents.append(cur); cur = []
            continue
        for k, w in enumerate(idx[line]["words"]):
            cur.append((line, w))
            # "3." opening a line is a list number; "Figure 1." closing a sentence is not
            if _ends_sentence(w[4]) and not (k == 0 and re.fullmatch(r"\(?\d+[.)]", w[4])):
                sents.append(cur); cur = []
    if cur:
        sents.append(cur)
    weight = idf or {}

    def score(sent):
        toks = {t for _, w in sent for t in tokens(w[4])}
        return sum((3.0 if t[0].isdigit() else 1.0) * weight.get(t, 1.0) for t in qset & toks)

    best = max(sents, key=lambda st: (score(st), any(line == i for line, _ in st)), default=None)
    if not best or score(best) == 0:
        return None
    by_line = {}
    for line, w in best:
        by_line.setdefault(line, []).append(w)
    lines = list(by_line)
    if len(lines) > max_lines:                            # a run-on list: keep the part around the match
        c = lines.index(i) if i in lines else len(lines) // 2
        lines = lines[max(0, c - max_lines // 2):][:max_lines]
    return [[min(w[0] for w in by_line[ln]), min(w[1] for w in by_line[ln]),
             max(w[2] for w in by_line[ln]), max(w[3] for w in by_line[ln])] for ln in lines]


def run(script, idx, figures, progress):
    for ln in idx:                                     # re-tokenize: older indexes counted citation numbers
        ln["toks"] = tokens(ln["text"])
    df = Counter(t for ln in idx for t in set(ln["toks"]))
    n_lines = len(idx) or 1
    idf = {t: math.log(n_lines / (1 + c)) for t, c in df.items()}
    # the reference list is never what a sentence is about ("arXiv preprint" appears in every entry)
    refs = set()
    start = next((i for i, ln in enumerate(idx) if squash(ln["text"]) in ("references", "bibliography")), None)
    if start is not None:
        end = next((i for i in range(start + 1, len(idx)) if squash(idx[i]["text"]) in ("appendix", "appendices")
                    or (_is_heading(idx[i]) and re.match(r"^A[\s.]", idx[i]["text"].strip()))), len(idx))
        refs = set(range(start, end))
    figs = {f["label"].lower().replace("fig.", "figure"): f for f in figures}
    prev = dict(page=1, focus=[0, 0, 612, 792], highlight=[])
    all_s = [s for ch in script["chapters"] for s in ch["sentences"]]
    k = 0
    for ch in script["chapters"]:
        if ch["key"] in ("quiz", "outro"):              # shown as cards, not on the page
            k += len(ch["sentences"])
            continue
        home = _section_pages(idx, ch["title"])
        for s in ch["sentences"]:
            progress(k, len(all_s), "matching sentences to the page"); k += 1
            ref = REF.search(s["text"])
            label = f"{'table' if ref and ref.group(1).lower().startswith('tab') else 'figure'} {ref.group(2)}" if ref else ""
            if label in figs:
                f = figs[label]
                cap = [ln for ln in idx if ln["page"] == f["page"] and
                       re.match(rf"^(figure|fig\.|table)\s+{re.escape(ref.group(2))}\b", ln["text"].lower())]
                # outline and frame the figure together with its caption line, so the
                # outline goes round the highlighted caption instead of through it
                # (the figure finder records the caption's own box; a caption beside wrapped
                # text shares its text line, so the index alone can't isolate it)
                c = f.get("caption_box") or (cap[0]["box"] if cap else None)
                box = f["box"]
                if c:
                    box = [min(box[0], c[0]), min(box[1], c[1]), max(box[2], c[2]), max(box[3], c[3])]
                s["align"] = dict(page=f["page"], focus=box, outline=box, figure=f["label"],
                                  highlight=[c] if c else [])
                prev = s["align"]
                continue
            q = tokens(s["text"])
            if not q:
                s["align"] = dict(prev, highlight=[]); continue
            qset = set(q)
            best, best_i = 0.0, None
            for i, ln in enumerate(idx):
                if not ln["toks"] or i in refs:
                    continue
                hit = qset.intersection(ln["toks"])
                if not hit:
                    continue
                score = sum((3.0 if re.match(r"\d", t) else 1.0) * idf.get(t, 0) for t in hit)
                if home and home <= ln["page"] <= home + 1:
                    score *= 1.3                                  # prefer the chapter's own section
                elif home and abs(ln["page"] - home) <= 2:
                    score *= 1.1
                if ln["page"] == prev.get("page"):
                    score *= 1.1                                  # and avoid needless page jumps
                if score > best:
                    best, best_i = score, i
            if best_i is None or best < 4.0:
                s["align"] = dict(prev, highlight=[]); continue
            ln0 = idx[best_i]
            # highlight the whole sentence of the paper that the best line belongs to
            hl = sentence_span(idx, best_i, qset, idf)
            if not hl:                                    # older index without word positions
                lines = [best_i] + [j for j in (best_i - 1, best_i + 1) if 0 <= j < len(idx)
                                    and idx[j]["page"] == ln0["page"]
                                    and sum(idf.get(t, 0) for t in qset.intersection(idx[j]["toks"])) >= 0.35 * best]
                hl = [idx[j]["box"] for j in sorted(lines)]
            focus = _paragraph(idx, best_i)
            focus = [min(focus[0], *(b[0] for b in hl)), min(focus[1], *(b[1] for b in hl)),
                     max(focus[2], *(b[2] for b in hl)), max(focus[3], *(b[3] for b in hl))]
            s["align"] = dict(page=ln0["page"], focus=focus, highlight=hl, score=round(best, 1))
            prev = s["align"]
    progress(len(all_s), len(all_s), "aligned")
    return script
