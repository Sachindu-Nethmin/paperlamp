---
name: transcribe_equation
purpose: Read an equation from an image of a paper's page.
---
The image shows one display equation from a research paper. The PDF's own text layer gives it as:
{{pdf_text}}
Transcribe the equation exactly into LaTeX (no surrounding $ signs, no equation number).
Return JSON: {"latex": "..."}
