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


ROMAN_OK = re.compile(r"X{0,3}(IX|IV|V?I{0,3})")
# words that are followed by a number, so a Roman numeral after them is a number ("Stage III",
# "Table IV", "Type I error"); plurals and lists ("Stages I and II") count too
ROMAN_AFTER = re.compile(
    r"\b((?i:stage|phase|part|section|chapter|table|figure|fig\.|type|level|step|round|experiment|study|tier|"
    r"class|category|version|volume|vol\.|appendix|article|track|setting|case|scenario|task|method|condition|group|"
    r"option|rule|lemma|theorem|definition|proposition|corollary|assumption|world war|season|book|act)s?)"
    r"(\s+|-)([IVX]{1,6})\b((?:\s*(?:,|and|or|to|through|-|\u2013)\s*[IVX]{1,6}\b)*)")
# on its own, a numeral with "II" in it (II, III, VII, XII ...) is never an acronym
ROMAN_ALONE = re.compile(r"(?<![A-Za-z0-9])[IVX]*II[IVX]*(?![A-Za-z0-9])")
ROMAN_ENUM = re.compile(r"\(([ivx]{1,4})\)")


def roman_value(r):
    """1-39 for a well-formed numeral (I ... XXXIX), else None."""
    r = r.upper()
    if not r or not ROMAN_OK.fullmatch(r):
        return None
    vals = {"I": 1, "V": 5, "X": 10}
    n = sum(-vals[a] if vals[a] < vals[b] else vals[a] for a, b in zip(r, r[1:])) + vals[r[-1]]
    return n or None


def _roman_words(m):
    n = roman_value(m.group(0))
    return int_words(n) if n else m.group(0)


def romans(t):
    """Roman numerals as words, so the voice says "Stage three", not "Stage I I I"."""
    t = ROMAN_AFTER.sub(lambda m: m.group(1) + m.group(2) + _roman_words(re.match(r".+", m.group(3)))
                        + re.sub(r"[IVX]{1,6}", _roman_words, m.group(4)), t)
    t = ROMAN_ALONE.sub(_roman_words, t)
    if re.search(r"\(i\)|\(ii\)", t):                    # an enumeration: (i) ... (ii) ... (iii)
        t = ROMAN_ENUM.sub(lambda m: f"({int_words(roman_value(m.group(1)))})" if roman_value(m.group(1)) else m.group(0), t)
    return t


def _spell(m):
    w = m if isinstance(m, str) else m.group(0)
    return w if w in SAY_AS_WORD else " ".join(w)


def to_spoken(text, terms=()):
    t = text
    for pat, rep in list(terms) + GENERIC:
        t = re.sub(pat, rep, t)
    t = romans(t)                     # before acronyms: "III" is a number, not "I I I"
    # short all-caps acronyms are read letter by letter (LLM -> L L M), plurals too (PRs -> P R s)
    t = re.sub(r"\b[A-Z]{2,4}\b(?![a-z])", _spell, t)
    t = re.sub(r"\b([A-Z]{2,4})s\b", lambda m: _spell(m.group(1)) + "s" if m.group(1) not in SAY_AS_WORD else m.group(0), t)
    t = re.sub(r"\b((?i:about|around|approximately|roughly|nearly))\s*~\s*", r"\1 ", t)
    t = re.sub(r"~\s*(?=\d)", "about ", t)
    # a year, also at the end of a sentence ("ICLR 2024."), but not 2024.5 or 2,024
    t = re.sub(r"(?<![\d.])((?:19|20)\d\d)(?!\d|[,.]\d|%)", lambda m: year_words(int(m.group(1))), t)
    t = re.sub(r"(\d[\d,]*(?:\.\d+)?)\s*%", lambda m: number_words(m.group(1)) + " percent", t)
    t = re.sub(r"\d[\d,]*\.\d+|\d{1,3}(?:,\d{3})+|\d+", lambda m: number_words(m.group()), t)
    t = t.replace("≥", "at least ").replace("×", " times ").replace("→", " to ")
    return re.sub(r"\s+", " ", t).strip()
