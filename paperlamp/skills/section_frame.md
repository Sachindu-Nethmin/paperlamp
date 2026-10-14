---
name: section_frame
purpose: Open and close a section of a long, sentence-by-sentence paper video.
---
A long video goes through the research paper "{{paper}}" sentence by sentence. It is about to
start the section "{{section}}"{{subsections}}. These are the section's sentences:
{{text}}

Return JSON:
{"intro": "one or two sentences of plain spoken English: what this section sets out to do and how it connects to the paper so far",
 "recap": ["two or three sentences: the main points of this section a viewer should remember"]}
Use only what the section says. Keep every number exactly as written, and add none.
Do not start with "In this section" both times; vary the wording.
