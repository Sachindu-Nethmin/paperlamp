---
name: paper-script-review
description: Review and improve a script that the local model wrote for a paper2video job — fact-check every sentence against the paper, fix or remove unsupported claims, add missing key findings and limitations — then re-render. Use when the user asks Claude to check, improve or rewrite a generated video script, or to compare its quality.
---

# Review a generated script with Claude

Inputs live in `jobs/<id>/`: `paper.txt` (full text), `notes.json` (per-section facts the local
model extracted), `script.json` (chapters → sentences, each with `status` from the verifier:
`ok`, `fixed`, or dropped), `meta.json`.

## Procedure

1. Read `paper.txt` fully — not only the notes. List the paper's research questions, dataset,
   method, every headline number, and its stated limitations.
2. Go through `script.json` sentence by sentence:
   - Check each claim and number against the paper. Numbers must appear exactly as printed.
   - Rewrite sentences that are vague, repetitive, overclaiming, or that attribute opinions
     to the paper that it doesn't state. Attribute opinions ("the authors argue…").
   - Keep one idea per sentence, 8–25 words, plain spoken English.
3. Add what is missing: the most important findings, the method in enough detail to be
   understood, and every limitation the authors state (in the Limitations chapter).
4. Name figures/tables in sentences that discuss them ("Table 5 shows…") — the document
   camera uses that to frame and outline them.
5. Save `script.json` (keep the `chapters[].title/key/sentences[].text` structure; you may set
   `"source": "Claude review"`). Then re-run from verify:
   `python3 -m p2v.cli --resume <id> --from verify`
6. Report what changed and the verifier's counts (`script.json` → `verification`).

Never invent numbers, even rounded ones; derived arithmetic must be labelled as such in the text.
