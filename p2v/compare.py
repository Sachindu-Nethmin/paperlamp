"""Compare two scripts for the same paper (e.g. local model vs Claude).

    python3 -m p2v.compare paper.pdf A.script.json B.script.json [--names A B] [--out report.md]

All metrics are computed from the paper text itself — no model judges another:
  numbers verified   share of numeric mentions that occur in the paper
  abstract coverage  share of the abstract's numbers the script mentions
  figure refs        distinct 'Figure N' / 'Table N' the script names
  limitation lines   sentences that state limitations / threats / caveats
  readability        words per sentence, Flesch reading ease
  repetition         near-duplicate sentence pairs (word Jaccard ≥ 0.6)
"""
import argparse, json, pathlib, re, subprocess

from .verify import check, norm, numbers_in, paper_numbers

LIMIT = re.compile(r"\b(limitation|limit(ed|s)?|threat|bias|caveat|may not (generali[sz]e|carry)|only (covers|one|"
                   r"python|claude)|does not (show|isolate|measure)|doesn't|cannot|can't|excluded|subset)\b", re.I)
REF = re.compile(r"\b(Figure|Fig\.|Table)\s+(\d+)", re.I)


def sentences(script):
    return [s["text"] for ch in script["chapters"] for s in ch["sentences"]]


def syllables(word):
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups) - (1 if w.endswith("e") and len(groups) > 1 else 0)
    return max(1, n)


def abstract_numbers(text):
    m = re.search(r"A\s?BSTRACT|Abstract", text)
    start = m.end() if m else 0
    end = re.search(r"\n\s*1\s+I\s?NTRODUCTION|\n\s*1\s+Introduction|CCS Concepts|Keywords", text[start:])
    chunk = text[start:start + (end.start() if end else 3000)]
    return {norm(n) for n in numbers_in(chunk) if len(norm(n)) > 1 or "." in n}


def metrics(script, paper_text):
    known = paper_numbers(paper_text)
    ss = sentences(script)
    words = [w for s in ss for w in s.split()]
    nums = [n for s in ss for n in numbers_in(s)]
    bad = [n for s in ss for n in check(s, known)]
    used = {norm(n) for n in nums} - {norm(n) for n in bad}
    absn = abstract_numbers(paper_text)
    refs = {m.group(2) + m.group(1)[0].lower() for s in ss for m in REF.finditer(s)}
    lim = sum(1 for s in ss if LIMIT.search(s))
    sets = [set(re.findall(r"[a-z]{3,}", s.lower())) for s in ss]
    dup = sum(1 for i in range(len(sets)) for j in range(i + 1, len(sets))
              if sets[i] and sets[j] and len(sets[i] & sets[j]) / len(sets[i] | sets[j]) >= 0.6)
    syl = sum(syllables(w) for w in words)
    wps = len(words) / max(len(ss), 1)
    flesch = 206.835 - 1.015 * wps - 84.6 * (syl / max(len(words), 1))
    return dict(sentences=len(ss), words=len(words), minutes=round(len(words) / 150, 1),
                numbers=len(nums), verified=round(1 - len(bad) / max(len(nums), 1), 3), unverified=sorted(set(bad)),
                distinct_numbers=len(used), abstract_coverage=round(len(absn & used) / max(len(absn), 1), 3),
                abstract_numbers=len(absn), figure_refs=len(refs), limitation_sentences=lim,
                words_per_sentence=round(wps, 1), flesch=round(flesch, 1), near_duplicates=dup,
                chapters=len(script["chapters"]))


ROWS = [("Sentences", "sentences"), ("Words (≈ minutes)", None), ("Numbers stated", "numbers"),
        ("Numbers found in the paper", "verified"), ("Distinct verified numbers", "distinct_numbers"),
        ("Abstract numbers covered", "abstract_coverage"), ("Figures/tables referenced", "figure_refs"),
        ("Limitation / caveat sentences", "limitation_sentences"), ("Words per sentence", "words_per_sentence"),
        ("Flesch reading ease", "flesch"), ("Near-duplicate sentence pairs", "near_duplicates")]


def table(results, names):
    out = ["| Metric | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for label, key in ROWS:
        cells = []
        for r in results:
            if key is None:
                cells.append(f"{r['words']:,} (≈ {r['minutes']})")
            elif key in ("verified", "abstract_coverage"):
                cells.append(f"{r[key] * 100:.1f}%")
            else:
                cells.append(f"{r[key]:,}" if isinstance(r[key], int) else str(r[key]))
        out.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf"); ap.add_argument("scripts", nargs="+")
    ap.add_argument("--names", nargs="+"); ap.add_argument("--out")
    a = ap.parse_args()
    text = subprocess.run(["pdftotext", "-layout", a.pdf, "-"], capture_output=True, text=True).stdout
    res = [metrics(json.loads(pathlib.Path(p).read_text()), text) for p in a.scripts]
    names = a.names or [pathlib.Path(p).stem for p in a.scripts]
    md = table(res, names)
    for n, r in zip(names, res):
        if r["unverified"]:
            md += f"\n\nUnverified numbers in {n}: {', '.join(r['unverified'][:20])}"
    print(md)
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(dict(zip(names, res)), indent=1))


if __name__ == "__main__":
    main()
