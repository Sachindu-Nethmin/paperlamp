"""Stage: plan chapters from the paper's sections and write narration per chapter.

The outline is built deterministically from the paper's own sections, so the
local model only ever writes a few sentences at a time from a short list of
facts: the size of task small models do reliably.

Two depths:
  * "summary": a target length in minutes; sections share a sentence budget and the
    script is trimmed to a hard word budget.
  * "full": the whole paper in order, every subsection its own chapter, each sized by
    how much the paper says there; figures and tables are walked through where the
    paper discusses them. Length follows the paper.
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
    return [dict(key="hook", title="Intro", goal="open with the paper's most important finding, stated the way the "
                 "authors state it, and why it matters to the viewer; add no comparisons of your own",
                 notes=[n for n in notes if re.search(r"result|evaluat|experiment|finding|conclusion|abstract|front",
                                                      n["title"], re.I)] or notes[:2], n=n_hook),
            dict(key="paper", title="The paper", goal="say what the paper is, who wrote it, where it was published, "
                 "and the questions it asks", notes=notes[:2], n=n_paper)]


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
    """Chapters: hook, the paper, the paper's sections, limitations, takeaway."""
    if depth == "full":
        return outline_full(meta, notes)
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


def outline_full(meta, notes):
    """Every section in the paper's order. Tiny sections (a two-line overview before
    its subsections) join their neighbour; chunks of one long section stay together
    up to a size a small model can handle in one go."""
    body = [n for n in notes if not SKIP_FULL.search(n["title"] + " " + n.get("parent", ""))
            and not n["title"].startswith("0 ")]
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
    limit_in_body = any(is_limitation_section(b) for g in groups for b in g["bases"])
    limits = [n for n in notes if is_limitation_section(n["title"]) or n["limitations"]]
    chapters = intro_chapters(meta, notes, 4, 4)
    for i, g in enumerate(groups):
        num = g["bases"][0].split()[0]
        names = [re.sub(r"^\S+\s+", "", b) for b in g["bases"]]
        if len(names) > 1 and "." not in num:                 # "PaperTalker Agent: Slide Builder"
            title = chapter_title(f"{names[0]}: {' & '.join(names[1:])}")
        else:
            title = chapter_title(" & ".join(names))
        if g["appendix"]:
            n, goal = max(1, min(4, round(g["words"] / 160))), (
                f"briefly explain what the appendix section '{title}' adds to the paper")
        else:
            n, goal = max(2, min(16, round(g["words"] / 55))), (
                f"explain the section '{title}' step by step for a student: what it is, how it works, "
                "why it matters, and its key numbers")
        chapters.append(dict(key=f"s{num}", title=title, goal=goal, notes=g["notes"], n=n))
    return chapters + closing_chapters(notes, limits, 0 if limit_in_body else 4, 4)


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
    out, said, every, shown = [], [], [], set()
    for i, ch in enumerate(chapters):
        progress(i, len(chapters), f"writing: {ch['title']}")
        extra = ""
        if ch["key"] == "paper":
            extra = (f"- Mention the title \"{meta.get('title', '')}\", the venue \"{meta.get('venue', '')}\" "
                     f"and the authors {', '.join(meta.get('authors', [])[:6])} if given.")
        if ch["key"] == "hook":
            extra = ("- The first sentence must state a concrete finding with its number, worded the way the FACTS word "
                     "it: keep what the number compares (\"6x faster than X\" only if FACTS say \"than X\").")
        labels, figs = figures_block(ch, figures, shown) if ch["key"].startswith("s") else ([], "")
        if figs:
            extra += ("\n- Walk the viewer through each figure or table listed under FIGURES: name it "
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
        out.append(dict(key=ch["key"], title=ch["title"], sentences=[dict(text=x) for x in sents],
                        **({"duplicates_dropped": len(dups)} if dups else {})))
    out = [c for c in out if c["sentences"]]                 # a chapter left empty is dropped
    trimmed = fit_length(out, minutes) if depth != "full" else 0
    out.append(dict(key="outro", title="Source", sentences=[dict(
        text="The paper is linked in the description. Thanks for watching.", fixed=True)]))
    n = sum(len(c["sentences"]) for c in out)
    dropped = sum(c.get("duplicates_dropped", 0) for c in out)
    progress(len(chapters), len(chapters), f"{n} sentences" + (f" ({trimmed} trimmed to fit {minutes:g} min)" if trimmed else "")
             + (f" · {dropped} repeats replaced" if dropped else ""))
    return dict(meta=meta, chapters=out, source="ollama", model=model, depth=depth, trimmed=trimmed,
                figures_explained=sorted(shown))


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
