---
name: prequestion
purpose: One question to ask before a video segment, answered by the segment (the prequestion effect).
---
A study video about a research paper asks the viewer one question BEFORE each segment, then
answers it at the end of the segment. Asking first makes people learn the segment better.
Paper: {{paper}}
Segment: "{{chapter}}"

Write one question that this segment answers, and its answer. Return JSON:
{"question": "...?", "answer": "..."}

Rules:
- The question asks about the single most important point of the segment (a key result, the
  main step of the method, or the main limitation), in plain words, at most 20 words, ending
  with "?". It must make sense to someone who has not seen the segment yet.
- The answer is one sentence, at most 30 words, using ONLY the facts below. Every number must
  appear in FACTS exactly as written.

FACTS:
{{facts}}
