---
name: explain_sentences
purpose: Explain each sentence of a paper, in order, for a whole-paper explainer video.
---
You are narrating a video that goes through a research paper sentence by sentence, explaining
each one to a final-year undergraduate.
Paper: {{paper}}
Section: "{{section}}"
Just before these sentences, the paper said: {{before}}

Explain each of the {{n}} numbered sentences below, in the same order. Return JSON:
{"explanations": ["...", "..."]} with exactly {{n}} items, one per sentence.

Rules:
- Each explanation is one or two short sentences in plain, spoken English (at most 40 words):
  say what the sentence means and, where it helps, why it matters. Explain any technical term.
- Keep every number exactly as the sentence writes it. Add no facts, numbers or opinions that
  the sentence does not contain.
- Do not start with "This sentence" or "The authors say". Do not repeat the same opening twice.
- Citations like [12] or (Chen et al., 2021) can be left out.
- A "Figure caption" explains what that figure or table shows.

SENTENCES:
{{sentences}}
