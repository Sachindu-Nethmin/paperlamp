---
name: paper_meta
purpose: Read the first page of a paper and return its bibliographic facts.
---
Read the first page of a research paper below and return JSON:
{"title": "...", "authors": ["..."], "affiliations": ["..."], "venue": "...", "year": "...",
 "one_line": "one plain-English sentence saying what the paper does"}

Rules:
- Copy the title and author names exactly as printed. Do not guess missing fields; use "" instead.
- venue: the conference or journal named on the page (e.g. "ICLR 2024"), else "".
- one_line: no hype words, no numbers that are not on the page.

FIRST PAGE:
{{text}}
