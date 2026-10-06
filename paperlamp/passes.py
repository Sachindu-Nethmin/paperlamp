"""Reading-pass videos, after S. Keshav's three-pass method ("How to Read a Paper",
ACM SIGCOMM Computer Communication Review, 2007).

  pass1  about 5 to 10 minutes: decide whether the paper matters. Title, abstract and
         introduction; how the paper is organised; the conclusion; every figure and
         table with its caption; a glance at the references; then the pass-one answers
         (what problem, what they did, what they found, who should read further).
  pass2  understand the evidence: the experimental setup first, then each result with
         what it compares, the limitations, and the references worth following up.
  pass3  the method in depth (with the appendix), the assumptions it rests on, and what
         you would need to reproduce it.

Facts still come only from the section notes, figure captions, headings and the
reference list, and every number goes through the same check as any other script.
Sentences can carry an "anchor" (text to highlight on the page) or a "figure" (keep
the camera on that figure), so structure and reference sentences land on the right
line even though they don't quote the paper.
"""
import re
from collections import Counter

from . import llm
from .analyze import facts_text, is_limitation_section
from .script import SKIP, author_sentences, fig_label, near_dup, paper_line

WORDS = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen " \
        "seventeen eighteen nineteen twenty".split()
SETUP = re.compile(r"experiment|setup|setting|baseline|dataset|data |evaluat|metric|implementation|benchmark", re.I)
RESULT = re.compile(r"result|experiment|evaluat|ablation|analysis|finding|comparison", re.I)
CONCL = re.compile(r"conclu|summary|discussion", re.I)
NON_METHOD = re.compile(r"introduction|related|background|experiment|result|evaluat|ablation|conclu|discussion|"
                        r"limitation|threat|front matter|acknowledg|ethic|reproducib", re.I)


def word(n):
    return WORDS[n] if 0 <= n < len(WORDS) else str(n)


def listing(items):
    items = [i for i in items if i]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else ""


class Writer:
    """Asks the local model for a few sentences at a time, never repeating itself."""

    def __init__(self, meta, model, progress, steps):
        self.meta, self.model, self.progress, self.steps = meta, model, progress, steps
        self.said, self.step = [], 0

    def tick(self, msg):
        self.progress(min(self.step, self.steps - 1), self.steps, msg)
        self.step += 1

    def ask(self, title, goal, n, notes, extra="", figures="(none)", summary=False):
        """summary=True: a recap may restate earlier facts, so only repeats within it are dropped."""
        if n <= 0:
            return []
        try:
            res = llm.chat(llm.skill("chapter_script", chapter=title, goal=goal, n=str(n), paper=paper_line(self.meta),
                                     facts=facts_text(notes) if notes else "(none)", extra=extra,
                                     avoid="\n".join(f"  * {x}" for x in self.said[-10:]) or "  (nothing yet)",
                                     figures=figures), model=self.model, max_tokens=150 + 60 * n)
            got = [x.strip() for x in res.get("sentences", []) if isinstance(x, str) and len(x.split()) >= 4]
        except RuntimeError:
            got = []
        out = []
        for x in got[:n]:
            if not any(near_dup(x, y) for y in ([] if summary else self.said) + out):
                out.append(x)
        self.said += out
        return out


def chapter(key, title, sentences):
    sents = [x if isinstance(x, dict) else dict(text=x) for x in sentences if x]
    return dict(key=key, title=title, sentences=sents) if sents else None


def intro_chapter(meta, what):
    t, authors = meta.get("title", ""), meta.get("authors") or []
    venue, year = meta.get("venue", ""), meta.get("year", "")
    s = [f"This is {what} through \"{t}\"."] + author_sentences(authors)
    if "preprint" in venue.lower():
        s.append(f"It is an arXiv preprint{f' from {year}' if year else ''}, so it has not been peer reviewed yet.")
    elif venue:
        s.append(f"It was published in {venue}{f' in {year}' if year else ''}.")
    return s


# ── structure and references (deterministic) ───────────────────────────────────
def structure_sentences(doc):
    """One sentence per top-level section, naming its subsections, anchored on its heading."""
    tops, order = {}, []
    for sc in doc.get("sections", []):
        num = sc["num"]
        if num in ("0", "R") or sc.get("appendix"):
            continue
        top = num.split(".")[0]
        if top not in tops:
            tops[top] = dict(title=sc["parent"] if "." in num else sc["title"], subs=[], num=top)
            order.append(top)
        if "." in num:
            tops[top]["subs"].append(sc["title"])
    first = tops[order[0]] if order else None
    out = [dict(text=f"The main text has {word(len(order))} sections.",
                anchor=dict(text=f"{first['num']} {first['title']}", heading=True))] if order else []
    for top in order:
        t = tops[top]
        subs = [s for s in t["subs"] if s.lower() != t["title"].lower()]
        text = f"Section {top}, {t['title']}" + (f", covers {listing(subs[:4])}" if subs else "") + "."
        out.append(dict(text=text, anchor=dict(text=f"{top} {t['title']}", heading=True)))
    app = [sc for sc in doc.get("sections", []) if sc.get("appendix")]
    if app:
        names = []
        for sc in app:
            if sc["title"] not in names:
                names.append(sc["title"])
        out.append(dict(text=f"An appendix adds {listing(names[:4])}.",
                        anchor=dict(text=f"{app[0]['num']} {app[0]['title']}", heading=True)))
    return out


def references(paper_text):
    """{number: entry text} for a numbered reference list ([1] ...), else {}."""
    m = re.search(r"\n\s*(R\s?EFERENCES|References|REFERENCES|Bibliography)\s*\n", paper_text)
    if not m:
        return {}, paper_text
    body, tail = paper_text[:m.start()], paper_text[m.end():]
    refs, cur = {}, None
    for ln in tail.splitlines():
        r = re.match(r"^\s*\[(\d+)\]\s+(.*)", ln)
        if r:
            cur = int(r.group(1)); refs[cur] = r.group(2).strip()
        elif cur is not None and ln.strip() and not re.match(r"^\s*(Appendix|A\s{2,}|A\s+[A-Z])", ln):
            if len(refs[cur]) < 400:
                refs[cur] += " " + ln.strip()
        elif cur is not None and re.match(r"^\s*(Appendix|APPENDIX)", ln):
            break
    return refs, body


def cited_counts(text):
    counts = Counter()
    for grp in re.findall(r"\[(\d+(?:\s*[,–-]\s*\d+)*)\]", text):
        for part in re.split(r"\s*,\s*", grp):
            a = re.split(r"\s*[–-]\s*", part)
            if len(a) == 2 and a[0].isdigit() and a[1].isdigit() and 0 < int(a[1]) - int(a[0]) < 20:
                counts.update(range(int(a[0]), int(a[1]) + 1))
            elif part.strip().isdigit():
                counts[int(part)] += 1
    return counts


def fix_case(text, body):
    """Bibliographies often lower-case names ("Pptagent"); use the spelling the paper's
    own text uses for each word ("PPTAgent") when it has one."""
    out = []
    for w in text.split():
        core = re.sub(r"\W+$", "", w)
        forms = [f for f in re.findall(rf"(?<![\w-]){re.escape(core)}(?![\w-])", body, re.I) if not f.islower()] \
            if len(core) > 3 else []
        out.append(w.replace(core, Counter(forms).most_common(1)[0][0]) if forms else w)
    return " ".join(out)


def short_ref(entry, body=""):
    """'PPTAgent (Zheng and colleagues, 2025)' from a reference entry."""
    parts = [p.strip() for p in re.split(r"(?<=[a-z\)])\.\s+", entry) if p.strip()]
    authors, title = (parts[0], parts[1]) if len(parts) > 1 else (entry, "")
    names = [n for n in re.split(r",\s*(?:and\s+)?|\s+and\s+", authors) if n.strip()]
    first = names[0].split() if names else []
    surname = first[-1] if first else ""
    year = (re.findall(r"\b(?:19|20)\d\d\b", entry) or [""])[-1]
    title = title.split(":")[0] if ":" in title and len(title.split(":")[0].split()) <= 6 else title
    title = fix_case(" ".join(title.split()[:9]), body) if body else " ".join(title.split()[:9])
    who = (f"{surname} and colleagues" if len(names) > 1 else surname) + (f", {year}" if year else "")
    return f"{title} ({who})" if title and surname else title or who


def reference_sentences(paper_text, within=None, k=3, lead="it cites most"):
    refs, body = references(paper_text)
    if not refs:
        return []
    counts = cited_counts(within if within is not None else body)
    top = [n for n, _ in counts.most_common() if n in refs][:k]
    out = [dict(text=f"The reference list has {word(len(refs))} entries.",
                anchor=dict(text="References", heading=True))] if within is None else []
    for i, n in enumerate(top):
        name = short_ref(refs[n], body)
        text = (f"The work {lead} is {name}." if i == 0 else f"Next comes {name}." if i == 1
                else f"Then {name}.")
        out.append(dict(text=text, anchor=dict(text=f"[{n}]", reference=True)))
    if top and within is None:
        out.append(dict(text="If you already know these, you know the paper's closest neighbours.", hold=True))
    return out


# ── figures ───────────────────────────────────────────────────────────────────
FORMAT_NOTE = re.compile(r"\b(bold|underline|italic|best and (the )?second|indicates the best|NA means|"
                         r"not applicable|zoom in|best viewed|in color)\b", re.I)


def caption_sentences(f, k=2, max_words=34):
    """The caption's first sentences, as the authors wrote them, without its label and
    without typography notes ("Bold indicates the best")."""
    cap = re.sub(r"\s+", " ", f.get("caption", ""))
    rest = re.sub(r"^(Figure|Fig\.|Table)\s*\d+\s*[:.]\s*", "", cap)
    out = []
    for sent in re.split(r"(?<=[a-z0-9\)\]][.?!])\s+(?=[A-Z(])", rest):
        sent = sent.strip()
        if len(out) == k or not sent:
            break
        if FORMAT_NOTE.search(sent) or len(sent.split()) > max_words:
            if out:
                break
            continue
        out.append(sent if sent.endswith((".", "?", "!")) else sent + ".")
    return out


def caption_figures(w, figures):
    """Pass 1 reads the figures the way Keshav says: each figure with its caption. The
    narration is the caption itself (its first sentences), so nothing is paraphrased."""
    out = []
    for f in sorted(figures, key=lambda f: (f["page"], f["box"][1])):
        sents = caption_sentences(f)
        if not sents:
            continue
        out.append(dict(text=f"{f['label']}: {sents[0]}", figure=f["label"]))
        out += [dict(text=x, figure=f["label"]) for x in sents[1:]]
    return out


def figure_sentences(w, figures, notes, n_main=2, n_appendix=1, extra_goal="", only=None):
    """One or two sentences per figure/table from the section notes, each kept on that figure."""
    out = []
    for f in sorted(figures, key=lambda f: (f["page"], f["box"][1])):
        lab = f["label"]
        if only is not None and lab not in only:
            continue
        refs = [n for n in notes if any(fig_label(str(r)) == lab for r in n.get("figures") or [])]
        appendix = all(n.get("appendix") for n in refs) if refs else f["page"] > 12
        n = n_appendix if appendix else n_main
        cap = re.sub(r"\s+", " ", f.get("caption", ""))
        w.tick(f"writing: {lab}")
        got = w.ask(lab, f"tell the viewer what {lab} shows and what to notice in it{extra_goal}", n, refs,
                    extra=(f"- Write about {lab} only. The first sentence must start with \"{lab} shows\".\n"
                           f"- Use only its caption and the facts; do not mention any other figure or table."),
                    figures=f"- {lab}: {cap[:300]}")
        got = [x for x in got if fig_label(x) in (None, lab)]
        if not got or fig_label(got[0]) != lab:          # the model didn't name it: read the caption instead
            rest = re.sub(r"^(Figure|Fig\.|Table)\s*\d+\s*[:.]\s*", "", cap).split(". ")[0].rstrip(".")
            got = [f"{lab} shows {rest[0].lower() + rest[1:] if rest else 'this part of the work'}."] + got[1:]
        out += [dict(text=x, figure=lab) for x in got]
    return out


# ── the three passes ──────────────────────────────────────────────────────────
def pass1(meta, notes, figures, doc, paper_text, w):
    front = [n for n in notes if n["title"].startswith("0 ")] or notes[:1]
    intro = [n for n in notes if re.search(r"^\S+\s+introduction", n["title"], re.I)] or notes[1:2]
    concl = [n for n in notes if CONCL.search(n["title"]) and not n.get("appendix")] or notes[-1:]
    chs = [chapter("paper", "Pass 1", intro_chapter(meta, "a first pass") + [dict(hold=True, text=
        "In about ten minutes we'll read the abstract and introduction, see how the paper is organised, read "
        "the conclusion, go through every figure and table, and glance at the references.")])]
    w.tick("writing: the abstract")
    chs.append(chapter("abstract", "Abstract", w.ask(
        "Abstract", "explain what the abstract says in plain words: the problem, what the authors built, "
        "and their main results, as the authors state them", 6, front)))
    w.tick("writing: the introduction")
    chs.append(chapter("s1", "Introduction", w.ask(
        "Introduction", "explain the problem the paper tackles, why it matters, and the contributions the "
        "authors claim", 6, intro)))
    chs.append(chapter("structure", "How the paper is organised", structure_sentences(doc)))
    w.tick("writing: the conclusion")
    chs.append(chapter("conclusion", "Conclusion", w.ask(
        "Conclusion", "say what the authors conclude, in their own terms", 3, concl)))
    chs.append(chapter("figures", "Figures and tables", caption_figures(w, figures)))
    chs.append(chapter("references", "References", reference_sentences(paper_text)))
    w.tick("writing: pass-one answers")
    chs.append(chapter("verdict", "After pass 1", w.ask(
        "After pass 1", "answer, in this order, one sentence each: what problem the paper tackles; what the "
        "authors did; what they found, exactly as the authors state it; and who should read it further",
        4, front + concl, summary=True,
        extra="- Keep every number attached to the comparison the FACTS give it; never add a comparison.")))
    return chs


def pass2(meta, notes, figures, doc, paper_text, w):
    body = [n for n in notes if not n.get("appendix") and not SKIP.search(n["title"] + " " + n.get("parent", ""))]
    setup = [n for n in body if SETUP.search(n["title"]) and not RESULT.search(n["title"].split(" ", 1)[-1][:3])]
    results = [n for n in body if RESULT.search(n["title"]) or RESULT.search(n.get("parent", ""))]
    limits = [n for n in notes if is_limitation_section(n["title"]) or n["limitations"]]
    chs = [chapter("paper", "Pass 2", intro_chapter(meta, "a second pass") + [
        "This time we look at the evidence: how the experiments were set up, what each result compares, the "
        "limitations, and which references to follow up."])]
    w.tick("writing: the setup")
    words_ = sum(n.get("words", 300) for n in setup)
    chs.append(chapter("setup", "Experimental setup", w.ask(
        "Experimental setup", "explain how the evaluation works: the data, the baselines, the metrics and the "
        "settings, so that the results can be read correctly", max(4, min(12, round(words_ / 60))), setup or body)))
    labels = {fig_label(str(r)) for n in results for r in n.get("figures") or []} - {None}
    chs.append(chapter("results", "Results", figure_sentences(
        w, figures, results or body, n_main=3, n_appendix=1, only=labels or None,
        extra_goal="; say first what is compared (which methods, on which data, with which metric), "
                   "then the numbers")))
    w.tick("writing: limitations")
    chs.append(chapter("limits", "Limitations", w.ask(
        "Limitations", "state the limitations and threats to validity the authors report; add none of your own",
        5, limits or body[-2:])))
    within = "\n".join(sc["text"] for sc in doc.get("sections", []) if SETUP.search(sc["title"]) or
                       RESULT.search(sc["title"]))
    chs.append(chapter("followup", "References to follow up", reference_sentences(
        paper_text, within=within, lead="the evaluation sections cite most")))
    w.tick("writing: what the evidence supports")
    chs.append(chapter("verdict", "After pass 2", w.ask(
        "After pass 2", "say what the evidence supports and what it does not, as the authors' own results and "
        "limitations show", 4, results + limits, summary=True)))
    return chs


def pass3(meta, notes, figures, doc, paper_text, w):
    body = [n for n in notes if not SKIP.search(n["title"] + " " + n.get("parent", ""))]
    method = [n for n in body if not n.get("appendix") and not NON_METHOD.search(n["title"] + " " + n.get("parent", ""))]
    appendix = [n for n in body if n.get("appendix")]
    limits = [n for n in notes if is_limitation_section(n["title"]) or n["limitations"]]
    chs = [chapter("paper", "Pass 3", intro_chapter(meta, "a third, in-depth pass") + [
        "Now we go through the method step by step, check the assumptions it rests on, and see what it would "
        "take to reproduce the work."])]
    groups = []
    for n in method:
        base = re.sub(r"\s*\(part \d+\)$", "", n["title"])
        if groups and groups[-1][0] == base:
            groups[-1][1].append(n)
        else:
            groups.append((base, [n]))
    for base, ns in groups:
        title = re.sub(r"^\S+\s+", "", base)
        w.tick(f"writing: {title}")
        words_ = sum(n.get("words", 300) for n in ns)
        labels = [lab for lab in {fig_label(str(r)) for n in ns for r in n.get("figures") or []} if lab]
        caps = "\n".join(f"- {f['label']}: {f.get('caption', '')[:300]}" for f in figures if f["label"] in labels)
        chs.append(chapter(f"s{base.split()[0]}", title, w.ask(
            title, f"explain '{title}' step by step in full detail: inputs, each step, outputs, and any equation "
            "in words", max(4, min(18, round(words_ / 45))), ns,
            extra="- Name each figure or table listed under FIGURES when you explain it." if caps else "",
            figures=caps or "(none)")))
    app_groups = []
    for n in appendix:                                # parts of one long appendix section stay together
        base = re.sub(r"\s*\(part \d+\)$", "", n["title"])
        if app_groups and app_groups[-1][0] == base:
            app_groups[-1][1].append(n)
        else:
            app_groups.append((base, [n]))
    for base, ns in app_groups:
        title = re.sub(r"^\S+\s+", "", base)
        w.tick(f"writing: appendix {title}")
        words_ = sum(n.get("words", 300) for n in ns)
        chs.append(chapter(f"s{base.split()[0]}", f"Appendix: {title}", w.ask(
            title, f"explain what the appendix section '{title}' adds", max(1, min(6, round(words_ / 120))), ns)))
    w.tick("writing: assumptions")
    chs.append(chapter("assumptions", "Assumptions", w.ask(
        "Assumptions", "list the assumptions the method rests on, as the authors state them or as their method "
        "requires; say 'the method assumes' for each", 5, method + limits)))
    w.tick("writing: reproducing it")
    chs.append(chapter("reproduce", "Reproducing it", w.ask(
        "Reproducing it", "say what someone would need to reproduce the work: data, code, models and compute, "
        "only as stated in the facts", 4, body)))
    return chs


PASSES = {"pass1": pass1, "pass2": pass2, "pass3": pass3}


def write(meta, notes, model, progress, depth, figures, doc, paper_text):
    w = Writer(meta, model, progress, steps=len(figures) + 12)
    chapters = [c for c in PASSES[depth](meta, notes, figures, doc, paper_text, w) if c]
    chapters.append(dict(key="outro", title="Source", sentences=[dict(
        text="The paper is linked in the description. Thanks for watching.", fixed=True)]))
    n = sum(len(c["sentences"]) for c in chapters)
    progress(1, 1, f"{n} sentences ({depth})")
    return dict(meta=meta, chapters=chapters, source="ollama", model=model, depth=depth)
