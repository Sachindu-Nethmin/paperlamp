"""In-depth videos: the whole paper, every sentence explained at length, with its mathematics
and its technical terms taught along the way. Often hours long.

Built on the whole-paper reader (reader.py): the same sentences in the same reading order, each
narrated while the highlighter sits on it. On top of that:

- Equations. Display equations are cut out of the page layout line by line
  (reader.split_math), matched to the exact LaTeX in the paper's arXiv source (texsrc.py), and
  walked through: what the paper uses it for, the equation read aloud, each symbol with its
  meaning (that symbol is highlighted on the page while it is explained), then the intuition.
  A paper with no LaTeX source has its equations read off the page image by a local vision
  model, when one is installed; otherwise the PDF's own text of the formula is used.
- Technical terms. A glossary pass collects the paper's terms; each is defined where it is first
  used, the most frequent open the video as "Key terms", and all of them go to glossary.md.
- Background: the concepts the paper assumes, taught before the abstract.
- Each main section opens with what it sets out to do and ends with a short recap; every figure
  and table gets a walkthrough from the text inside it.

Every model answer is cached in the job folder (deep_cache.json), so a job stopped after hours
of writing picks up where it stopped. Explanations that change a number fall back to the paper's
own sentence, and the verify stage checks every number again afterwards.
"""
import base64, hashlib, io, json, pathlib, re, shutil, unicodedata
from collections import Counter

from . import arxiv, llm, texsrc, verify
from .pdf import nice_title, page_image, SCALE
from .reader import (GREEK_CH, MATH_OPS, _join, _union, block_text, boxes_of, classify, items, layout,
                     split_math)
from .script import author_sentences, fig_label, listing, paper_line
from .speak import int_words

BATCH = 6                         # sentences explained per model call (longer answers than reader.py)
N_KEY_TERMS = 10
N_CONCEPTS = 4
PROMPT_VERSION = "deep-1"         # part of every cache key: a changed prompt asks again


class Cache:
    """Model answers by (prompt, model), kept on disk after every answer."""

    def __init__(self, path):
        self.path = pathlib.Path(path)
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self.hits = 0

    def ask(self, name, model, prompt, **kw):
        key = hashlib.sha1(f"{PROMPT_VERSION}|{name}|{model}|{prompt}".encode()).hexdigest()
        if key in self.data:
            self.hits += 1
            return self.data[key]
        try:
            res = llm.chat(prompt, model=model, **kw)
        except RuntimeError:
            return {}                                     # not cached: asked again next time
        self.data[key] = res
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
        return res


# ── equations ────────────────────────────────────────────────────────────────
def mark_equations(blocks, figs):
    """Formula blocks of the paper's text (not the reference list, not inside a figure or
    table) become kind "equation"; reader.classify left them as "skip"."""
    for b in blocks:
        if not b.get("eq") or b["part"] not in ("body", "abstract", "appendix"):
            continue
        inside = any(f["page"] == b["page"] and f.get("box") and _center_in(b["box"], f["box"]) for f in figs)
        if not inside and b["kind"] in ("skip", "text"):
            b["kind"] = "equation"
    return blocks


def _center_in(box, outer, pad=2):
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return outer[0] - pad <= cx <= outer[2] + pad and outer[1] - pad <= cy <= outer[3] + pad


def eq_numbers(nums):
    """["(2)"] -> ["2"]; the numbers the paper prints beside an equation."""
    return [re.sub(r"[()\s]", "", n) for n in nums or []]


def match_latex(pdf_eqs, tex_eqs, threshold=0.5):
    """Pair the PDF's equations with the source's, in order (a monotone alignment that
    maximises similarity; a matching equation number adds a little). Returns {pdf index: tex index}."""
    n, m = len(pdf_eqs), len(tex_eqs)
    if not n or not m:
        return {}

    def score(i, j):
        s = texsrc.similarity(pdf_eqs[i]["text"], tex_eqs[j]["latex"])
        nums = {x for x in eq_numbers(pdf_eqs[i].get("nums")) if x.isdigit()}
        if nums & {str(k) for k in tex_eqs[j].get("numbers", [])}:
            s += 0.25
        return s
    sc = [[score(i, j) for j in range(m)] for i in range(n)]
    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            take = best[i - 1][j - 1] + sc[i - 1][j - 1] if sc[i - 1][j - 1] >= threshold else -1
            best[i][j] = max(best[i - 1][j], best[i][j - 1], take)
    pairs, i, j = {}, n, m
    while i and j:
        if sc[i - 1][j - 1] >= threshold and best[i][j] == best[i - 1][j - 1] + sc[i - 1][j - 1]:
            pairs[i - 1] = j - 1; i -= 1; j -= 1
        elif best[i][j] == best[i - 1][j]:
            i -= 1
        else:
            j -= 1
    return pairs


def _base(sym):
    """The character a symbol is written with on the page: "\\hat\\beta_{j}" -> "β", "w^\\top" -> "w"."""
    t = texsrc.plain(sym).strip()
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    for c in t:
        if c in GREEK_CH or c.isalpha():
            return c
    return ""


def symbol_boxes(eq_words, sym):
    """Boxes of the words in an equation that show this symbol (its main letter, any accent or
    subscript ignored)."""
    base = _base(sym)
    if not base:
        return []
    hits = []
    for w in eq_words:
        t = unicodedata.normalize("NFD", w[4])
        t = "".join(c for c in t if unicodedata.category(c) != "Mn")
        if t[:1] == base and (len(t) == 1 or not t[1:2].isalpha() or base in GREEK_CH):
            hits.append(list(w[:4]))
    return hits[:6]


def transcribe(pdf_path, eq, model, cache, img_cache):
    """The equation's LaTeX, read off the page image by a vision model; "" if it does not look
    like the formula the PDF's text layer shows."""
    img = page_image(pdf_path, eq["page"], img_cache)
    x0, y0, x1, y1 = eq["box"]
    crop = img.crop((max(0, int((x0 - 6) * SCALE)), max(0, int((y0 - 6) * SCALE)),
                     int((x1 + 6) * SCALE), int((y1 + 6) * SCALE)))
    buf = io.BytesIO()
    crop.save(buf, "PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    res = cache.ask("transcribe_equation", model, llm.skill("transcribe_equation", pdf_text=eq["text"]) +
                    f"\n[image {hashlib.sha1(b64.encode()).hexdigest()[:12]}]", images=[b64], max_tokens=400)
    latex = texsrc.clean(str((res or {}).get("latex") or ""))
    return latex if latex and texsrc.similarity(eq["text"], latex) >= 0.35 else ""


# ── technical terms ──────────────────────────────────────────────────────────
def term_re(term):
    t = term.strip()
    if re.fullmatch(r"[A-Z0-9][A-Za-z0-9\-]{1,9}", t) and sum(c.isupper() for c in t) >= 2:
        return re.compile(rf"(?<![A-Za-z0-9]){re.escape(t)}s?(?![A-Za-z0-9])")      # an acronym: case matters
    words = [re.escape(w) for w in re.split(r"[\s\-]+", t) if w]
    return re.compile(r"(?<![A-Za-z0-9])" + r"[\s\-]+".join(words) + r"(?:s|es)?(?![A-Za-z0-9])", re.I)


def glossary(chunks, paper, model, cache, tick):
    """[{term, definition}] from the paper's text, in chunks of about 1,200 words."""
    terms, seen = [], set()
    for text in chunks:
        res = cache.ask("glossary_terms", model, llm.skill("glossary_terms", paper=paper, text=text, n="8"),
                        max_tokens=900)
        tick("collecting technical terms")
        for t in (res or {}).get("terms") or []:
            if not isinstance(t, dict):
                continue
            term = re.sub(r"\s+", " ", str(t.get("term") or "")).strip(" .,:;\"'")
            d = re.sub(r"\s+", " ", str(t.get("definition") or "")).strip()
            if 2 <= len(term) <= 60 and len(d.split()) >= 4 and term.lower() not in seen:
                seen.add(term.lower())
                terms.append(dict(term=term, definition=d[:1].upper() + d[1:]))
    return terms


def text_chunks(texts, size=1200):
    out, cur, n = [], [], 0
    for t in texts:
        cur.append(t); n += len(t.split())
        if n >= size:
            out.append(" ".join(cur)); cur, n = [], 0
    if cur:
        out.append(" ".join(cur))
    return out


def term_boxes(words, term):
    """Where a term sits inside a sentence on the page: (page, line boxes), or None."""
    first = re.split(r"[\s\-]+", term.strip())
    n = len(first)
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    toks = [norm(x["w"][4]) for x in words]
    want = [norm(w) for w in first]
    for i in range(len(toks) - n + 1):
        if all(toks[i + k].startswith(want[k]) and want[k] for k in range(n)):
            ws = words[i:i + n]
            page = ws[0]["page"]
            rows = []
            for x in ws:
                w = x["w"]
                if x["page"] != page:
                    continue
                if rows and abs(rows[-1][1] - w[1]) < 3:
                    r = rows[-1]; rows[-1] = [min(r[0], w[0]), min(r[1], w[1]), max(r[2], w[2]), max(r[3], w[3])]
                else:
                    rows.append(list(w[:4]))
            return page, rows
    return None


# ── checks ───────────────────────────────────────────────────────────────────
def numbers_ok(text, known, extra=()):
    allowed = set(known) | {verify.norm(x) for x in extra}
    return all(verify.norm(n) in allowed for n in verify.numbers_in(text))


NAMES = {k: k for k in texsrc.GREEK}          # \theta -> theta


def say_math(text):
    """Leftover notation in a model's answer, said in words: "theta_i" -> "theta i",
    "$\\beta_j$" -> "beta j", "f_{j}" -> "f j". What cannot be said this way stays, and fails spoken_ok."""
    t = re.sub(r"(?<!\\)\$([^$]{1,80})\$", r"\1", str(text or ""))
    t = re.sub(r"\\(" + "|".join(sorted(NAMES, key=len, reverse=True)) + r")(?![A-Za-z])", lambda m: m.group(1).lower(), t)
    t = re.sub(r"\\(?:hat|tilde|bar)\{?([A-Za-z]+)\}?", r"\1 hat", t)
    t = re.sub(r"(?<=[A-Za-z0-9])_\{([A-Za-z0-9 ,]+)\}", r" \1", t)
    t = re.sub(r"(?<=[A-Za-z0-9])_(?=[A-Za-z0-9])", " ", t)
    t = re.sub(r"\^\{?2\}?(?![\w])", " squared", t)
    return re.sub(r"\s+", " ", t).strip()


def spoken_ok(text):
    """No LaTeX or loose symbols left in what the voice will read."""
    return not re.search(r"\\[A-Za-z]+|[_^{}$]", text)


def sentences_of(text):
    parts = re.split(r"(?<=[.?!])\s+(?=[A-Z0-9\"'“])", re.sub(r"\s+", " ", str(text or "")).strip())
    return [p for p in parts if len(p.split()) >= 3]


def _cap(s):
    s = say_math(s)
    s = s[:1].upper() + s[1:]
    return s if not s or s.endswith((".", "?", "!")) else s + "."


# ── the script ───────────────────────────────────────────────────────────────
def write(pdf_path, meta, figs, heads, model, progress, appendix=False, job_dir=None, arxiv_id="",
          known=None, vision=""):
    """The in-depth script: chapters of explanations, equation walkthroughs, key terms and
    background, each sentence tied to its exact spot on the page."""
    job_dir = pathlib.Path(job_dir or pathlib.Path(pdf_path).parent)
    cache = Cache(job_dir / "deep_cache.json")
    pages = split_math(layout(pdf_path))
    blocks = mark_equations(classify(pages, figs, heads), figs)
    stream = items(blocks, appendix)
    body_text = " ".join(block_text(b) for b in blocks if b["kind"] == "text")
    known = set(known) if known is not None else verify.paper_numbers(body_text)
    figs_by = {f["label"]: f for f in figs}
    paper = paper_line(meta)

    # ── 1. the paper as items, in reading order (as reader.write, plus equations) ──
    chapters = [dict(key="abstract", title="Abstract", items=[], num="")]
    eqs = []
    for kind, x in stream:
        if kind == "heading":
            h = x.get("head") or {}
            title = nice_title(re.sub(r"\s+", " ", h.get("title") or block_text(x)).strip(), body_text)
            num = h.get("num") or ""
            chapters.append(dict(key=f"s{num or len(chapters)}", title=title, num=num, appendix=x["part"] == "appendix",
                                 items=[dict(kind="heading", text=f"Section {num}: {title}." if num else f"{title}.",
                                             align=dict(page=x["page"], focus=_union([x["box"]], 40),
                                                        highlight=[ln["box"] for ln in x["lines"]]))]))
        elif kind == "caption":
            t = _join([dict(w=w, eol=k == len(ln["words"]) - 1) for ln in x["lines"] for k, w in enumerate(ln["words"])])
            f = figs_by.get(fig_label(t) or "")
            hl = [ln["box"] for ln in x["lines"]]
            box = _union(hl + ([f["box"]] if f else []))
            chapters[-1]["items"].append(dict(kind="caption", source=t, figure=f["label"] if f else None,
                                              fig=f, align=dict(page=x["page"], focus=box, outline=box, highlight=hl,
                                                                **({"figure": f["label"]} if f else {}))))
        elif kind == "equation":
            lines = x.get("all_lines") or x["lines"]
            words = [w for ln in x["lines"] for w in ln["words"]]
            eq = dict(page=x["page"], box=x["box"], text=block_text(x), nums=x.get("nums") or [], words=words,
                      lines=[ln["box"] for ln in lines if ln["words"]])
            eqs.append(eq)
            chapters[-1]["items"].append(dict(kind="equation", eq=eq))
        else:
            t = _join(x)
            if len(t.split()) < 3 and not t.endswith((".", "?", "!")):
                continue
            page, hl = boxes_of(x)
            para = _union([blocks[w["block"]]["box"] for w in x if w["page"] == page] + hl)
            chapters[-1]["items"].append(dict(kind="sentence", source=t, words=x,
                                              align=dict(page=page, focus=para, highlight=hl)))
    chapters = [c for c in chapters if c["items"]]
    srcs = [it for c in chapters for it in c["items"] if it["kind"] in ("sentence", "caption")]
    src_at = {id(it): k for k, it in enumerate(srcs)}
    groups = top_groups(chapters)
    fig_items = [it for c in chapters for it in c["items"] if it["kind"] == "caption" and it.get("fig")]

    # ── 2. the LaTeX source ──
    tex = ""
    src = arxiv.source(arxiv_id, job_dir / "src") if arxiv_id else (job_dir / "src" if (job_dir / "src").exists() else None)
    if src:
        tex = texsrc.flatten(src)
    defs = texsrc.macros(tex) if tex else {}
    tex_eqs = texsrc.equations(tex, defs) if tex else []
    tex_sents = texsrc.math_sentences(tex, defs) if tex else []
    pairs = match_latex(eqs, tex_eqs)
    for i, e in enumerate(eqs):
        if i in pairs:
            e.update(latex=tex_eqs[pairs[i]]["latex"], latex_from="the paper's LaTeX source")
    missing = [e for e in eqs if not e.get("latex")]

    chunks = text_chunks([it["source"] for it in srcs])
    batches = sum((len([it for it in c["items"] if it["kind"] in ("sentence", "caption")]) + BATCH - 1) // BATCH
                  for c in chapters)
    total = len(chunks) + 1 + len(eqs) + batches + len(fig_items) + len(groups) + (len(missing) if vision else 0)
    done = [0]

    def tick(msg):
        done[0] += 1
        progress(min(done[0], total), total, msg)
    progress(0, total, f"{len(srcs)} sentences, {len(eqs)} equations ({len(eqs) - len(missing)} from the LaTeX source), "
                       f"{len(fig_items)} figures and tables")

    # ── 3. equations no source covers: read them off the page with a vision model ──
    if vision and missing:
        for e in missing:
            latex = transcribe(pdf_path, e, vision, cache, job_dir / "pages_eq")
            if latex:
                e.update(latex=latex, latex_from="the page image (read by a local vision model)")
            tick("reading equations off the page")
        if vision != model:
            llm.unload(vision)                        # one model in memory at a time
        shutil.rmtree(job_dir / "pages_eq", ignore_errors=True)    # 300-dpi page scans, only needed here

    # ── 4. technical terms and background ──
    terms = glossary(chunks, paper, model, cache, tick)
    counts = {t["term"]: len(term_re(t["term"]).findall(body_text)) for t in terms}
    terms = [t for t in terms if counts[t["term"]] >= 1]
    first_use, introduced = {}, set()
    for k, it in enumerate(srcs):
        for t in terms:
            if t["term"] not in introduced and term_re(t["term"]).search(it["source"]):
                introduced.add(t["term"])
                first_use.setdefault(k, []).append(t)
                t["at"] = k
    key_terms = []
    for t in sorted([t for t in terms if "at" in t], key=lambda t: -counts[t["term"]]):
        low = t["term"].lower()                      # "IRT model" adds nothing once "IRT" is a key term
        if not any(k["term"].lower() in low or low in k["term"].lower() for k in key_terms):
            key_terms.append(t)
    key_terms = sorted(key_terms[:N_KEY_TERMS], key=lambda t: t["at"])
    abstract = " ".join(it["source"] for c in chapters if c["key"] == "abstract" for it in c["items"]
                        if it["kind"] == "sentence")[:2500]
    res = cache.ask("background_concepts", model, llm.skill(
        "background_concepts", paper=paper, abstract=abstract or "(no abstract found)",
        terms=", ".join(t["term"] for t in terms[:30]) or "(none)",
        equations="; ".join(e["latex"] for e in eqs if e.get("latex"))[:1500] or "(none)", n=str(N_CONCEPTS)),
        max_tokens=1400)
    tick("background concepts")
    concepts = []
    for c in (res or {}).get("concepts") or []:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        ss = [_cap(s) for s in (c.get("sentences") or []) if isinstance(s, str)]
        ss = [s for s in ss if len(s.split()) >= 5 and numbers_ok(s, set()) and spoken_ok(s)]
        if len(ss) >= 2:
            concepts.append(dict(name=str(c["name"]).strip(), sentences=ss[:5]))

    # ── 5. equations, symbol by symbol ──
    flat = [(ci, k) for ci, c in enumerate(chapters) for k in range(len(c["items"]))]
    pos = {id(chapters[ci]["items"][k]): n for n, (ci, k) in enumerate(flat)}
    every = [chapters[ci]["items"][k] for ci, k in flat]
    for ci, c in enumerate(chapters):
        for it in c["items"]:
            if it["kind"] != "equation":
                continue
            e, n = it["eq"], pos[id(it)]
            before = " ".join(x["source"] for x in every[max(0, n - 3):n] if x.get("source"))[-700:]
            after = " ".join(x["source"] for x in every[n + 1:n + 3] if x.get("source"))[:500]
            number = eq_numbers(e["nums"])
            latex = e.get("latex") or e["text"]
            res = cache.ask("explain_equation", model, llm.skill(
                "explain_equation", paper=paper, section=c["title"], before=before or "(start of section)",
                after=after or "(end of section)", latex=latex,
                number=f" ({', '.join(number)})" if number else "",
                source_note=f", from {e['latex_from']}" if e.get("latex") else
                ", as the PDF's text layer gives it (subscripts and superscripts may be flattened)"),
                max_tokens=1100)
            tick(f"equations: {c['title']}")
            # frame the equation with its paragraph's full width, so the lines around it aren't cut off
            near = [x["align"]["focus"] for x in every[max(0, n - 2):n + 3]
                    if x.get("align") and x["align"].get("page") == e["page"] and x["kind"] == "sentence"]
            width = (min(f[0] for f in near), max(f[2] for f in near)) if near else None
            it["narration"] = equation_sentences(e, res or {}, known, number, width)

    # ── 6. every sentence, in depth ──
    k_src = 0
    for c in chapters:
        part = [it for it in c["items"] if it["kind"] in ("sentence", "caption")]
        before = ""
        for k in range(0, len(part), BATCH):
            batch = part[k:k + BATCH]
            listing_ = []
            for i, it in enumerate(batch):
                line = f"{i + 1}. " + ("Figure caption: " if it["kind"] == "caption" else "") + it["source"]
                hint = None
                if tex_sents and (set(it["source"]) & (GREEK_CH | MATH_OPS) or re.search(r"\s[a-z]\s[a-z]{1,3}\s", it["source"])):
                    hint = texsrc.best_sentence(it["source"], tex_sents)
                if hint:
                    line += f"\n   LaTeX of this sentence: {hint['latex'][:500]}"
                new = first_use.get(src_at[id(it)], [])
                if new:
                    line += "\n   New terms: " + "; ".join(f"{t['term']} ({t['definition']})" for t in new)
                listing_.append(line)
            res = cache.ask("explain_deep", model, llm.skill(
                "explain_deep", paper=paper, section=c["title"], before=before or "(start)", n=str(len(batch)),
                sentences="\n".join(listing_)), max_tokens=160 + 170 * len(batch))
            got = [x if isinstance(x, str) else "" for x in ((res or {}).get("explanations") or [])]
            for i, it in enumerate(batch):
                e = re.sub(r"^\s*\d+[.)]\s*", "", got[i]).strip() if i < len(got) else ""
                e = re.sub(r"^(This|The) sentence (says|means|explains|states) that\s+", "", e, flags=re.I)
                e = say_math(re.sub(r"^Figure caption:\s*", "", e))
                ok = len(e.split()) >= 6 and numbers_ok(e, known) and spoken_ok(e)
                it["text"] = _cap(e) if ok else it["source"]
                it["explained"] = ok
            before = " ".join(it["source"] for it in batch[-2:])
            k_src += len(batch)
            tick(f"explaining: {c['title']}")

    # ── 7. figures and tables, walked through ──
    for c in chapters:
        for it in c["items"]:
            if it["kind"] != "caption" or not it.get("fig"):
                continue
            f = it["fig"]
            inside = figure_text(pages, f)
            res = cache.ask("explain_figure", model, llm.skill(
                "explain_figure", paper=paper, section=c["title"], caption=it["source"],
                kind="table" if f["label"].lower().startswith("table") else "figure",
                inside=inside or "(no text inside: a picture or a chart without labels)"), max_tokens=700)
            tick(f"figures: {f['label']}")
            walk = [_cap(s) for s in (res or {}).get("walkthrough") or [] if isinstance(s, str)]
            it["walk"] = [s for s in walk if len(s.split()) >= 5 and numbers_ok(s, known) and spoken_ok(s)][:5]

    # ── 8. each main section: what it sets out to do, and a recap ──
    for g in groups:
        texts = [it["source"] for c in g for it in c["items"] if it.get("source")]
        if len(texts) < 6:
            tick("section openings and recaps")
            continue
        subs = [c["title"] for c in g[1:]]
        res = cache.ask("section_frame", model, llm.skill(
            "section_frame", paper=paper, section=g[0]["title"],
            subsections=f" (with the subsections {listing(subs)})" if subs else "",
            text=" ".join(texts)[:5000]), max_tokens=500)
        tick("section openings and recaps")
        intro = _cap((res or {}).get("intro") or "")
        recap = [_cap(s) for s in (res or {}).get("recap") or [] if isinstance(s, str)]
        if len(intro.split()) >= 6 and numbers_ok(intro, known) and spoken_ok(intro):
            g[0]["intro"] = intro
        g[-1]["recap"] = [s for s in recap if len(s.split()) >= 5 and numbers_ok(s, known) and spoken_ok(s)][:3]

    # ── 9. the finished chapters ──
    title_b = next((b for b in blocks if b["kind"] == "title"), None)
    head = [dict(text=f"This video explains \"{meta.get('title', '')}\" in depth, sentence by sentence, "
                      "from beginning to end.",
                 **({"exact": True, "align": dict(page=title_b["page"], focus=_union([title_b["box"]], 60),
                                                  highlight=[ln["box"] for ln in title_b["lines"]])} if title_b else {}))]
    out = [dict(key="paper", title="The paper", sentences=head + author_sentences(meta.get("authors") or []))]
    mains = [g[0]["title"] for g in groups if not g[0].get("appendix") and g[0]["key"] != "abstract"]
    road = []
    if mains:
        road.append(dict(text=f"After the abstract, the paper has {int_words(len(mains))} main parts: "
                              f"{listing(mains)}.", hold=True, fixed=True))
    road.append(dict(text="We go through every sentence in order, with the highlighter on the sentence "
                          "being explained.", hold=True, fixed=True))
    if eqs:
        road.append(dict(text=("Its equation is" if len(eqs) == 1 else f"Each of its {int_words(len(eqs))} equations is")
                              + " read aloud and taken apart symbol by symbol.", hold=True, fixed=True))
    if key_terms or concepts:
        road.append(dict(text="First, the key terms" + (" and the background you need." if concepts else "."),
                         hold=True, fixed=True))
    out.append(dict(key="roadmap", title="What this video covers", sentences=road))
    if key_terms:
        ks = []
        for t in key_terms:
            it = srcs[t["at"]]
            spot = term_boxes(it.get("words") or [], t["term"]) if it.get("words") else None
            align = (dict(page=spot[0], focus=it["align"]["focus"], highlight=spot[1]) if spot and spot[1]
                     else dict(it["align"]))
            ks.append(dict(text=f"{t['term']}: {t['definition']}", exact=True, align=align, term=t["term"]))
        out.append(dict(key="terms", title="Key terms", sentences=ks))
    if concepts:
        bs = []
        for c in concepts:
            bs.append(dict(text=f"{c['name']}.", hold=True, fixed=True))
            bs += [dict(text=s, hold=True) for s in c["sentences"]]
        out.append(dict(key="background", title="Background", sentences=bs))
    for c in chapters:
        ss = []
        for it in c["items"]:
            if it["kind"] == "heading":
                ss.append(dict(text=it["text"], fixed=True, exact=True, align=it["align"]))
                if c.get("intro"):
                    ss.append(dict(text=c["intro"], hold=True))
            elif it["kind"] == "equation":
                ss += it.get("narration") or []
            else:
                ss.append(dict(text=it.get("text") or it["source"], source=it["source"], exact=True, align=it["align"],
                               **({"figure": it["figure"]} if it.get("figure") else {})))
                for s in it.get("walk") or []:
                    a = it["align"]
                    ss.append(dict(text=s, exact=True, align=dict(page=a["page"], focus=a["focus"], outline=a.get("outline"),
                                                                 highlight=[], **({"figure": a["figure"]} if a.get("figure") else {}))))
        if c.get("recap"):
            ss.append(dict(text="To recap.", hold=True, fixed=True))
            ss += [dict(text=s, hold=True) for s in c["recap"]]
        if ss:
            out.append(dict(key=c["key"], title=c["title"], sentences=ss))
    out.append(dict(key="outro", title="Source", sentences=[dict(
        text="The paper is linked in the description, with a glossary of its terms. Thanks for watching.", fixed=True)]))
    write_side_files(job_dir, meta, terms, eqs)
    n = sum(len(c["sentences"]) for c in out)
    explained = sum(1 for it in srcs if it.get("explained"))
    progress(total, total, f"{len(srcs)} sentences ({explained} explained in depth), {len(eqs)} equations, "
                           f"{len(terms)} terms · {n} in the video" + (f" · {cache.hits} answers reused" if cache.hits else ""))
    return dict(meta=meta, chapters=out, source="ollama", model=model, depth="deep", appendix_included=bool(appendix),
                glossary=[dict(term=t["term"], definition=t["definition"]) for t in terms],
                equations=[dict(number=", ".join(eq_numbers(e["nums"])), page=e["page"], latex=e.get("latex", ""),
                                latex_from=e.get("latex_from", "the PDF text layer"), pdf_text=e["text"]) for e in eqs])


def equation_sentences(e, res, known, number, width=None):
    """The walkthrough of one equation: what it is for, read aloud, each symbol (highlighted as
    it is explained), then the intuition. Falls back to naming it, if the model gave nothing usable.
    width: the x-range of the text around it, which the camera frames too."""
    pad = _union([e["box"]], 50)
    if width:
        pad = [min(pad[0], width[0]), pad[1], max(pad[2], width[1]), pad[3]]
    lines = e["lines"] or [e["box"]]
    extra = re.findall(r"\d+(?:\.\d+)?", e.get("latex") or e["text"])
    ok = lambda s: len(s.split()) >= 3 and numbers_ok(s, known, extra) and spoken_ok(s)
    label = f"Equation {number[0]}" if number else "This equation"
    role = _cap(res.get("role") or "")
    out = [dict(text=f"{label}. {role}" if ok(role) else f"{label}.", exact=True,
                align=dict(page=e["page"], focus=pad, highlight=lines))]
    ra = _cap(res.get("read_aloud") or "")
    if ok(ra):
        out.append(dict(text=f"Read aloud, it says: {ra[0].lower() + ra[1:]}", exact=True,
                        align=dict(page=e["page"], focus=pad, highlight=lines)))
    for s in (res.get("symbols") or [])[:8]:
        if not isinstance(s, dict):
            continue
        t = _cap(s.get("explain") or "")
        if not ok(t):
            continue
        boxes = symbol_boxes(e["words"], str(s.get("symbol") or ""))
        out.append(dict(text=t, exact=True, symbol=str(s.get("symbol") or ""),
                        align=dict(page=e["page"], focus=pad, highlight=boxes or lines)))
    for s in (res.get("intuition") or [])[:4]:
        t = _cap(s) if isinstance(s, str) else ""
        if ok(t):
            out.append(dict(text=t, exact=True, align=dict(page=e["page"], focus=pad, outline=e["box"], highlight=[])))
    return out


def top_groups(chapters):
    """Chapters grouped by main section: "3", "3.1" and "3.2.1" together. The abstract is a group of its own."""
    groups, cur = [], None
    for c in chapters:
        top = c.get("num", "").split(".")[0] if c.get("num") else c["key"]
        if cur is None or top != cur[0] or "." not in (c.get("num") or ""):
            cur = (top, [c]); groups.append(cur)
        else:
            cur[1].append(c)
    return [g for _, g in groups]


def figure_text(pages, f, limit=350):
    """The words inside a figure or table, line by line (axis labels, row and column names, cells)."""
    pg = next((p for p in pages if p["page"] == f["page"]), None)
    if not pg or not f.get("box"):
        return ""
    ws = [w for b in pg["blocks"] for ln in b["lines"] for w in ln["words"] if _center_in(w[:4], f["box"], 1)]
    ws.sort(key=lambda w: (round(w[1] / 4), w[0]))
    rows, y = [], None
    for w in ws:
        if y is None or abs(w[1] - y) > 4:
            rows.append([]); y = w[1]
        rows[-1].append(w[4])
    text = "\n".join(" ".join(r) for r in rows)
    return " ".join(text.split()[:limit]) if len(text.split()) > limit else text


def write_side_files(job_dir, meta, terms, eqs):
    """out/glossary.md and out/equations.md: what the video taught, to read again."""
    out = pathlib.Path(job_dir) / "out"
    out.mkdir(exist_ok=True)
    title = meta.get("title", "")
    g = [f"# Glossary: {title}", "", "Terms in the order the paper first uses them.", ""]
    g += [f"- **{t['term']}**: {t['definition']}" for t in sorted(terms, key=lambda t: t.get("at", 1 << 30))]
    (out / "glossary.md").write_text("\n".join(g) + "\n", encoding="utf-8")
    if eqs:
        q = [f"# Equations: {title}", ""]
        for e in eqs:
            num = ", ".join(eq_numbers(e["nums"]))
            q += [f"## {'Equation ' + num if num else 'Unnumbered equation'} (page {e['page']})", "",
                  f"$$ {e.get('latex') or e['text']} $$", "",
                  f"_From {e.get('latex_from', 'the PDF text layer')}._", ""]
        (out / "equations.md").write_text("\n".join(q), encoding="utf-8")
