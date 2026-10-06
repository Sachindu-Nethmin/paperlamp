"""Stage: plan chapters from the paper's sections and write narration per chapter.

The outline is built deterministically from the paper's own sections, so the
local model only ever writes a few sentences at a time from a short list of
facts: the size of task small models do reliably.

Three depths:
  * "summary": a target length in minutes; sections share a sentence budget and the
    script is trimmed to a hard word budget.
  * "auto": the paper's sections in order (no related work or appendix), each given as
    many sentences as its content needs: its key numbers, claims and method steps, and
    a walk through each figure or table. Nothing is padded to a length or trimmed to fit.
  * "full": the whole paper in order, every subsection its own chapter, each sized by
    how much the paper says there; figures and tables are walked through where the
    paper discusses them. Length follows the paper.
  * "study": built on what learning research says works (see outline_study): the big
    picture and key terms first, then short segments that each open with a question to keep
    in mind and close with its answer, a critical look at the limitations, and recall
    questions at the end.
  * "core": the key sections in detail: the methodology, the results and the discussion,
    each section its own chapter sized like "full"; the introduction, related work and
    background only feed a short opening, and the appendix is left out.
"""
import re

from . import llm
from .analyze import facts_text, is_limitation_section

SKIP = re.compile(r"acknowledg|ethic|reproducib|author contribution|funding|impact statement|"
                  r"related work|background and related|references", re.I)
# the whole-paper mode keeps related work (context matters for a full explanation)
SKIP_FULL = re.compile(r"acknowledg|ethic|reproducib|author contribution|funding|impact statement|"
                       r"societal|references", re.I)
# measured on real runs: narration plus the pauses between sentences and chapters comes to
# about 130 words per minute of video, and the model's sentences average about 20 words
WPM, WORDS_PER_SENTENCE = 130, 20
FIG_RE = re.compile(r"\b(Fig(?:ure)?\.?|Table)\s*(\d+)", re.I)


def skip_re(depth):
    return SKIP_FULL if depth == "full" else SKIP


def intro_chapters(meta, notes, n_hook, n_paper):
    """The two opening chapters, in the order a document-camera video wants them: the
    paper itself first (title, every author by name, venue) so the video opens on page 1,
    then the hook, which is what the finding is. Opening on the hook instead dropped the
    viewer into the middle of the paper, and left the title page for half a minute in."""
    return [dict(key="paper", title="The paper", goal="say what the paper is, who wrote it, where it was published, "
                 "and the questions it asks", notes=notes[:2], n=n_paper),
            dict(key="hook", title="Intro", goal="then give the paper's most important finding, stated the way the "
                 "authors state it, and why it matters to the viewer; add no comparisons of your own",
                 notes=[n for n in notes if re.search(r"result|evaluat|experiment|finding|conclusion|abstract|front",
                                                      n["title"], re.I)] or notes[:2], n=n_hook)]


def closing_chapters(notes, limits, n_limits, n_takeaway):
    out = []
    if n_limits:
        out.append(dict(key="limits", title="Limitations", goal="state the limitations and threats to validity "
                        "the authors report; do not add new ones", notes=limits or notes[-2:], n=n_limits))
    out.append(dict(key="takeaway", title="Takeaway", goal="summarize what the paper shows and why it matters, "
                    "without overclaiming", notes=[n for n in notes if re.search(r"conclu|discuss|abstract|front",
                                                                                n["title"], re.I)] or notes[:2],
                    n=n_takeaway))
    return out


def outline(meta, notes, minutes, depth="summary"):
    """Chapters: the paper, the hook, the paper's sections, limitations, takeaway."""
    if depth == "full":
        return outline_full(meta, notes)
    if depth == "auto":
        return outline_auto(meta, notes)
    if depth == "core":
        return outline_core(meta, notes)
    if depth == "study":
        return outline_study(meta, notes)
    body = [n for n in notes if not n["appendix"] and not SKIP.search(n["title"] + " " + n.get("parent", ""))
            and not n["title"].startswith("0 ")]            # front matter feeds the intro chapters instead
    limits = [n for n in notes if is_limitation_section(n["title"]) or n["limitations"]]
    main = [n for n in body if not is_limitation_section(n["title"])]
    # merge consecutive chunks of the same top-level section
    groups = []
    for n in main:
        top = n["title"].split()[0].split(".")[0]
        if groups and groups[-1]["top"] == top:
            groups[-1]["notes"].append(n)
        else:
            groups.append(dict(top=top, title=n.get("parent") or re.sub(r"^\S+\s+", "", n["title"]), notes=[n]))
    # the whole sentence budget scales with the target length; fixed chapters take a
    # share of it and the rest is split across the paper's sections by content
    total = max(12, round(minutes * WPM / WORDS_PER_SENTENCE))
    fixed = {"hook": max(2, round(total * 0.10)), "paper": max(2, round(total * 0.08)),
             "limits": max(2, round(total * 0.10)), "takeaway": max(2, round(total * 0.07))}
    left = max(len(groups), total - sum(fixed.values()))
    words = [sum(len((x.get("summary") or "").split()) + 20 * len(x["key_numbers"]) + 8 * len(x["claims"])
                 for x in g["notes"]) or 1 for g in groups]
    # too many small sections for the budget: keep the richest ones
    if len(groups) > left:
        keep = sorted(range(len(groups)), key=lambda i: -words[i])[:left]
        groups = [g for i, g in enumerate(groups) if i in keep]
        words = [w for i, w in enumerate(words) if i in keep]
    chapters = intro_chapters(meta, notes, fixed["hook"], fixed["paper"])
    for g, w in zip(groups, words):
        n = max(1, min(25, round(left * w / sum(words))))
        title = chapter_title(g["title"])
        chapters.append(dict(key=f"s{g['top']}", title=title, goal=f"explain the section '{title}' clearly, "
                             "step by step, with its key numbers", notes=g["notes"], n=n))
    return chapters + closing_chapters(notes, limits, fixed["limits"], fixed["takeaway"])


def chapter_title(t):
    t = re.sub(r"\s+", " ", re.sub(r"\s*\(part \d+\)$", "", t)).strip()
    t = t.title() if t.isupper() else t
    return t if len(t) <= 60 else t[:57].rsplit(" ", 1)[0] + "..."


def note_words(n):
    """How much the paper says in a section (older notes lack the count: estimate it)."""
    return n.get("words") or (len((n.get("summary") or "").split()) * 6 + 40 * len(n["key_numbers"]))


def group_sections(body):
    """The paper's sections in order. Tiny sections (a two-line overview before its
    subsections) join their neighbour; chunks of one long section stay together up to
    a size a small model can handle in one go."""
    groups = []
    for n in body:
        base, w = re.sub(r"\s*\(part \d+\)$", "", n["title"]), note_words(n)
        g = groups[-1] if groups else None
        if g and g["appendix"] == n["appendix"] and g["words"] + w < 2400 and (
                base in g["bases"] or (g["parent"] == n.get("parent") and g["words"] < 150)):
            g["notes"].append(n); g["words"] += w
            if base not in g["bases"]:
                g["bases"].append(base)
            continue
        groups.append(dict(bases=[base], parent=n.get("parent"), appendix=n["appendix"], notes=[n], words=w))
    return groups


def group_title(g):
    num = g["bases"][0].split()[0]
    names = [re.sub(r"^\S+\s+", "", b) for b in g["bases"]]
    if len(names) > 1 and "." not in num:                     # "PaperTalker Agent: Slide Builder"
        return num, chapter_title(f"{names[0]}: {' & '.join(names[1:])}")
    return num, chapter_title(" & ".join(names))


def outline_full(meta, notes):
    """Every section in the paper's order, related work and the appendix included."""
    body = [n for n in notes if not SKIP_FULL.search(n["title"] + " " + n.get("parent", ""))
            and not n["title"].startswith("0 ")]
    groups = group_sections(body)
    limit_in_body = any(is_limitation_section(b) for g in groups for b in g["bases"])
    limits = [n for n in notes if is_limitation_section(n["title"]) or n["limitations"]]
    chapters = intro_chapters(meta, notes, 4, 4)
    for g in groups:
        num, title = group_title(g)
        if g["appendix"]:
            n, goal = max(1, min(4, round(g["words"] / 160))), (
                f"briefly explain what the appendix section '{title}' adds to the paper")
        else:
            n, goal = max(2, min(16, round(g["words"] / 55))), (
                f"explain the section '{title}' step by step for a student: what it is, how it works, "
                "why it matters, and its key numbers")
        chapters.append(dict(key=f"s{num}", title=title, goal=goal, notes=g["notes"], n=n))
    return chapters + closing_chapters(notes, limits, 0 if limit_in_body else 4, 4)


def content_sentences(notes, lo=2, hi=18):
    """How many sentences a section needs: notes restate each other (a claim is often
    a key number in words), so distinct points count with diminishing returns, and
    each figure or table to walk through adds one."""
    nums = {str(x["value"]) for n in notes for x in n["key_numbers"]}
    points = {x for n in notes for x in n["claims"] + n["method"] if isinstance(x, str)}
    figs = {fig_label(str(r)) for n in notes for r in n.get("figures") or []} - {None}
    units = len(nums) + len(points) / 2
    return max(lo, min(hi, round(1.5 * units ** 0.5) + min(len(figs), 5)))


# the key-sections video keeps the paper's own method, results and discussion sections
NOT_CORE = re.compile(r"introduc|related work|background|preliminar|motivat|conclu|acknowledg|ethic|"
                      r"reproducib|author contribution|funding|impact statement|societal|references", re.I)
RESULTS = re.compile(r"result|evaluat|experiments\b|finding|analys|ablation|performance|comparison|"
                     r"\brq\s*\d|research question|case stud|user stud", re.I)
DISCUSSION = re.compile(r"discuss|implication|threat|validity|limitation|lesson|insight|future work", re.I)
ROLE_GOAL = {
    "Method": "explain the method step by step for a student: what goes in, each step and why it is done, "
              "the design choices, and the key numbers",
    "Results": "explain the results in detail: what was compared, on what data, each key number and what it "
               "means, and what to notice in each figure and table",
    "Discussion": "explain what the results mean: the implications, the limitations and threats to validity, "
                  "and the open questions, as the paper states them",
}


SETUP = re.compile(r"metric|setup|setting|baseline|protocol|dataset|implementation detail", re.I)


def section_role(g, seen_results):
    """Method, Results or Discussion, from the section's own titles (and its parent's). How
    a study is set up ("Evaluation Metrics", "Experimental Setup") is method. A section with
    neither kind of name is method before the first results section, results after it."""
    names = [re.sub(r"^\S+\s+", "", b) for b in g["bases"]]
    parent = g.get("parent") or ""
    if any(DISCUSSION.search(x) for x in names + [parent]):
        return "Discussion"
    if any(RESULTS.search(x) and not SETUP.search(x) for x in names) or (
            RESULTS.search(parent) and not SETUP.search(parent) and not all(SETUP.search(x) for x in names)):
        return "Results"
    return "Results" if seen_results else "Method"


def abstract_chapter(notes, n):
    """The abstract explained in plain words, from the front-matter notes (title page and
    abstract). None when the paper's front matter wasn't read."""
    front = [x for x in notes if x["title"].startswith("0 ")]
    return dict(key="abstract", title="Abstract", notes=front, n=n,
                goal="explain what the abstract says in plain words: the problem, what the authors did, "
                     "and their main results") if front else None


def outline_sections(meta, notes, detail):
    """The paper's own sections in order, the methodology, results and discussion labelled
    as such (see section_role), after the title, authors, main finding and the abstract.
    detail=False (Auto): the introduction and conclusion are explained too, each section
    as long as its content needs. detail=True (Key sections): only the method, results and
    discussion, each given what the whole-paper video would give it, and at least what its
    numbers, claims and figures need. Limitations get their own chapter only when the paper
    has no discussion section of its own."""
    skip = NOT_CORE if detail else SKIP
    body = [n for n in notes if not n["appendix"] and not n["title"].startswith("0 ")
            and not skip.search(n["title"] + " " + n.get("parent", ""))]
    chapters = intro_chapters(meta, notes, 2, 3)
    ab = abstract_chapter(notes, 5 if detail else 4)
    chapters += [ab] if ab else []
    seen_results = has_discussion = False
    for g in group_sections(body):
        num, title = group_title(g)
        if NOT_CORE.search(" ".join(g["bases"]) + " " + (g.get("parent") or "")):   # introduction, conclusion ...
            chapters.append(dict(key=f"s{num}", title=title, notes=g["notes"], n=content_sentences(g["notes"]),
                                 goal=f"explain the section '{title}' clearly for a student, with its key numbers"))
            continue
        role = section_role(g, seen_results)
        seen_results |= role == "Results"
        has_discussion |= role == "Discussion"
        n = (max(content_sentences(g["notes"], lo=3, hi=20), min(18, round(g["words"] / 55))) if detail
             else content_sentences(g["notes"]))
        label = title if title.lower().startswith(role.lower()) else f"{role}: {title}"
        chapters.append(dict(key=f"s{num}", title=chapter_title(label), notes=g["notes"], n=n,
                             goal=f"{ROLE_GOAL[role]} (section '{title}')"))
    limits = [n for n in notes if is_limitation_section(n["title"]) or n["limitations"]]
    n_limits = 0 if has_discussion else len({x for n in limits for x in n["limitations"] if isinstance(x, str)})
    return chapters + closing_chapters(notes, limits, min(6, round(1.5 * n_limits ** 0.5)), 3)


SEGMENT = 10                    # sentences in a study segment: under two minutes of video
FOCUS = 0.7                     # a study segment keeps the key points: 70% of what Auto gives a section


def outline_study(meta, notes):
    """A study video, each part from evidence on how people learn from text and video:
    - a reading goal first (Carey et al. 2020, "Ten simple rules for reading a scientific
      paper"): what the paper is and what it asks, from the abstract;
    - key terms before the content (Mayer's pre-training principle);
    - the method, results and discussion in short segments (segmenting principle), each
      opening with a question to keep in mind and closing with its answer (prequestions:
      Carpenter & Toftness 2017; Pan & Carpenter 2023), and saying why each step or result
      matters (self-explanation and elaborative interrogation, moderate utility in
      Dunlosky et al. 2013);
    - every figure walked through (Carey et al. rule 4) and the limitations read critically
      (rule 6); related work and the appendix left out (Mayer's coherence principle);
    - recall questions at the end, answered after a pause (practice testing, high utility in
      Dunlosky et al. 2013), and the segment questions again in the description, to answer a
      day later (distributed practice)."""
    body = [n for n in notes if not n["appendix"] and not n["title"].startswith("0 ")
            and not NOT_CORE.search(n["title"] + " " + n.get("parent", ""))]
    chapters = intro_chapters(meta, notes, 2, 2)
    ab = abstract_chapter(notes, 3)
    if ab:
        ab["goal"] = ("give the big picture from the abstract: the problem, what the authors did and what they "
                      "found, so the viewer knows what to look for")
        chapters.append(ab)
    terms = [x for x in notes if x["title"].startswith("0 ")] + body[:3]
    if terms:
        chapters.append(dict(key="terms", title="Key terms", notes=terms, n=3,
                             goal="define the three technical terms a student most needs to follow this paper, one "
                                  "sentence each, in the form \"<term> means ...\", using the paper's own meaning"))
    # segments of about two minutes (SEGMENT sentences): neighbouring sections of the same kind
    # are joined, so the viewer meets one question per segment, not one per small subsection
    segs, seen_results, has_discussion = [], False, False
    for g in group_sections(body):
        num, title = group_title(g)
        role = section_role(g, seen_results)
        seen_results |= role == "Results"
        has_discussion |= role == "Discussion"
        n = max(3, round(FOCUS * content_sentences(g["notes"])))
        if segs and segs[-1]["role"] == role and segs[-1]["n"] + n <= SEGMENT:
            segs[-1]["titles"].append(title); segs[-1]["notes"] += g["notes"]; segs[-1]["n"] += n
        else:
            segs.append(dict(role=role, num=num, titles=[title], notes=list(g["notes"]), n=n))
    for sg in segs:
        role, title = sg["role"], " & ".join(sg["titles"])
        goal = ROLE_GOAL[role] + (", and say why each step or result matters" if role != "Discussion" else
                                  "; read them critically: what the evidence does not show, and other explanations "
                                  "the paper itself considers")
        label = title if title.lower().startswith(role.lower()) else f"{role}: {title}"
        chapters.append(dict(key=f"s{sg['num']}", title=chapter_title(label), notes=sg["notes"], prequestion=True,
                             n=min(SEGMENT, sg["n"]), goal=f"{goal} (section '{title}')"))
    limits = [n for n in notes if is_limitation_section(n["title"]) or n["limitations"]]
    n_limits = 0 if has_discussion else len({x for n in limits for x in n["limitations"] if isinstance(x, str)})
    return chapters + closing_chapters(notes, limits, min(5, round(1.5 * n_limits ** 0.5)), 3)


def prequestion(ch, meta, model):
    """(question, answer) for a study segment, or None when the model's answer has a number
    that isn't in the segment's facts or the question isn't one."""
    facts = facts_text(ch["notes"])
    try:
        res = llm.chat(llm.skill("prequestion", paper=paper_line(meta), chapter=ch["title"], facts=facts),
                       model=model, max_tokens=200)
    except RuntimeError:
        return None
    q, a = str(res.get("question") or "").strip(), str(res.get("answer") or "").strip()
    nums = lambda t: {x.replace(",", "") for x in re.findall(r"\d[\d,]*(?:\.\d+)?", t)}
    if not q.endswith("?") or len(q.split()) < 4 or len(a.split()) < 4 or not nums(a) <= nums(facts):
        return None
    return q, a


def outline_core(meta, notes):
    return outline_sections(meta, notes, detail=True)


def outline_auto(meta, notes):
    return outline_sections(meta, notes, detail=False)


def listing(items):
    items = [i for i in items if i]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else ""


def author_sentences(authors, per=6):
    """Every author, read out by name, written here rather than by the model so none is
    dropped or misspelt. Up to seven fit one sentence; longer lists go six to a sentence
    (a long run of names is hard to follow, and the voice model slurs very long
    sentences). Each sentence keeps its names in "authors", so the camera can mark them
    on the title page."""
    names = [a.strip() for a in authors if a and a.strip()]
    if not names:
        return []
    if len(names) <= per + 1:
        return [dict(text=f"It was written by {listing(names)}.", authors=names)]
    chunks = [names[k:k + per] for k in range(0, len(names), per)]
    out = [dict(text=f"It has {len(names)} authors.", authors=[])]
    for k, c in enumerate(chunks):
        lead = "They are " if k == 0 else "And finally, " if k == len(chunks) - 1 else "Then "
        out.append(dict(text=f"{lead}{listing(c)}.", authors=c))
    return out


def names_authors(text, authors):
    """A sentence that lists authors itself (the model's own, often partial, list)."""
    t = re.sub(r"[^a-z]", "", text.lower())
    last = {re.sub(r"[^a-z]", "", a.split()[-1].lower()) for a in authors if a.split()}
    return bool(re.search(r"\b(written|authored)\s+by\b", text, re.I)) or sum(1 for x in last if x and x in t) >= 2


def lead_with_title(rows, title):
    """Put the sentence that names the paper first. The model is asked to mention the
    title but does not always open with it, and the camera frames the first sentence of
    the chapter on the title page, so whatever leads is what the video opens on."""
    words = [w for w in re.findall(r"[A-Za-z0-9]{4,}", title) if w.lower() not in
             ("with", "from", "that", "this", "using", "based", "towards", "into")]
    if not words or not rows:
        return rows
    def score(i):
        t = rows[i]["text"].lower()
        hits = sum(1 for w in words if w.lower() in t)
        named = bool(re.search(r"\b(titled|called|named|paper|benchmark|framework|model|system|study)\b", t))
        return (hits, named and hits > 0)
    best = max(range(len(rows)), key=score)
    return [rows[best]] + rows[:best] + rows[best + 1:] if score(best)[0] else rows


def paper_line(meta):
    authors = meta.get("authors") or []
    who = (authors[0] + (" et al." if len(authors) > 1 else "")) if authors else ""
    return ", ".join(x for x in (meta.get("title", ""), who, meta.get("venue", ""), str(meta.get("year", ""))) if x)


def fig_label(s):
    m = FIG_RE.search(s)
    return f"{'Table' if m.group(1).lower().startswith('tab') else 'Figure'} {m.group(2)}" if m else None


def figures_block(ch, figures, shown):
    """Figures/tables this chapter's notes refer to and that haven't been walked through
    yet, with their captions, so the model can explain what each one shows."""
    caps = {f["label"]: f.get("caption", "") for f in figures}
    labels = []
    for n in ch["notes"]:
        for ref in n.get("figures") or []:
            lab = fig_label(str(ref))
            if lab and lab in caps and lab not in shown and lab not in labels:
                labels.append(lab)
    return labels, "\n".join("- " + lab + ": " + re.sub(r"\s+", " ", caps[lab])[:300] for lab in labels)


def _wordset(s):
    return set(re.findall(r"[a-z0-9]+", s.lower())) - {"the", "a", "an", "of", "and", "to", "in", "is", "are", "for"}


def near_dup(a, b, threshold=0.6):
    """Sentences that share most of their words (word Jaccard, as in compare.py)."""
    x, y = _wordset(a), _wordset(b)
    return bool(x and y) and len(x & y) / len(x | y) >= threshold


def write(meta, notes, minutes, model, progress, depth="summary", figures=()):
    chapters = outline(meta, notes, minutes, depth)
    out, said, every, shown, asked = [], [], [], set(), []
    for i, ch in enumerate(chapters):
        progress(i, len(chapters), f"writing: {ch['title']}")
        extra = ""
        if ch["key"] == "paper":
            extra = (f"- Mention the title \"{meta.get('title', '')}\" and the venue \"{meta.get('venue', '')}\" "
                     "if given. Do not name the authors: their names are read out separately.")
        if ch["key"] == "hook":
            extra = ("- Your first sentence must state a concrete finding with its number, worded the way the FACTS word "
                     "it: keep what the number compares (\"6x faster than X\" only if FACTS say \"than X\").")
        labels, figs = figures_block(ch, figures, shown) if ch["key"].startswith("s") else ([], "")
        if figs:
            extra += ("\n- Walk the viewer through each figure or table listed under FIGURES: name it once "
                      "(\"Figure 4 shows ...\"), say what it shows and what to notice in it.")

        def ask(n, more=""):
            try:
                avoid = "\n".join(f"  * {x}" for x in said[-14:]) or "  (nothing yet)"
                res = llm.chat(llm.skill("chapter_script", chapter=ch["title"], goal=ch["goal"], n=str(n),
                                         paper=paper_line(meta), facts=facts_text(ch["notes"]), extra=extra + more,
                                         avoid=avoid, figures=figs or "(none)"),
                               model=model, max_tokens=150 + 60 * n,
                               on_token=lambda k: progress(i + min(k / (40 * n + 1), 0.95), len(chapters),
                                                           f"writing: {ch['title']}"))
                got = [x.strip() for x in res.get("sentences", []) if isinstance(x, str) and len(x.split()) >= 4]
            except RuntimeError:
                got = []
            return got[:n]                                     # small models overshoot the count they are given

        # small models copy sentences from earlier chapters (or from the ALREADY SAID list):
        # drop near-duplicates of anything said before, and ask once for fresh ones
        sents, dups = [], []
        for x in ask(ch["n"]):
            (dups if any(near_dup(x, y) for y in every + sents) else sents).append(x)
        if dups and len(sents) < ch["n"]:
            more = ("\n- These sentences were already used earlier in the video. Do not reuse them or their facts; "
                    "say something new about THIS part of the paper:\n" + "\n".join(f"  * {x}" for x in dups))
            sents += [x for x in ask(ch["n"] - len(sents), more) if not any(near_dup(x, y) for y in every + sents)]
        shown.update(lab for lab in labels if any(fig_label(x) == lab for x in sents))
        said += [x for x in sents if re.search(r"\d", x)]      # facts with numbers are the ones that repeat
        every += sents
        rows = [dict(text=x) for x in sents]
        if ch.get("prequestion") and rows and (qa := prequestion(ch, meta, model)):
            rows = ([dict(text=f"A question to keep in mind: {qa[0]}", prequestion=qa[0])] + rows
                    + [dict(text=f"So, the answer: {qa[1][:1].lower() + qa[1][1:]}", answer=qa[1])])
            asked.append(dict(question=qa[0], answer=qa[1], chapter=ch["title"]))
        if ch["key"] == "paper":
            rows = [r for r in rows if not names_authors(r["text"], meta.get("authors") or [])]
            rows = lead_with_title(rows, meta.get("title", ""))          # the video opens on the title
            rows = rows[:1] + author_sentences(meta.get("authors") or []) + rows[1:]
        out.append(dict(key=ch["key"], title=ch["title"], sentences=rows,
                        **({"duplicates_dropped": len(dups)} if dups else {})))
    out = [c for c in out if c["sentences"]]                 # a chapter left empty is dropped
    trimmed = fit_length(out, minutes) if depth == "summary" else 0
    out.append(dict(key="outro", title="Source", sentences=[dict(
        text="The paper is linked in the description. Thanks for watching.", fixed=True)]))
    n = sum(len(c["sentences"]) for c in out)
    dropped = sum(c.get("duplicates_dropped", 0) for c in out)
    progress(len(chapters), len(chapters), f"{n} sentences" + (f" ({trimmed} trimmed to fit {minutes:g} min)" if trimmed else "")
             + (f" · about {max(1, round(words(out) / WPM))} min" if depth != "summary" else "")
             + (f" · {dropped} repeats replaced" if dropped else ""))
    return dict(meta=meta, chapters=out, source="ollama", model=model, depth=depth, trimmed=trimmed,
                figures_explained=sorted(shown), **({"study_questions": asked} if asked else {}))


# "Table 1 shows that X" / "In Table 1, X" once a chapter has named Table 1
LEAD_FIG = re.compile(r"^(?:As\s+)?(?:(Figure|Fig\.|Table)\s+(\w+)\s+(?:also\s+)?(?:shows|reports|presents|"
                      r"illustrates|indicates|reveals|demonstrates|confirms|highlights)\s+that\s+|"
                      r"In\s+(Figure|Fig\.|Table)\s+(\w+),\s+)(?=\S)", re.I)
# "The authors argue that X", "The paper notes that X", "They report that X"
LEAD_AUTH = re.compile(r"^(?:The\s+(?:authors|researchers|paper|study)|They)\s+(?:also\s+)?(?:argue|note|report|"
                       r"say|state|claim|find|found|observe|show|suggest|explain|point out|acknowledge|conclude|"
                       r"emphasi[sz]e|highlight|mention|stress|believe|contend|write|reason)s?\s+that\s+(?=\S)", re.I)
AUTH_GAP = 6                     # sentences between two that keep "the authors ... that"


def _drop_lead(text, m):
    rest = text[m.end():]
    return rest[:1].upper() + rest[1:]


def tidy(chapters):
    """Small models open sentence after sentence with "Table 1 shows that ..." and "The
    authors note that ...". Keep the first naming of a figure in each chapter and an
    occasional attribution; elsewhere state the fact directly. A sentence that loses its
    figure's name keeps it in "figure", so the camera still shows that figure."""
    since, changed = AUTH_GAP, 0
    for ch in chapters:
        named = set()
        for s in ch["sentences"]:
            if s.get("fixed") or s.get("anchor"):
                continue
            m = LEAD_FIG.match(s["text"])
            if m:
                kind, num = (m.group(1) or m.group(3)), (m.group(2) or m.group(4))
                label = f"{'Table' if kind.lower().startswith('tab') else 'Figure'} {num}"
                if label in named:
                    s["text"] = _drop_lead(s["text"], m)
                    s.setdefault("figure", label)
                    changed += 1
                named.add(label)
            else:
                named.update(f"{'Table' if k.lower().startswith('tab') else 'Figure'} {n}"
                             for k, n in re.findall(r"\b(Figure|Fig\.|Table)\s+(\d+)", s["text"]))
            m = LEAD_AUTH.match(s["text"])
            if m and since < AUTH_GAP:
                s["text"] = _drop_lead(s["text"], m)
                changed += 1
            elif m:
                since = 0
            since += 1
    return changed


def words(chapters):
    return sum(len(x["text"].split()) for c in chapters for x in c["sentences"])


def fit_length(chapters, minutes, slack=1.08):
    """Hard word budget for the target length (like a per-slide word cap): drop the last
    sentence of the longest section chapter until the script fits. Intro, paper,
    limitations and takeaway chapters are kept whole. Returns how many were dropped."""
    budget, dropped = minutes * WPM * slack, 0
    while words(chapters) > budget:
        body = [c for c in chapters if c["key"].startswith("s") and len(c["sentences"]) > 1]
        if not body:
            break
        max(body, key=lambda c: len(c["sentences"]))["sentences"].pop()
        dropped += 1
    return dropped
