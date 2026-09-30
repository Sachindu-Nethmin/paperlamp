#!/usr/bin/env python3
"""Benchmark the local Ollama models on the tool's core job: pull key facts and
numbers out of a paper section as JSON, without inventing any.

Scores per model:
  json_ok    - the reply parsed as the requested JSON
  precision  - share of numbers in the reply that literally occur in the source
  recall     - share of the gold key numbers the reply found
  seconds    - wall time; tok_s - generation speed reported by Ollama

Run:  python3 bench/model_bench.py <paper.txt> [model ...]
"""
import json, re, sys, time, urllib.request

OLLAMA = "http://127.0.0.1:11434"
DEFAULT_MODELS = ["MichelRosselli/bonsai-27b:latest", "ornith:9b", "gemma4:E4B", "qwen3:14b"]

# Section 2 of SWE-bench (arXiv:2310.06770) and the numbers a good summary must keep.
GOLD = ["2,294", "12", "90,000", "195", "40%", "51", "1.7", "3.0", "32.8", "300", "11", "3,010"]

PROMPT = """You are extracting facts from a research paper section for a video script.
Return JSON: {{"key_numbers": [{{"value": "<number exactly as written>", "meaning": "<what it measures>"}}],
"claims": ["<one-sentence factual claim>"]}}.
Rules: copy every number exactly as it appears in the text; never compute or invent numbers;
include every quantitative fact about the dataset, tasks, codebases, patches and tests.

TEXT:
{text}"""


def section(txt_path):
    t = open(txt_path, encoding="utf-8").read()
    a, b = t.index("2     SWE- BENCH"), t.index("3     SWE-L LAMA")
    return re.sub(r"[ \t]+", " ", t[a:b])


def norm(n):
    return n.replace(",", "").rstrip("%").rstrip(".")


def numbers(s):
    return re.findall(r"\d[\d,]*(?:\.\d+)?%?", s)


def run(model, text):
    body = json.dumps({"model": model, "stream": False, "format": "json", "think": False,
                       "keep_alive": 0,
                       "options": {"temperature": 0.1, "num_ctx": 8192, "num_predict": 1500},
                       "messages": [{"role": "user", "content": PROMPT.format(text=text)}]}).encode()
    t0 = time.time()
    req = urllib.request.Request(OLLAMA + "/api/chat", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1800) as r:
        d = json.load(r)
    secs = time.time() - t0
    out = d["message"]["content"]
    tok_s = d.get("eval_count", 0) / max(d.get("eval_duration", 1) / 1e9, 1e-9)
    try:
        j = json.loads(out); ok = True
    except Exception:
        j, ok = {}, False
    src = {norm(n) for n in numbers(text)}
    got = [norm(n) for n in numbers(json.dumps(j))] if ok else [norm(n) for n in numbers(out)]
    prec = sum(n in src for n in got) / len(got) if got else 0.0
    rec = sum(norm(g) in set(got) for g in GOLD) / len(GOLD)
    return dict(model=model, json_ok=ok, precision=round(prec, 3), recall=round(rec, 3),
                numbers=len(got), seconds=round(secs, 1), tok_s=round(tok_s, 1),
                invented=sorted({n for n in got if n not in src})[:10], raw=out[:4000])


if __name__ == "__main__":
    text = section(sys.argv[1])
    models = sys.argv[2:] or DEFAULT_MODELS
    results = []
    for m in models:
        print(f"== {m}", flush=True)
        try:
            r = run(m, text)
        except Exception as e:
            r = dict(model=m, error=str(e))
        results.append(r)
        print({k: v for k, v in r.items() if k != "raw"}, flush=True)
    json.dump(results, open("bench/results.json", "w"), indent=1)
