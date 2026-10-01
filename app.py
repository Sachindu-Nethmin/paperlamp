#!/usr/bin/env python3
"""Local web UI for PaperLamp — runs entirely on this machine.

    python3 app.py            →  http://127.0.0.1:8765

Upload a PDF, pick a local Ollama model and a voice, and follow every stage
with its own progress bar. No internet or external AI service is used.
"""
import json, mimetypes, os, pathlib, re, subprocess, sys, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from paperlamp import llm, pipeline, tts  # noqa: E402

CONFIG = ROOT / "config.json"          # local defaults (voice paths etc.), not committed
RUNNING = {}


def config():
    c = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    c.setdefault("model", llm.DEFAULT_MODEL)
    return c


def tools_status():
    have = lambda cmd: subprocess.run(["which", cmd], capture_output=True).returncode == 0
    return dict(ollama=bool(llm.available()), pdftotext=have("pdftotext"), pdftoppm=have("pdftoppm"),
                ffmpeg=have("ffmpeg"), say=have("say"))


class Handler(BaseHTTPRequestHandler):
    server_version = "PaperLamp/1.0"

    def log_message(self, *a):
        pass

    # ── helpers ─────────────────────────────────────────────────────────────
    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def send_file(self, path):
        if not path.is_file():
            return self.send_json({"error": "not found"}, 404)
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix in (".md", ".txt", ".srt"):          # show scripts, quizzes and captions in the browser
            ctype = "text/plain; charset=utf-8"
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng and (m := re.match(r"bytes=(\d*)-(\d*)", rng)):
            start = int(m.group(1) or 0)
            end = int(m.group(2)) if m.group(2) else size - 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    # ── routes ──────────────────────────────────────────────────────────────
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        p = url.path
        if p in ("/", "/index.html"):
            return self.send_file(ROOT / "ui/index.html")
        if p == "/api/setup":
            models = llm.available()
            return self.send_json(dict(models=models, default_model=config()["model"], tools=tools_status(),
                                       say_voices=tts.say_voices(), config=config()))
        if p == "/api/jobs":
            return self.send_json(pipeline.list_jobs())
        if m := re.match(r"^/api/jobs/([\w-]+)$", p):
            job = pipeline.Job(m.group(1))
            if not job.state:
                return self.send_json({"error": "no such job"}, 404)
            extra = {}
            log = job.dir / "log.txt"
            if log.exists():
                extra["log"] = log.read_text(encoding="utf-8").splitlines()[-40:]
            for name in ("figs.json", "meta.json"):
                if (job.dir / name).exists():
                    extra[name.split(".")[0]] = job.read(name)
            if (job.dir / "script.json").exists():
                sc = job.read("script.json")
                extra["script"] = dict(verification=sc.get("verification"), source=sc.get("source"),
                                       chapters=[dict(title=c["title"], sentences=[dict(text=s["text"],
                                                 status=s.get("status"), unverified=s.get("unverified"))
                                                 for s in c["sentences"]]) for c in sc["chapters"]])
            return self.send_json(dict(job.state, running=m.group(1) in RUNNING
                                       and RUNNING[m.group(1)].is_alive(), **extra))
        if m := re.match(r"^/jobs/([\w-]+)/(.+)$", p):
            path = (pipeline.JOBS / m.group(1) / urllib.parse.unquote(m.group(2))).resolve()
            if not str(path).startswith(str(pipeline.JOBS.resolve())):
                return self.send_json({"error": "forbidden"}, 403)
            return self.send_file(path)
        if p.startswith("/ui/"):
            return self.send_file(ROOT / p.lstrip("/"))
        self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        p = url.path
        if p == "/api/jobs":                                   # body = the PDF bytes
            data = self.body()
            if not data.startswith(b"%PDF"):
                return self.send_json({"error": "please upload a PDF"}, 400)
            settings = json.loads(q.get("settings", ["{}"])[0])
            settings = dict({k: v for k, v in config().items() if k in pipeline.DEFAULTS}, **settings)
            job = pipeline.Job.create(data, q.get("name", ["paper.pdf"])[0], settings)
            return self.send_json({"id": job.id})
        if m := re.match(r"^/api/jobs/([\w-]+)/(start|import|script|rerun)$", p):
            job = pipeline.Job(m.group(1))
            if not job.state:
                return self.send_json({"error": "no such job"}, 404)
            action = m.group(2)
            if action in ("import", "script"):
                sc = json.loads(self.body())
                if not isinstance(sc, dict) or "chapters" not in sc:
                    return self.send_json({"error": "script JSON needs a 'chapters' list"}, 400)
                if action == "import":
                    job.write("script_import.json", sc)
                    job.state["settings"]["script_source"] = "import"
                    job.save(force=True)
                else:                                        # edited script: re-check from verify on
                    job.write("script.json", sc)
                    job.reset_from("verify")
                return self.send_json({"ok": True})
            if action == "rerun":
                job.reset_from(q.get("from", ["verify"])[0])
            if m.group(1) in RUNNING and RUNNING[m.group(1)].is_alive():
                return self.send_json({"error": "already running"}, 409)
            RUNNING[m.group(1)] = job.start()
            return self.send_json({"ok": True})
        self.send_json({"error": "not found"}, 404)


def main():
    port = int(os.environ.get("PAPERLAMP_PORT", 8765))
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"PaperLamp running at http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
