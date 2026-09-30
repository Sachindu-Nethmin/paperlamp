---
name: compare-scripts
description: Compare the quality of two video scripts for the same paper (e.g. local Ollama model vs Claude) with objective metrics computed from the paper text, and write the result up. Use when the user asks how the local model compares with Claude, or to evaluate a new model or prompt change.
---

# Compare scripts

1. Get both scripts in the tool's format (`script.json` from a job, or an export such as
   `examples/claude_swebench.script.json` made with `bench/export_claude_scripts.py`).
2. Run: `python3 -m p2v.compare paper.pdf A.script.json B.script.json --names A B --out cmp.json`
3. Metrics (all computed against the paper, no model-as-judge): numbers found in the paper,
   distinct verified numbers, abstract-number coverage, figure/table references, limitation
   sentences, words per sentence, Flesch reading ease, near-duplicate sentences.
4. Also read both scripts side by side and note qualitative differences the metrics miss:
   wrong emphasis, missing method detail, misread findings, repetition, generic filler.
5. Write the result into `docs/COMPARISON.md` with the table, the qualitative notes, and time
   and hardware used. Flag clearly which numbers are derived arithmetic or attributed to other
   papers (the verifier reports those as "unverified" by design).
