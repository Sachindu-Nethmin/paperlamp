"""The paper's own LaTeX: its display equations in order, and its sentences that hold inline math.

A PDF's text layer turns an equation into loose symbols ("P ( y ij = 1 | θ i , β j ) = σ ( θ i − β j )",
with subscripts on their own lines), which no model can read reliably. The arXiv source has the
exact formula. This module flattens the source into one document (\\input and \\include
followed), expands the authors' simple macros, and lists:

- every display equation (equation, align, gather, multline, eqnarray, \\[ \\], $$ $$, starred or
  not), with its label and whether LaTeX numbers it;
- every sentence that contains inline math ($...$ or \\( \\)), so a PDF sentence can be explained
  from its exact symbols.

Matching these to the PDF is done in deep.py; `signature` gives the letters and symbols both
sides share.
"""
import pathlib, re
from collections import Counter

ENVS = ("equation", "align", "gather", "multline", "eqnarray", "flalign", "alignat", "displaymath", "dmath")
GREEK = {"alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ",
         "eta": "η", "theta": "θ", "vartheta": "θ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν",
         "xi": "ξ", "pi": "π", "rho": "ρ", "varrho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ",
         "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ",
         "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω"}
SYMBOLS = {"sum": "∑", "prod": "∏", "int": "∫", "partial": "∂", "nabla": "∇", "infty": "∞", "in": "∈",
           "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "approx": "≈", "times": "×", "cdot": "·",
           "pm": "±", "mid": "|", "sim": "∼", "propto": "∝", "forall": "∀", "exists": "∃", "rightarrow": "→",
           "to": "→", "leftarrow": "←", "mapsto": "↦", "subset": "⊂", "subseteq": "⊆", "cup": "∪", "cap": "∩",
           "top": "⊤", "ell": "ℓ", "log": "log", "exp": "exp", "max": "max", "min": "min", "arg": "arg",
           "argmax": "argmax", "argmin": "argmin", "mathbb": "", "lVert": "‖", "rVert": "‖", "|": "‖", "Vert": "‖"}


def _read(path):
    try:
        return pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def strip_comments(text):
    """Drop % comments (not \\%), line by line."""
    return "\n".join(re.sub(r"(?<!\\)%.*$", "", ln) for ln in text.splitlines())


def main_file(src):
    """The .tex file that holds \\documentclass and \\begin{document} (the largest, if several)."""
    cands = [p for p in pathlib.Path(src).rglob("*.tex")
             if re.search(r"\\documentclass", _read(p)) and re.search(r"\\begin\{document\}", _read(p))]
    return max(cands, key=lambda p: p.stat().st_size) if cands else None


def flatten(src):
    """The whole document as one string, comments removed, \\input/\\include/\\subfile inlined."""
    src = pathlib.Path(src)
    main = main_file(src)
    if not main:
        return ""
    seen = set()

    def inline(path, depth=0):
        if depth > 12 or path in seen:
            return ""
        seen.add(path)
        text = strip_comments(_read(path))

        def sub(m):
            name = m.group(2).strip()
            for base in (path.parent, src):
                for cand in (base / name, base / f"{name}.tex"):
                    if cand.is_file():
                        return inline(cand.resolve(), depth + 1)
            return ""
        return re.sub(r"\\(input|include|subfile)\s*\{([^}]+)\}", sub, text)
    return inline(main.resolve())


def _group(text, i):
    """The {...} group starting at text[i] == "{": (content, index after it), braces balanced."""
    if i >= len(text) or text[i] != "{":
        return None, i
    depth, j = 0, i
    while j < len(text):
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[i + 1:j], j + 1
        j += 1
    return None, i


def macros(text):
    """{name: (n_args, body)} from \\newcommand, \\renewcommand, \\providecommand, \\def and
    \\DeclareMathOperator. Only what can be expanded safely by substitution."""
    out = {}
    for m in re.finditer(r"\\(?:re)?(?:new|provide)command\*?\s*\{?\\([A-Za-z]+)\}?\s*(?:\[(\d)\])?(?:\[[^\]]*\])?\s*(?=\{)", text):
        body, _ = _group(text, m.end())
        if body is not None:
            out[m.group(1)] = (int(m.group(2) or 0), body)
    for m in re.finditer(r"\\def\\([A-Za-z]+)\s*(?=\{)", text):
        body, _ = _group(text, m.end())
        if body is not None:
            out.setdefault(m.group(1), (0, body))
    for m in re.finditer(r"\\DeclareMathOperator\*?\s*\{\\([A-Za-z]+)\}\s*(?=\{)", text):
        body, _ = _group(text, m.end())
        if body is not None:
            out[m.group(1)] = (0, r"\operatorname{" + body + "}")
    # macros that only change formatting or spacing would expand into noise
    return {k: v for k, v in out.items() if "\\marginpar" not in v[1] and "\\color" not in v[1] and len(v[1]) < 300}


def expand(latex, defs, passes=4):
    """The formula with the authors' macros replaced by their definitions."""
    if not defs:
        return latex
    names = sorted(defs, key=len, reverse=True)
    pat = re.compile(r"\\(" + "|".join(map(re.escape, names)) + r")(?![A-Za-z])")
    for _ in range(passes):
        changed, out, i = False, [], 0
        for m in pat.finditer(latex):
            if m.start() < i:
                continue
            n, body = defs[m.group(1)]
            j, args = m.end(), []
            for _a in range(n):
                while j < len(latex) and latex[j] == " ":
                    j += 1
                if j < len(latex) and latex[j] == "{":
                    g, j = _group(latex, j)
                    args.append(g or "")
                elif j < len(latex):
                    args.append(latex[j]); j += 1
            rep = body
            for k, a in enumerate(args, 1):
                rep = rep.replace(f"#{k}", a)
            out.append(latex[i:m.start()] + rep)
            i, changed = j, True
        latex = "".join(out) + latex[i:]
        if not changed:
            break
    return latex


def body(text):
    """From \\begin{document} to \\end{document}, the bibliography left out."""
    a = text.find("\\begin{document}")
    b = text.find("\\end{document}")
    t = text[a + 16 if a >= 0 else 0: b if b > a else len(text)]
    return re.sub(r"\\begin\{thebibliography\}.*?\\end\{thebibliography\}", " ", t, flags=re.S)


def clean(latex):
    """A formula as a reader needs it: labels, numbering commands and spacing tweaks removed."""
    t = re.sub(r"\\label\s*\{[^}]*\}", "", latex)
    t = re.sub(r"\\(nonumber|notag|displaystyle|textstyle|limits|nolimits)\b", "", t)
    t = re.sub(r"\\(?:[,;:!> ]|quad|qquad)(?![A-Za-z])", " ", t)
    t = re.sub(r"\\(?:big|Big|bigg|Bigg)[lrm]?(?![A-Za-z])", "", t)
    t = re.sub(r"\\(?:left|right)(?![A-Za-z])\.?", "", t)
    t = re.sub(r"\\tag\*?\{[^}]*\}", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip().strip(",.;").strip()


def equations(text, defs=None):
    """Every display equation in document order:
    [{latex, label, numbered, lines, env, appendix, pos}]. `numbered` counts the numbers LaTeX
    prints for it (an align with three numbered rows prints three)."""
    defs = macros(text) if defs is None else defs
    doc = body(text)
    app_at = doc.find("\\appendix")
    found = []
    env = "|".join(ENVS)
    for m in re.finditer(rf"\\begin\{{({env})(\*?)\}}(.*?)\\end\{{\1\2\}}", doc, re.S):
        inner = m.group(3)
        if m.group(1) == "alignat":
            inner = re.sub(r"^\s*\{\d+\}", "", inner)
        rows = [r for r in re.split(r"\\\\(?:\[[^\]]*\])?", inner) if r.strip()]
        starred = m.group(2) == "*" or m.group(1) == "displaymath"
        if m.group(1) in ("align", "eqnarray", "gather", "flalign", "alignat"):
            numbered = 0 if starred else sum(1 for r in rows if not re.search(r"\\(nonumber|notag)\b", r))
        else:
            numbered = 0 if starred or re.search(r"\\(nonumber|notag)\b", inner) else 1
        if re.search(r"\\tag\{", inner):
            numbered = max(numbered, 1)
        found.append((m.start(), m.end(), m.group(1) + m.group(2), inner, numbered, len(rows)))
    for m in re.finditer(r"(?<!\\)\\\[(.*?)\\\]|(?<!\\)\$\$(.*?)\$\$", doc, re.S):
        inner = m.group(1) if m.group(1) is not None else m.group(2)
        found.append((m.start(), m.end(), "display", inner, 0, 1))
    found.sort()
    out, last = [], -1
    for a, b, kind, inner, numbered, rows in found:
        if a < last:                                    # nested inside an earlier one
            continue
        last = b
        lab = re.search(r"\\label\s*\{([^}]*)\}", inner)
        latex = clean(expand(inner, defs))
        if not latex:
            continue
        out.append(dict(latex=latex, label=lab.group(1) if lab else "", numbered=numbered, lines=rows,
                        env=kind, appendix=app_at >= 0 and a > app_at, pos=a))
    n = 0
    for e in out:                                       # the numbers LaTeX would print, in plain order
        e["numbers"] = list(range(n + 1, n + 1 + e["numbered"]))
        n += e["numbered"]
    return out


def plain(latex):
    """A formula as letters and symbols, the way a PDF's text layer shows it ("\\theta_i" -> "θi")."""
    t = re.sub(r"\\(?:text|mathrm|mathit|mathbf|mathsf|mathcal|mathbb|operatorname|boldsymbol|bm|mathtt)\*?\s*\{([^{}]*)\}",
               r"\1", latex)
    t = re.sub(r"\\(?:hat|tilde|bar|vec|dot|ddot|widehat|widetilde|overline|underline)\s*\{([^{}]*)\}", r"\1", t)
    t = re.sub(r"\\(?:hat|tilde|bar|vec|dot)\s*(\\?[A-Za-z])", r"\1", t)
    t = re.sub(r"\\frac", " ", t)
    t = re.sub(r"\\([A-Za-z]+)", lambda m: GREEK.get(m.group(1), SYMBOLS.get(m.group(1), " ")), t)
    return re.sub(r"[{}_^&\\$]", " ", t)


def signature(text):
    """What two renderings of one formula share: its letters, digits and math symbols, counted."""
    return Counter(c for c in text if c.isalnum() or c in "∑∏∫∂∇∞∈≤≥≠≈×·±|∼∝→⊤ℓ‖=+−-<>")


def similarity(pdf_text, latex):
    a, b = signature(pdf_text), signature(plain(latex))
    if not a or not b:
        return 0.0
    common = sum((a & b).values())
    return 2 * common / (sum(a.values()) + sum(b.values()))


INLINE = re.compile(r"(?<!\\)\$[^$]+?(?<!\\)\$|\\\(.+?\\\)", re.S)


def prose(t):
    """LaTeX prose made readable: citations and labels dropped, references kept as "ref",
    formatting commands unwrapped, inline math kept as it is."""
    t = re.sub(r"~?\\(?:cite[a-z]*|nocite)\*?(?:\[[^\]]*\])*\{[^}]*\}", "", t)
    t = re.sub(r"\\label\{[^}]*\}", "", t)
    t = re.sub(r"~?\\(?:eqref|ref|autoref|cref|Cref)\{[^}]*\}", " (ref)", t)
    t = re.sub(r"\\(?:emph|textbf|textit|texttt|textsc|underline|mbox|textrm|textsf)\{([^{}]*)\}", r"\1", t)
    t = re.sub(r"\\(?:footnote)\{[^{}]*\}", "", t)
    t = t.replace("~", " ").replace("``", '"').replace("''", '"')
    return re.sub(r"\s+", " ", t).strip()


def math_sentences(text, defs=None):
    """[{latex, words}] for each prose sentence that contains inline math. Display math,
    figures, tables and listings are removed first, so a sentence is prose around symbols."""
    defs = macros(text) if defs is None else defs
    doc = body(text)
    doc = re.sub(r"\\begin\{(figure|table|algorithm|lstlisting|verbatim|minted|tabular)\*?\}.*?\\end\{\1\*?\}", " ", doc, flags=re.S)
    doc = re.sub(rf"\\begin\{{({'|'.join(ENVS)})\*?\}}.*?\\end\{{\1\*?\}}", " [display equation] ", doc, flags=re.S)
    doc = re.sub(r"(?<!\\)\\\[.*?\\\]|\$\$.*?\$\$", " [display equation] ", doc, flags=re.S)
    out = []
    for para in re.split(r"\n\s*\n", doc):
        if not INLINE.search(para):
            continue
        para = prose(expand(para, defs))
        # split at sentence ends that are outside math
        masked = INLINE.sub(lambda m: "§" * len(m.group(0)), para)
        starts, k = [0], 0
        for m in re.finditer(r"(?<=[a-z0-9)\]§][.?!])\s+(?=[A-Z\\(])", masked):
            starts.append(m.end())
        for a, b in zip(starts, starts[1:] + [len(para)]):
            s = para[a:b].strip()
            if INLINE.search(s) and len(s) > 20:
                out.append(dict(latex=s, words=words(INLINE.sub(" ", s))))
    return out


def words(s):
    return {w.lower() for w in re.findall(r"[A-Za-z]{3,}", s)}


def best_sentence(pdf_sentence, sents, min_share=0.6, min_hits=4):
    """The LaTeX sentence that a PDF sentence was typeset from, or None: most of the PDF
    sentence's words must be in it."""
    q = words(pdf_sentence)
    if len(q) < min_hits:
        return None
    best, score = None, 0.0
    for s in sents:
        hits = len(q & s["words"])
        sc = hits / len(q)
        if hits >= min_hits and sc > score:
            best, score = s, sc
    return best if score >= min_share else None
