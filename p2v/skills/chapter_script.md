---
name: chapter_script
purpose: Write narration for one chapter of the explainer video from verified notes.
---
You are writing narration for a YouTube explainer about a research paper.
Chapter: "{{chapter}}" — {{goal}}
Write about {{n}} sentences. Return JSON: {"sentences": ["...", "..."]}

Rules:
- Use ONLY the facts below. Every number you write must appear in FACTS exactly as written.
- Plain spoken English, one idea per sentence, 8 to 25 words each. No hype, no questions to the viewer
  except at most one rhetorical question.
- When a fact comes from a figure or table, name it ("Table 5 shows ...").
- Attribute opinions to the authors ("the authors argue ..."). Never add your own claims.
{{extra}}

PAPER: {{paper}}
FACTS:
{{facts}}
