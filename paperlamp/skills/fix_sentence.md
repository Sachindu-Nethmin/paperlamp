---
name: fix_sentence
purpose: Repair a narration sentence whose numbers could not be found in the paper.
---
This sentence from a video script contains numbers that do NOT appear in the paper: {{bad}}
Rewrite it so that every number is taken exactly from ALLOWED NUMBERS, or remove the numbers.
Keep the meaning if the allowed facts support it; otherwise make it a general statement.
Return JSON: {"sentence": "..."}

SENTENCE: {{sentence}}
ALLOWED NUMBERS AND FACTS:
{{facts}}
