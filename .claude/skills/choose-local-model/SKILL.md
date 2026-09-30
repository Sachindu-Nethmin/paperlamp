---
name: choose-local-model
description: Benchmark the Ollama models installed on this machine and pick the smallest one that extracts paper facts without inventing numbers. Use when setting paper2video up on a new computer, when a model is slow or out of memory, or when the user asks which local model to use.
---

# Choose the local model

1. List installed models: `curl -s 127.0.0.1:11434/api/tags` (don't download new ones unless the
   user asks — the tool is meant to run offline).
2. Run the benchmark on any paper text (the default section-2 slicing expects the SWE-bench paper;
   adapt `section()` for another paper):
   `python3 bench/model_bench.py <paper.txt> [model ...]`
3. Read `bench/results.json`. Prefer, in order: `precision` (share of numbers that exist in the
   source — anything below 0.95 means the model invents numbers), then `recall`, then memory
   size, then speed. `json_ok` must be true.
4. Set the choice as the default in `config.json` (`{"model": "..."}`) — the UI preselects it.

Reference results on a 16 GB Apple M5 are in the README ("Choosing the local model").
