---
name: glossary_terms
purpose: Collect the technical terms of a part of a paper, with definitions, for an in-depth video.
---
From this part of the research paper "{{paper}}", list the technical terms, acronyms and method or
dataset names that a final-year undergraduate in computing may not know, as this paper uses them.

Return JSON: {"terms": [{"term": "the term exactly as the paper writes it", "definition": "one plain sentence, at most 30 words"}]}

Rules:
- At most {{n}} terms, the most important first. Skip everyday words and the paper's own section names.
- For an acronym, use the acronym as the term and say what it stands for in the definition.
- Define the term the way this paper uses it; use the paper's own definition when it gives one.
- Add no numbers that are not in the text.

TEXT:
{{text}}
