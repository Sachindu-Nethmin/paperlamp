"""Minimal client for a local Ollama server (no internet, no API keys).

Streams tokens so the UI can show progress, asks for JSON when a task needs
structure, and loads prompt templates ("skills") from paperlamp/skills/*.md.
"""
import json, pathlib, re, time, urllib.request

OLLAMA = "http://127.0.0.1:11434"
DEFAULT_MODEL = "ornith:9b"          # smallest local model with zero invented numbers in bench/
SKILLS = pathlib.Path(__file__).resolve().parent / "skills"
# Ollama is on this machine: never route it through a system proxy (an app launched from
# Finder picks up the Mac's proxy settings, and a proxy can't reach our 127.0.0.1)
_open = urllib.request.build_opener(urllib.request.ProxyHandler({})).open
# bench/results.json (16 GB Apple M5): fewest invented numbers first, then the most key
# numbers found. Models that were not benchmarked rank after these.
BENCH_ORDER = ["ornith:9b", "MichelRosselli/bonsai-27b:latest", "gemma4:E4B", "qwen3:14b"]


def available():
    """Installed models, or [] if Ollama isn't running."""
    try:
        with _open(OLLAMA + "/api/tags", timeout=5) as r:
            return [m["name"] for m in json.load(r)["models"]]
    except Exception:
        return []


def best_model(installed, fallback=DEFAULT_MODEL):
    """The best installed model by the benchmark; else the fallback if it is installed,
    else any installed model."""
    for m in BENCH_ORDER:
        if m in installed:
            return m
    return fallback if fallback in installed or not installed else installed[0]


def loaded():
    """Models Ollama currently holds in memory: [(name, bytes)]."""
    try:
        with _open(OLLAMA + "/api/ps", timeout=5) as r:
            return [(m["name"], m.get("size_vram") or m.get("size") or 0) for m in json.load(r).get("models", [])]
    except Exception:
        return []


def unload(model=DEFAULT_MODEL):
    """Ask Ollama to drop one model from memory now."""
    try:
        req = urllib.request.Request(OLLAMA + "/api/generate", json.dumps({"model": model, "keep_alive": 0}).encode(),
                                     {"Content-Type": "application/json"})
        _open(req, timeout=60).read()
    except Exception:
        pass


def unload_all(wait=30):
    """Free every model Ollama holds (any job's, not just ours) and wait until it has
    really let go, so the voice model and renderer get that memory on a 16 GB laptop.
    Ollama's own server stays running; it needs only a few MB. Returns GB freed."""
    held = loaded()
    for name, _ in held:
        unload(name)
    end = time.time() + wait
    while loaded() and time.time() < end:
        time.sleep(0.5)
    return round(sum(b for _, b in held) / 1e9, 1)


def skill(name, **values):
    """Fill a prompt template: {{key}} placeholders; the YAML-ish header is dropped."""
    text = (SKILLS / f"{name}.md").read_text(encoding="utf-8")
    text = re.sub(r"^---.*?---\s*", "", text, flags=re.S)
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
    return text


def chat(prompt, model=DEFAULT_MODEL, want_json=True, ctx=8192, max_tokens=2048, temperature=0.2,
         on_token=None, retries=2):
    """One user message → reply text (or parsed JSON). on_token(n) reports progress."""
    body = {"model": model, "stream": True, "think": False, "keep_alive": "10m",
            "options": {"temperature": temperature, "num_ctx": ctx, "num_predict": max_tokens},
            "messages": [{"role": "user", "content": prompt}]}
    if want_json:
        body["format"] = "json"
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(OLLAMA + "/api/chat", json.dumps(body).encode(),
                                         {"Content-Type": "application/json"})
            out, n, t0 = [], 0, time.time()
            with _open(req, timeout=3600) as r:
                for line in r:
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    piece = d.get("message", {}).get("content", "")
                    if piece:
                        out.append(piece); n += 1
                        if on_token and n % 8 == 0:
                            on_token(n)
                    if d.get("done"):
                        break
            text = "".join(out).strip()
            if not want_json:
                return text
            return json.loads(_json_part(text))
        except Exception as e:                      # bad JSON or a dropped stream: try again
            last_err = e
            body["options"]["temperature"] = min(0.7, body["options"]["temperature"] + 0.2)
    raise RuntimeError(f"Ollama ({model}) failed: {last_err}")


def _json_part(text):
    """Models sometimes wrap JSON in prose or code fences."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    a, b = text.find("{"), text.rfind("}")
    return text[a:b + 1] if a >= 0 and b > a else text
