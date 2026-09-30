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


def run(script, paper_text, notes, model, progress, tries=2):
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
