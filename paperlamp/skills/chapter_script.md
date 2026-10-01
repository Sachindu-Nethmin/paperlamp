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
- When a fact comes from a figure or table, name it ("Table 5 shows ...").
- Attribute opinions to the authors ("the authors argue ..."). Never add your own claims.
{{extra}}
- ALREADY SAID in earlier chapters. Do not repeat these facts or numbers unless this chapter adds something new:
{{avoid}}

PAPER: {{paper}}
FIGURES (figures and tables this part of the paper discusses, with their captions):
{{figures}}
FACTS:
{{facts}}
