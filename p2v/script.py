"""Stage: plan chapters from the paper's sections and write narration per chapter.

The outline is built deterministically from the paper's own sections, so the
local model only ever writes a few sentences at a time from a short list of
facts — the size of task small models do reliably.
"""
import re

from . import llm
from .analyze import facts_text, is_limitation_section

SKIP = re.compile(r"acknowledg|ethic|reproducib|author contribution|funding|impact statement|"
                  r"related work|background and related|references", re.I)
WPM, WORDS_PER_SENTENCE = 150, 17


def outline(meta, notes, minutes):
    """Chapters: hook, the paper, one per main section group, limitations, takeaway."""
    body = [n for n in notes if not n["appendix"] and not SKIP.search(n["title"])]
    limits = [n for n in notes if is_limitation_section(n["title"]) or n["limitations"]]
    main = [n for n in body if not is_limitation_section(n["title"])]
    # merge consecutive chunks of the same top-level section
    groups = []
    for n in main:
        top = n["title"].split()[0].split(".")[0]
        if groups and groups[-1]["top"] == top:
            groups[-1]["notes"].append(n)
        else:
            groups.append(dict(top=top, title=re.sub(r"^\S+\s+", "", n["title"]), notes=[n]))
    total = max(12, round(minutes * WPM / WORDS_PER_SENTENCE))
    fixed = {"hook": 5, "paper": 4, "limits": max(4, total // 12), "takeaway": 4}
    left = max(len(groups) * 3, total - sum(fixed.values()))
    words = [sum(len((x.get("summary") or "").split()) + 20 * len(x["key_numbers"]) + 8 * len(x["claims"])
                 for x in g["notes"]) or 1 for g in groups]
    chapters = [dict(key="hook", title="Intro", goal="open with the single most surprising finding and why it matters",
                     notes=[n for n in notes if re.search(r"result|evaluat|experiment|finding|conclusion|abstract|front",
                                                          n["title"], re.I)] or notes[:2], n=fixed["hook"]),
                dict(key="paper", title="The paper", goal="say what the paper is, who wrote it, where it was published, "
                     "and the questions it asks", notes=notes[:2], n=fixed["paper"])]
    for g, w in zip(groups, words):
        n = max(3, min(25, round(left * w / sum(words))))
        title = re.sub(r"\s+", " ", g["title"]).strip().title()[:60]
        chapters.append(dict(key=f"s{g['top']}", title=title, goal=f"explain the section '{title}' clearly, "
                             "step by step, with its key numbers", notes=g["notes"], n=n))
    chapters.append(dict(key="limits", title="Limitations", goal="state the limitations and threats to validity "
                         "the authors report; do not add new ones", notes=limits or notes[-2:], n=fixed["limits"]))
    chapters.append(dict(key="takeaway", title="Takeaway", goal="summarize what the paper shows and why it matters, "
                         "without overclaiming", notes=[n for n in notes if re.search(r"conclu|discuss|abstract|front",
                                                                                    n["title"], re.I)] or notes[:2],
                         n=fixed["takeaway"]))
    return chapters


def paper_line(meta):
    authors = meta.get("authors") or []
    who = (authors[0] + (" et al." if len(authors) > 1 else "")) if authors else ""
    return f"{meta.get('title', '')} — {who} {meta.get('venue', '')} {meta.get('year', '')}".strip()


def write(meta, notes, minutes, model, progress):
    chapters = outline(meta, notes, minutes)
    out = []
    for i, ch in enumerate(chapters):
        progress(i, len(chapters), f"writing: {ch['title']}")
        extra = ""
        if ch["key"] == "paper":
            extra = (f"- Mention the title \"{meta.get('title', '')}\", the venue \"{meta.get('venue', '')}\" "
                     f"and the authors {', '.join(meta.get('authors', [])[:6])} if given.")
        if ch["key"] == "hook":
            extra = "- The first sentence must state a concrete finding with its number."
        try:
            res = llm.chat(llm.skill("chapter_script", chapter=ch["title"], goal=ch["goal"], n=str(ch["n"]),
                                     paper=paper_line(meta), facts=facts_text(ch["notes"]), extra=extra),
                           model=model, max_tokens=150 + 60 * ch["n"],
                           on_token=lambda k, i=i: progress(i + min(k / (40 * ch["n"] + 1), 0.95), len(chapters),
                                                            f"writing: {ch['title']}"))
            sents = [s.strip() for s in res.get("sentences", []) if isinstance(s, str) and len(s.split()) >= 4]
        except RuntimeError:
            sents = []
        out.append(dict(key=ch["key"], title=ch["title"], sentences=[dict(text=s) for s in sents]))
    out.append(dict(key="outro", title="Source", sentences=[dict(
        text="The paper is linked in the description. Thanks for watching.", fixed=True)]))
    progress(len(chapters), len(chapters), f"{sum(len(c['sentences']) for c in out)} sentences")
    return dict(meta=meta, chapters=out, source="ollama", model=model)
