#!/usr/bin/env python3
"""Local web UI for PaperLamp — runs entirely on this machine.

    python3 app.py            →  http://127.0.0.1:8765

Upload a PDF, pick a local Ollama model and a voice, and follow every stage
with its own progress bar. No internet or external AI service is used.
"""
import json, mimetypes, os, pathlib, re, signal, subprocess, sys, threading, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from paperlamp import llm, memory, pipeline, tts  # noqa: E402

# local defaults (voice paths etc.), not committed; the installed app keeps its own copy
CONFIG = pipeline.HOME / "config.json" if (pipeline.HOME / "config.json").exists() else ROOT / "config.json"


class Runner:
    """One job at a time. Every job's heavy steps (the language model, voice cloning,
    rendering) need several GB; running two jobs at once is what makes a 16 GB laptop
    swap. Jobs wait in a queue, and when the queue is empty everything is unloaded."""

    def __init__(self):
        self.queue, self.current = [], None
        self.lock, self.wake = threading.Lock(), threading.Event()
        threading.Thread(target=self.loop, daemon=True).start()

    def add(self, jid):
        with self.lock:
            if jid == self.current or jid in self.queue:
                return None
            self.queue.append(jid)
            pos = len(self.queue) - (0 if self.current else 1)
        self.wake.set()
        return pos

    def position(self, jid):
        with self.lock:
            return self.queue.index(jid) + 1 if jid in self.queue else 0

    def loop(self):
        while True:
            self.wake.wait()
            with self.lock:
                if not self.queue:
                    self.wake.clear()
                    continue
                self.current = self.queue.pop(0)
            job = pipeline.Job(self.current)
            try:
                job.run()
            finally:
                with self.lock:
                    self.current = None
                    idle = not self.queue
                if idle:                              # nothing waiting: give all the memory back
                    freed = memory.free_all()
                    job.log(f"idle: freed {freed['ollama_gb']} GB of language model, "
                            f"stopped {freed['voice_servers']} voice server(s)")


RUNNER = Runner()


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
            jobs = pipeline.list_jobs()
            for j in jobs:
                j.update(running=RUNNER.current == j["id"], queued=RUNNER.position(j["id"]))
            return self.send_json(jobs)
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
            return self.send_json(dict(job.state, running=RUNNER.current == m.group(1),
                                       queued=RUNNER.position(m.group(1)), **extra))
        if p == "/api/memory":
            return self.send_json(dict(memory.status(), current=RUNNER.current, queue=list(RUNNER.queue)))
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
        if m := re.match(r"^/api/jobs/([\w-]+)/derive$", p):    # another kind of video of the same paper
            src = pipeline.Job(m.group(1))
            depth = q.get("depth", [""])[0]
            if not src.state:
                return self.send_json({"error": "no such job"}, 404)
            if depth not in ("summary", "full", "pass1", "pass2", "pass3"):
                return self.send_json({"error": "unknown kind of video"}, 400)
            if not (src.dir / "notes.json").exists():
                return self.send_json({"error": "this job has no notes yet"}, 409)
            job = pipeline.Job.derive(src, depth)
            return self.send_json({"id": job.id, "queued": RUNNER.add(job.id)})
        if p == "/api/memory/free":
            if RUNNER.current:
                return self.send_json({"error": "a video is being made; each step frees its memory when it "
                                                "finishes, and everything is freed when the queue is empty"}, 409)
            return self.send_json(memory.free_all())
        if p == "/api/shutdown":                            # the app is quitting: free memory, then exit
            self.send_json({"ok": True})
            threading.Thread(target=shutdown, daemon=True).start()
            return
        if m := re.match(r"^/api/jobs/([\w-]+)/reveal$", p):  # show the video (or the job) in Finder
            job = pipeline.Job(m.group(1))
            target = job.dir / "out" / "video.mp4"
            subprocess.run(["open", "-R", str(target if target.exists() else job.dir)])
            return self.send_json({"ok": True})
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
            if RUNNER.current == m.group(1):
                return self.send_json({"error": "already running"}, 409)
            if action == "rerun":
                job.reset_from(q.get("from", ["verify"])[0])
            return self.send_json({"ok": True, "queued": RUNNER.add(m.group(1))})
        self.send_json({"error": "not found"}, 404)


def shutdown(*_):
    """Free the language model and any voice server before the process goes away."""
    try:
        memory.free_all()
    finally:
        os._exit(0)


def main():
    port = int(os.environ.get("PAPERLAMP_PORT", 8765))
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    signal.signal(signal.SIGTERM, shutdown)               # the app stops us this way: free memory first
    print(f"PaperLamp running at http://127.0.0.1:{port}  (Ctrl+C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
