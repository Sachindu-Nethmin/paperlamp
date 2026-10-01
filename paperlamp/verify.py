"""Stage: every number in the script must occur in the paper.

A sentence whose numbers can't all be found in the paper text is sent back to
the model once or twice with the allowed facts; if it still fails, it is
dropped. Labels such as "Figure 3", "Table 5", "Section 4.2" or "RQ1" are
references, not claims, and are skipped.
"""
import re

from . import llm
from .analyze import facts_text

NUM = re.compile(r"(?<![\w.])(\d(?:[\d,]*\d)?(?:\.\d+)?)(\s?%|\s?percent|k\b|K\b)?")
LABEL = re.compile(r"(Figure|Fig\.|Table|Section|Sec\.|§|Appendix|RQ|Eq\.|Equation|Chapter)\s*$", re.I)


def norm(v):
    v = v.replace(",", "").strip()
    if "." in v:
        v = v.rstrip("0").rstrip(".") or "0"
    return v


def paper_numbers(text):
    out = set()
    for m in NUM.finditer(text):
        out.add(norm(m.group(1)))
        if m.group(2) and m.group(2).strip().lower() == "k":
            out.add(norm(str(float(m.group(1).replace(",", "")) * 1000)))
    return out


def numbers_in(sentence):
    found = []
    for m in NUM.finditer(sentence):
        if LABEL.search(sentence[:m.start()]):
            continue
        found.append(m.group(1))
    return found


def check(sentence, known):
    return [n for n in numbers_in(sentence) if norm(n) not in known]


REF = re.compile(r"\b(Table|Figure|Fig\.)\s+(\d+)\b")
REF_PHRASE = r"(?:As\s+)?(?:shown\s+in\s+)?{label}\s*(?:shows|reports|lists|indicates|presents|gives|summarizes|reveals)?\s*(?:that)?,?\s*"


def check_labels(text, figures, index):
    """A sentence may only cite 'Table N'/'Figure N' if that exists in the paper and
    one of the sentence's numbers is printed on that figure's page. Otherwise the
    citation is removed and the facts are kept."""
    pages = {}
    for f in figures:
        pages[f["label"].lower()] = f["page"]
    for m in list(REF.finditer(text)):
        label = f"{'table' if m.group(1).lower().startswith('tab') else 'figure'} {m.group(2)}"
        nums = {norm(n) for n in numbers_in(text)}
        ok = label in pages and (not nums or any(
            norm(n) in nums for ln in index if ln["page"] == pages[label] for n in numbers_in(ln["text"])))
        if not ok:
            pat = REF_PHRASE.format(label=re.escape(m.group(0)))
            new = re.sub(pat, "", text, count=1, flags=re.I).strip()
            if new and new != text:
                text = new[0].upper() + new[1:]
    return text


def run(script, paper_text, notes, model, progress, tries=2, figures=(), index=()):
    known = paper_numbers(paper_text)
    facts = facts_text(notes, max_words=1200)
    all_s = [(ci, si) for ci, ch in enumerate(script["chapters"]) for si, _ in enumerate(ch["sentences"])]
    stats = dict(total=len(all_s), ok=0, fixed=0, dropped=0, numbers=0, unverified_before=0)
    for k, (ci, si) in enumerate(all_s):
        progress(k, len(all_s), "checking numbers")
        s = script["chapters"][ci]["sentences"][si]
        bad = check(s["text"], known)
        stats["numbers"] += len(numbers_in(s["text"]))
        stats["unverified_before"] += len(bad)
        s["original"] = s["text"]
        relabelled = check_labels(s["text"], figures, index) if figures or index else s["text"]
        if relabelled != s["text"]:
            s["text"], s["label_fixed"] = relabelled, True
            stats["labels_fixed"] = stats.get("labels_fixed", 0) + 1
        if not bad:
            s["status"] = "ok"; stats["ok"] += 1
            continue
        fixed = None
        for _ in range(tries):
            try:
                cand = llm.chat(llm.skill("fix_sentence", bad=", ".join(bad), sentence=s["text"], facts=facts),
                                model=model, max_tokens=200).get("sentence", "")
            except RuntimeError:
                cand = ""
            if cand and len(cand.split()) >= 4 and not check(cand, known):
                fixed = cand
                break
        if fixed:
            s.update(text=fixed, status="fixed", unverified=bad); stats["fixed"] += 1
        else:
            s.update(status="dropped", unverified=bad); stats["dropped"] += 1
    for ch in script["chapters"]:
        ch["sentences"] = [s for s in ch["sentences"] if s.get("status") != "dropped"]
    progress(len(all_s), len(all_s), f"{stats['ok']} ok · {stats['fixed']} fixed · {stats['dropped']} dropped")
    script["verification"] = stats
    return script
