---
name: quiz
purpose: Write multiple-choice comprehension questions about a paper from verified notes.
---
You are writing a short comprehension check for students who have just watched an explainer video
about a research paper. Write {{n}} multiple-choice questions. Return JSON:
{"questions": [{"question": "...", "options": ["...", "...", "...", "..."], "answer": 0,
                "kind": "detail or understanding", "explanation": "one sentence saying why, from the facts"}]}

Rules:
- Use ONLY the facts below. The correct option must be stated in FACTS, and every number in the
  question, the correct option and the explanation must appear in FACTS exactly as written.
- About half "detail" questions (a specific number, dataset, method step or result) and half
  "understanding" questions (the main idea, why something was done, what a result means, a limitation).
- Exactly 4 short options (under 12 words each), exactly one correct. Wrong options must sound
  plausible but be clearly wrong according to FACTS.
- "answer" is the index (0 to 3) of the correct option. Vary which position is correct.
- Questions must make sense on their own, without the video. No "all of the above", no "none of the above".

PAPER: {{paper}}
FACTS:
{{facts}}
