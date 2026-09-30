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
    if not meta["title"] and doc.get("pdf_title"):
        meta["title"] = doc["pdf_title"]
    progress(1, 1, meta["title"][:80])
    return meta


def section_notes(chunks, model, progress):
    notes = []
    for i, ch in enumerate(chunks):
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
        notes.append(dict(n, title=ch["title"], appendix=ch.get("appendix", False)))
    progress(len(chunks), len(chunks), f"{len(notes)} sections noted")
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
