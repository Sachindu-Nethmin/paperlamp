"""Minimal client for a local Ollama server (no internet, no API keys).

Streams tokens so the UI can show progress, asks for JSON when a task needs
structure, and loads prompt templates ("skills") from paperlamp/skills/*.md.
"""
import json, pathlib, re, time, urllib.request

OLLAMA = "http://127.0.0.1:11434"
DEFAULT_MODEL = "ornith:9b"          # smallest local model with zero invented numbers in bench/
SKILLS = pathlib.Path(__file__).resolve().parent / "skills"


def available():
    """Installed models, or [] if Ollama isn't running."""
    try:
        with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=5) as r:
            return [m["name"] for m in json.load(r)["models"]]
    except Exception:
        return []


def unload(model=DEFAULT_MODEL):
    """Free the model's memory now (before voice cloning, which needs it on a 16 GB laptop)."""
    try:
        req = urllib.request.Request(OLLAMA + "/api/generate", json.dumps({"model": model, "keep_alive": 0}).encode(),
                                     {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=60).read()
    except Exception:
        pass


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
            with urllib.request.urlopen(req, timeout=3600) as r:
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
