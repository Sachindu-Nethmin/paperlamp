"""Stage: read the paper with the local model (map step).

Small local models can't hold a whole paper, so each section chunk is turned
into structured notes on its own; later stages only ever see these notes.
"""
import json, re

from . import llm


def paper_meta(doc, model, progress):
    progress(0, 1, "reading the first page")
    meta = llm.chat(llm.skill("paper_meta", text=doc["first_page"][:3500]), model=model, max_tokens=600)
    for k in ("title", "authors", "affiliations", "venue", "year", "one_line"):
        meta.setdefault(k, "" if k not in ("authors", "affiliations") else [])
    meta["authors"] = [n.title() if isinstance(n, str) and n.isupper() else n for n in meta["authors"]]
    meta = check_meta(meta, doc)
    progress(1, 1, meta["title"][:80])
    return meta


def _words(s):
    return re.findall(r"[^\W_]+", str(s).lower())


def check_meta(meta, doc):
    """Wrong titles and author lists are the most visible slips in generated paper videos,
    so the model's reading of page 1 is checked against page 1 itself: the title must be
    (almost) all there, and each author's surname must appear. Anything unsupported is
    replaced by the PDF's own title or dropped, and noted in meta["checks"]."""
    page = set(_words(doc.get("first_page", "")))
    checks = []
    tw = _words(meta.get("title", ""))
    if not tw or sum(w in page for w in tw) / len(tw) < 0.85:
        pdf_title = doc.get("pdf_title") or ""
        pw = _words(pdf_title)
        if pw and sum(w in page for w in pw) / len(pw) >= 0.85:
            checks.append(f"title replaced by the PDF's own title (model gave: {meta.get('title', '')!r})")
            meta["title"] = pdf_title
        elif tw:
            checks.append("title not found on page 1")
    keep, flat = [], "".join(_words(doc.get("first_page", "")))
    for name in meta.get("authors") or []:
        w = _words(name)
        # "ShouB", "Lin*1": marks are often glued to a surname, so a page word that starts
        # with the surname counts, as does the full name run together
        if w and (w[-1] in page or "".join(w) in flat or
                  (len(w[-1]) >= 3 and any(t.startswith(w[-1]) and len(t) <= len(w[-1]) + 2 for t in page))):
            keep.append(name)
        else:
            checks.append(f"author dropped, not on page 1: {name!r}")
    meta["authors"] = keep
    if not meta.get("venue") and re.search(r"\barxiv:\s?\d{4}\.\d{4,5}|\bpreprint\b", doc.get("first_page", ""), re.I):
        meta["venue"] = "arXiv preprint"                   # say so, rather than leave the venue blank
        checks.append("venue set to arXiv preprint (page 1 says so)")
    meta["checks"] = checks
    return meta


def section_notes(chunks, model, progress, known=None):
    """known: notes already taken for this paper (e.g. by another video of it), by
    title; a chunk whose title and length match is reused instead of read again."""
    notes, reused = [], 0
    known = known or {}
    for i, ch in enumerate(chunks):
        old = known.get(ch["title"])
        if old and old.get("words") == len(ch["text"].split()) and not old.get("error"):
            notes.append(old); reused += 1
            continue
        progress(i, len(chunks), f"notes: {ch['title'][:60]}")
        try:
            n = llm.chat(llm.skill("section_notes", title=ch["title"], text=ch["text"]), model=model,
                         max_tokens=1500, on_token=lambda k, i=i: progress(i + min(k / 900, 0.95), len(chunks),
                                                                          f"notes: {ch['title'][:60]}"))
        except RuntimeError as e:
            n = {"summary": "", "error": str(e)}
        for k in ("key_numbers", "claims", "method", "limitations", "figures"):
            v = n.get(k) or []
            n[k] = v if isinstance(v, list) else [v]
        n["key_numbers"] = [x for x in n["key_numbers"] if isinstance(x, dict) and x.get("value")]
        notes.append(dict(n, title=ch["title"], parent=ch.get("parent", ""), appendix=ch.get("appendix", False),
                          words=len(ch["text"].split())))
    progress(len(chunks), len(chunks), f"{len(notes)} sections noted" + (f" ({reused} reused)" if reused else ""))
    return notes


def facts_text(notes, max_words=1800):
    """Flatten notes into the FACTS block a chapter prompt gets."""
    lines = []
    for n in notes:
        lines.append(f"[{n['title']}] {n.get('summary', '')}")
        lines += [f"- {x['value']}: {x.get('meaning', '')}" for x in n["key_numbers"]]
        lines += [f"- {c}" for c in n["claims"] + n["method"] + n["limitations"] if isinstance(c, str)]
    out, count = [], 0
    for ln in lines:
        w = len(ln.split())
        if count + w > max_words:
            break
        out.append(ln); count += w
    return "\n".join(out)


def is_limitation_section(title):
    return bool(re.search(r"limitation|threat|validity|discussion|future work|caveat", title, re.I))
