---
name: section_notes
purpose: Extract verifiable facts from one section of a paper.
---
You are taking notes on one section of a research paper for an explainer video.
Return JSON:
{"summary": "2-3 plain sentences",
 "key_numbers": [{"value": "number exactly as written in the text", "meaning": "what it measures"}],
 "claims": ["one factual sentence per claim"],
 "method": ["how something was done, one item per step"],
 "limitations": ["any limitation, threat to validity or caveat the authors state"],
 "figures": ["labels like 'Figure 3' or 'Table 2' that this section refers to"]}

Rules:
- Copy every number exactly as it appears (keep %, commas, decimals). Never compute, round or invent numbers.
- Only use facts written in the text below. If a list would be empty, return [].

SECTION: {{title}}
TEXT:
{{text}}
