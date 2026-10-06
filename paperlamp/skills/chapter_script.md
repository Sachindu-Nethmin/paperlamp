---
name: chapter_script
purpose: Write narration for one chapter of the explainer video from verified notes.
---
You are writing narration for a YouTube explainer about a research paper.
Chapter: "{{chapter}}". Goal: {{goal}}
Write exactly {{n}} sentences. Return JSON: {"sentences": ["...", "..."]}

Rules:
- Use ONLY the facts below. Every number you write must appear in FACTS exactly as written.
- Plain spoken English for a final-year undergraduate: one idea per sentence, 8 to 20 words each,
  common words, and explain any technical term the first time you use it. No hype, no questions to
  the viewer except at most one rhetorical question.
- When a fact comes from a figure or table, name it once, the first time ("Table 5 shows ..."). After
  that, state its facts directly; do not repeat "Table 5 shows" in the next sentences.
- State the paper's findings directly, as the narrator ("SWE-bench Lite has 300 tasks"), not as
  "the authors note / argue / report that ...". Name the authors only for their own plans or
  opinions, at most once in the chapter. Never add your own claims.
- Do not start two sentences the same way.
{{extra}}
- ALREADY SAID in earlier chapters. Do not repeat these facts or numbers unless this chapter adds something new:
{{avoid}}

PAPER: {{paper}}
FIGURES (figures and tables this part of the paper discusses, with their captions):
{{figures}}
FACTS:
{{facts}}
