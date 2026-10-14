---
name: explain_figure
purpose: Walk through a figure or table of a paper for a video that shows it on screen.
---
You are explaining a {{kind}} of the research paper "{{paper}}" to a final-year undergraduate
while it is on screen.
Section: "{{section}}"
Caption: {{caption}}
Text inside the {{kind}} (labels, axis titles, rows and cells, in reading order; may be incomplete):
{{inside}}

Return JSON: {"walkthrough": ["two to five sentences of plain spoken English"]}
Explain how to read it (what the axes, rows or columns are), then the main pattern or comparison it
shows and why it matters. Quote numbers only exactly as they appear above, and only the few that
matter. Do not repeat the caption word for word. No LaTeX or symbols.
