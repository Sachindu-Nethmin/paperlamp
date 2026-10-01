# Local model vs Claude: script quality

The same paper was scripted twice, and both scripts were scored with metrics computed from the
paper text itself (no model judges another):

- **Paper:** "On the Use of Agentic Coding: An Empirical Study of Pull Requests on GitHub"
  (24 pages, 12,761 words)
- **Local:** written offline by `ornith:9b` through Ollama, with PaperLamp's default pipeline
  (target 3 minutes, before length control was added; it produced about 7 minutes)
- **Claude:** written by Claude for an in-depth video (`examples/claude_tosem.script.json`)

Reproduce with:

```bash
python3 -m paperlamp.compare paper.pdf local.script.json examples/claude_tosem.script.json \
    --names "Local (ornith:9b)" "Claude" --out report.md
```

| Metric | Local (ornith:9b) | Claude |
|---|---|---|
| Sentences | 51 | 169 |
| Words (about minutes) | 1,014 (6.8) | 3,069 (20.5) |
| Numbers stated | 57 | 167 |
| Numbers found in the paper | 100.0% | 95.2% |
| Distinct verified numbers | 33 | 109 |
| Abstract numbers covered | 30.0% | 80.0% |
| Figures and tables referenced | 1 | 10 |
| Limitation or caveat sentences | 4 | 12 |
| Words per sentence | 19.9 | 18.2 |
| Flesch reading ease (higher is easier) | 37.4 | 60.0 |
| Near-duplicate sentence pairs | 2 | 0 |

## What this shows

- **Accuracy:** the local script states no number that is missing from the paper, because the
  pipeline checks every number and repairs or removes failures. Claude's script has 8 numbers that
  are not in the paper text. Most are arithmetic Claude derived (sums and differences of reported
  counts), which a reader cannot check against the page. PaperLamp reports these for imported
  scripts rather than silently removing them.
- **Coverage and depth:** Claude covers far more: 80% of the abstract's numbers against 30%, ten
  figures and tables against one, and three times the limitation sentences. Part of the gap is
  length (Claude was writing a 20-minute video), but per sentence the local script is still thinner.
- **Readability:** Claude's sentences are much easier to follow (Flesch 60.0 against 37.4), and the
  local model repeats itself a little.

## What changed in PaperLamp because of this

- Chapters are now told which figures and tables they should walk through, with the captions
  (the local script named only one).
- The writing prompt asks for 8 to 20 word sentences in plain words, with technical terms explained
  the first time they appear.
- Length control: the model's sentence count per chapter is capped, and the script is trimmed to a
  hard word budget for the target length.
- The "Whole paper" mode gives the local model room to cover every section, closer to the depth
  of the Claude script.

## Recommended workflow

The local model gives a correct first draft with no internet connection or cost. When a stronger
script matters (for publishing), have a frontier model review it (the `paper-script-review` skill),
or write one and render it with `--import`. Visuals, voice and fact-checking stay identical, so only
the writing changes.
