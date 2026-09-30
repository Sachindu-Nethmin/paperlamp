"""Turn an on-screen caption into text the voice clone reads reliably.

Captions keep digits exactly as the paper prints them ("2,294", "1.96%");
the TTS gets words ("two thousand two hundred and ninety-four", "one point
nine six percent"), so it cannot misread a figure. Project-specific terms
(model names, jargon) are passed in as an ordered list of (pattern, spoken).
The ASR check (asr_check.py) verifies the result.
"""
import re

ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
        "fourteen fifteen sixteen seventeen eighteen nineteen").split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def int_words(n):
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("" if n % 10 == 0 else "-" + ONES[n % 10])
    if n < 1000:
        rest = n % 100
        return ONES[n // 100] + " hundred" + ("" if not rest else " and " + int_words(rest))
    if n < 1_000_000:
        rest = n % 1000
        head = int_words(n // 1000) + " thousand"
        if not rest:
            return head
        return head + (" and " if rest < 100 else " ") + int_words(rest)
    rest = n % 1_000_000
    head = int_words(n // 1_000_000) + " million"
    return head if not rest else head + " " + int_words(rest)


def year_words(n):
    hi, lo = divmod(n, 100)
    if lo == 0:
        return int_words(hi) + " hundred"
    return int_words(hi) + " " + (("oh " + ONES[lo]) if lo < 10 else int_words(lo))


def number_words(s):
    """'1.96' -> 'one point nine six'; trailing zeros after the point are dropped."""
    s = s.replace(",", "")
    if "." in s:
        whole, frac = s.split(".")
        frac = frac.rstrip("0")
        w = int_words(int(whole or 0))
        return w if not frac else w + " point " + " ".join(ONES[int(c)] for c in frac)
    return int_words(int(s))


GENERIC = [(r"\be\.g\.,?", "for example,"), (r"\bi\.e\.,?", "that is,"), (r"\bvs\.?(?=\s)", "versus"),
           (r"\bet al\.", "and colleagues"), (r"\bFig\.\s*", "Figure "), (r"§\s*", "section "),
           (r"\b(\d+)(st|nd|rd|th)\b", r"\1")]
SAY_AS_WORD = {"NASA", "RAM", "LASER", "RADAR", "SCUBA", "AIDS", "GIF", "JSON", "SQL"}


def _spell(m):
    w = m.group(0)
    return w if w in SAY_AS_WORD else " ".join(w)


def to_spoken(text, terms=()):
    t = text
    for pat, rep in list(terms) + GENERIC:
        t = re.sub(pat, rep, t)
    # short all-caps acronyms are read letter by letter (LLM -> L L M)
    t = re.sub(r"\b[A-Z]{2,4}\b(?![a-z])", _spell, t)
    t = re.sub(r"(?<![\d.])((?:19|20)\d\d)(?![\d,.%])", lambda m: year_words(int(m.group(1))), t)
    t = re.sub(r"(\d[\d,]*(?:\.\d+)?)\s*%", lambda m: number_words(m.group(1)) + " percent", t)
    t = re.sub(r"\d[\d,]*\.\d+|\d{1,3}(?:,\d{3})+|\d+", lambda m: number_words(m.group()), t)
    t = t.replace("≥", "at least ").replace("×", " times ").replace("→", " to ")
    return re.sub(r"\s+", " ", t).strip()
