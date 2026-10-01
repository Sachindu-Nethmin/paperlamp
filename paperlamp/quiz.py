"""Stage: comprehension questions, checked against the paper.

Paper2Video (Zhu et al., 2025) scores generated videos with "PresentQuiz": questions
written from the paper, answered by an AI model standing in for the audience. Here the
same kind of quiz is written for people. It is used twice:

  * the video ends with a short "Check yourself" chapter (retrieval practice), and
  * the full set is saved as out/quiz.md, so a teacher or a user study can compare
    how much students understood after the abstract versus after the video.

Every question goes through the same number check as the narration, and the model is
asked the question again (from the facts, without the answer); questions that fail
either check, or are malformed, are dropped rather than repaired.
"""
import random, re, zlib

from . import llm, verify
from .analyze import facts_text
from .script import SKIP, paper_line

LETTERS = "ABCD"


def normalise(q, known):
    """The question in canonical form, or (None, reason) if it can't be used."""
    if not isinstance(q, dict):
        return None, "not an object"
    text = str(q.get("question") or "").strip()
    opts = q.get("options")
    if not text:
        return None, "no question"
    if not (isinstance(opts, list) and len(opts) == 4 and all(isinstance(o, str) and o.strip() for o in opts)):
        return None, "needs exactly 4 options"
    opts = [re.sub(r"^\(?[A-Da-d][).:]\s+", "", o.strip()) for o in opts]     # models sometimes add "A) "
    if len({o.lower() for o in opts}) < 4:
        return None, "duplicate options"
    a = q.get("answer")
    if isinstance(a, str) and len(a.strip()) == 1 and a.strip().upper() in LETTERS:
        a = LETTERS.index(a.strip().upper())
    if isinstance(a, str) and a.strip().isdigit():
        a = int(a.strip())
    if not isinstance(a, int) or isinstance(a, bool) or not 0 <= a < 4:
        return None, "no valid answer index"
    if re.search(r"\b(all|none) of the above\b", " ".join(opts), re.I):
        return None, "all/none of the above"
    why = str(q.get("explanation") or "").strip()
    bad = verify.check(f"{text} {opts[a]} {why}", known)
    if bad:
        return None, "number not in the paper: " + ", ".join(bad)
    kind = "detail" if str(q.get("kind", "")).lower().startswith("detail") else "understanding"
    return dict(question=text, options=opts, answer=a, kind=kind, explanation=why), None


def shuffled(q):
    """Options in a fixed random order per question, so the right answer isn't always A
    (small models tend to put it first)."""
    order = list(range(4))
    random.Random(zlib.crc32(q["question"].encode())).shuffle(order)
    return dict(q, options=[q["options"][i] for i in order], answer=order.index(q["answer"]))


def confirmed(q, facts, model):
    """Ask the model the question again, from the facts alone and without the answer.
    A question whose stated answer it can't reproduce is probably wrong or ambiguous."""
    opts = "\n".join(f"{LETTERS[k]}. {o}" for k, o in enumerate(q["options"]))
    try:
        r = llm.chat(llm.skill("quiz_answer", question=q["question"], options=opts, facts=facts), model=model,
                     max_tokens=40)
    except RuntimeError:
        return False
    pick = str(r.get("answer", "")).strip().upper()[:1]
    return pick in LETTERS and LETTERS.index(pick) == q["answer"]


def make(meta, notes, script_sentences, paper_text, model, progress, n=8, per_call=3, parts=4):
    """Questions from each part of the paper in turn (small batches come back well-formed
    more often, and cover the whole paper), checked for numbers, shuffled, then confirmed
    by asking the model to answer them again. Keeps up to n, mixing detail and
    understanding questions."""
    known = verify.paper_numbers(paper_text)
    body = [x for x in notes if not x.get("appendix") and not SKIP.search(x["title"] + " " + x.get("parent", ""))]
    size = max(1, -(-len(body) // parts))
    groups = [body[i:i + size] for i in range(0, len(body), size)] or [None]
    steps = len(groups) + 1
    cands, rejected, seen = [], [], set()
    for gi, g in enumerate(groups):
        progress(gi, steps, f"writing questions ({gi + 1}/{len(groups)})")
        facts = facts_text(g, max_words=1400) if g else "\n".join(f"- {x}" for x in script_sentences)
        try:
            res = llm.chat(llm.skill("quiz", n=str(per_call), paper=paper_line(meta), facts=facts), model=model,
                           max_tokens=300 * per_call,
                           on_token=lambda k, gi=gi: progress(gi + min(k / (180 * per_call), 0.95), steps,
                                                              f"writing questions ({gi + 1}/{len(groups)})"))
        except RuntimeError as e:
            rejected.append(dict(question=None, why=f"model error: {e}"))
            continue
        for q in res.get("questions") or []:
            c, why = normalise(q, known)
            key = re.sub(r"\W+", " ", c["question"].lower()).strip() if c else None
            if c and key in seen:
                c, why = None, "duplicate question"
            if c:
                seen.add(key); cands.append((shuffled(c), facts))
            else:
                rejected.append(dict(question=q.get("question") if isinstance(q, dict) else str(q), why=why))
    good = []
    for k, (c, facts) in enumerate(cands):
        progress(len(groups) + k / max(len(cands), 1), steps, "double-checking answers")
        if confirmed(c, facts, model):
            good.append(c)
        else:
            rejected.append(dict(question=c["question"], why="answer not reproduced when the question was re-asked"))
    # keep a mix: alternate detail / understanding while both are available
    det = [q for q in good if q["kind"] == "detail"]
    und = [q for q in good if q["kind"] != "detail"]
    mixed = []
    while (det or und) and len(mixed) < n:
        for pool in (und, det):
            if pool and len(mixed) < n:
                mixed.append(pool.pop(0))
    progress(steps, steps, f"{len(mixed)} questions kept · {len(rejected)} rejected")
    return dict(questions=mixed, rejected=rejected)


def say_question(i, total, q, spoken=False):
    # on screen "A: ..."; spoken "option A, ..." (a lone "A" is often read as the article "uh")
    tag = (lambda k: f"option {LETTERS[k]},") if spoken else (lambda k: f"{LETTERS[k]}:")
    opts = "; ".join(f"{tag(k)} {o.rstrip('.')}" for k, o in enumerate(q["options"][:3]))
    return f"Question {i} of {total}. {q['question']} {opts}; or {tag(3)} {q['options'][3].rstrip('.')}."


def say_answer(q, spoken=False):
    a = q["answer"]
    tag = f"option {LETTERS[a]}," if spoken else f"{LETTERS[a]}:"
    return f"The answer is {tag} {q['options'][a].rstrip('.')}." + (f" {q['explanation']}" if q["explanation"] else "")


def video_chapter(quiz, k=3, think=4.0):
    """The 'Check yourself' chapter: each question is read, the viewer gets a few seconds
    to think (the card stays up in silence), then the answer is revealed."""
    qs = quiz.get("questions", [])[:k]
    if not qs:
        return None
    sents = []
    for i, q in enumerate(qs, 1):
        card = dict(question=q["question"], options=q["options"], answer=q["answer"], index=i, total=len(qs),
                    explanation=q["explanation"])
        sents.append(dict(text=say_question(i, len(qs), q), spoken=say_question(i, len(qs), q, spoken=True),
                          fixed=True, status="ok", pause=think, card=dict(card, reveal=False, think=think)))
        sents.append(dict(text=say_answer(q), spoken=say_answer(q, spoken=True), fixed=True, status="ok",
                          card=dict(card, reveal=True)))
    return dict(key="quiz", title="Check yourself", sentences=sents)


def to_markdown(quiz, meta):
    title = meta.get("title", "")
    md = [f"# Check your understanding", "", f"_{title}_", "",
          "Answer without looking back at the paper or the video. The answer key is at the end.", ""]
    for i, q in enumerate(quiz.get("questions", []), 1):
        md += [f"**{i}. {q['question']}**", ""] + [f"- {LETTERS[k]}. {o}" for k, o in enumerate(q["options"])] + [""]
    md += ["---", "", "## Answer key", ""]
    for i, q in enumerate(quiz.get("questions", []), 1):
        md.append(f"{i}. **{LETTERS[q['answer']]}** ({q['kind']}). {q['explanation']}")
    md += ["", "_Questions were written by a local model from notes on the paper; every number was checked "
           "against the paper text. Review them before using them for assessment._", ""]
    return "\n".join(md)

