---
name: background_concepts
purpose: Explain the background concepts a paper assumes, before an in-depth video of it.
---
A long video is about to go through the research paper "{{paper}}" sentence by sentence.
Abstract: {{abstract}}
Terms the paper uses: {{terms}}
Equations in the paper (LaTeX): {{equations}}

Pick the background concepts a final-year undergraduate in computing needs before watching, which
the paper assumes rather than explains (for example a statistical model, a metric, a kind of
benchmark or a training method). Return JSON:
{"concepts": [{"name": "the concept", "sentences": ["three to five sentences of plain spoken English that teach it from scratch"]}]}

Rules:
- At most {{n}} concepts, in the order a student should learn them.
- Teach the general idea and why it matters for this kind of paper. Do not describe this paper's
  results. Use no numbers at all, and no LaTeX or symbols: say formulas in words.
