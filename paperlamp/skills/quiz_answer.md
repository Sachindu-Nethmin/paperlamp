---
name: quiz_answer
purpose: Answer a multiple-choice question from the facts alone, to double-check a generated question.
---
Answer this multiple-choice question using ONLY the facts below. If the facts do not clearly support
exactly one option, answer "none". Return JSON: {"answer": "A"} (or "B", "C", "D", "none").

QUESTION: {{question}}
{{options}}

FACTS:
{{facts}}
