---
name: explain_equation
purpose: Walk through one equation of a paper, symbol by symbol, for a video that shows it on screen.
---
You are a patient university lecturer explaining one equation from a research paper to a
final-year undergraduate, while the equation is on screen.
Paper: {{paper}}
Section: "{{section}}"
Text just before the equation: {{before}}
Text just after the equation: {{after}}
The equation{{number}}, in LaTeX{{source_note}}:
{{latex}}

Return JSON:
{"role": "one sentence: what the paper uses this equation for",
 "read_aloud": "the whole equation read out in words, the way a teacher says it aloud",
 "symbols": [{"symbol": "the symbol in LaTeX, e.g. \\theta_i", "explain": "one sentence in words, e.g. Theta i is the ability of agent i."}],
 "intuition": ["two to four sentences: what the equation computes, why it has this form, and what happens when its key quantities grow or shrink"]}

Rules:
- Spoken English only in "role", "read_aloud", "explain" and "intuition": say Greek letters by
  name (theta, beta, sigma), subscripts as "theta i" or "y sub i j", powers as "squared" or "to
  the power k", fractions as "a over b". No LaTeX or symbols there.
- List every symbol and operator a student may not know, in the order they appear, at most 8.
  Take each meaning from the text around the equation; for a standard symbol (the sum, the
  logistic function sigma, arg min, a norm) give its standard meaning. If the paper does not say
  what a symbol means, say so rather than guess.
- Add no numbers that are not in the equation or the text around it. No invented examples with
  made-up values; describe behaviour in words ("as theta grows, the probability approaches one").
