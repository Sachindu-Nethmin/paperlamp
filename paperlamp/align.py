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

from .pdf import lines_of, words

STOP = set("""a an the and or of to in on for with by from as at is are was were be been being this that these those it its
their they them we our us you your he she his her which who whom whose what when where why how than then there here
also into over under about after before between during each both more most other some such only own same so too very
can will just not no nor but if because while do does did done have has had having one two three four five six seven
eight nine ten paper authors author study show shows shown result results use used using""".split())
REF = re.compile(r"\b(Figure|Fig\.|Table)\s+(\d+|[A-Z]\d*)\b", re.I)


def tokens(s):
    return [w for w in re.findall(r"[a-z][a-z\-]{2,}|\d[\d,]*(?:\.\d+)?", s.lower()) if w not in STOP]


def build_index(pdf, pages):
    """Every text line of the paper with its page and box (points)."""
    idx = []
    for p in range(1, pages + 1):
        for ln in lines_of(words(pdf, p)):
            idx.append(dict(page=p, box=[ln["x0"], ln["y0"], ln["x1"], ln["y1"]], text=ln["text"],
                            toks=tokens(ln["text"])))
    return idx


def _section_pages(idx, chapter_title):
    """Pages where a chapter's section heading appears (search starts there)."""
    t = chapter_title.lower()[:30]
    for ln in idx:
        if len(ln["text"]) < 100 and t and t in re.sub(r"\s+", " ", ln["text"].lower()):
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


def run(script, idx, figures, progress):
    df = Counter(t for ln in idx for t in set(ln["toks"]))
    n_lines = len(idx) or 1
    idf = {t: math.log(n_lines / (1 + c)) for t, c in df.items()}
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
                s["align"] = dict(page=f["page"], focus=f["box"], outline=f["box"], figure=f["label"],
                                  highlight=[cap[0]["box"]] if cap else [])
                prev = s["align"]
                continue
            q = tokens(s["text"])
            if not q:
                s["align"] = dict(prev, highlight=[]); continue
            qset = set(q)
            best, best_i = 0.0, None
            for i, ln in enumerate(idx):
                if not ln["toks"]:
                    continue
                hit = qset.intersection(ln["toks"])
                if not hit:
                    continue
                score = sum((3.0 if re.match(r"\d", t) else 1.0) * idf.get(t, 0) for t in hit)
                if home and abs(ln["page"] - home) <= 2:
                    score *= 1.3                                  # prefer the chapter's own section
                if ln["page"] == prev.get("page"):
                    score *= 1.1                                  # and avoid needless page jumps
                if score > best:
                    best, best_i = score, i
            if best_i is None or best < 4.0:
                s["align"] = dict(prev, highlight=[]); continue
            # highlight the best line plus adjacent lines that also match well
            hl, ln0 = [best_i], idx[best_i]
            for j in (best_i - 1, best_i + 1):
                if 0 <= j < len(idx) and idx[j]["page"] == ln0["page"]:
                    sc = sum(idf.get(t, 0) for t in qset.intersection(idx[j]["toks"]))
                    if sc >= 0.35 * best:
                        hl.append(j)
            hl.sort()
            s["align"] = dict(page=ln0["page"], focus=_paragraph(idx, best_i),
                              highlight=[idx[j]["box"] for j in hl], score=round(best, 1))
            prev = s["align"]
    progress(len(all_s), len(all_s), "aligned")
    return script
