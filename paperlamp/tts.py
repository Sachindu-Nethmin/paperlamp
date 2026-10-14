"""Stage: narration audio, fully local.

Backends
  say         macOS system voices (built in, fast, always available)
  chatterbox  voice cloning with Resemble AI's open-source Chatterbox, run in its
              own Python environment (torch is heavy) from a 7-20 s reference WAV
Every clip is trimmed of leading/trailing silence and normalised; clips are cached
by text + settings so re-runs only synthesize changed sentences.
"""
import hashlib, json, os, pathlib, re, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def say_voices():
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    except FileNotFoundError:
        return []
    voices = []
    for line in out.splitlines():
        name = line.split("  ")[0].strip()
        if "en_" in line or "en-" in line:
            voices.append(name)
    return voices


def _post(src, dst):
    """Trim silence at both ends, normalise, 44.1 kHz stereo WAV."""
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-af",
                    "silenceremove=start_periods=1:start_silence=0.05:start_threshold=-50dB:detection=peak,areverse,"
                    "silenceremove=start_periods=1:start_silence=0.10:start_threshold=-50dB:detection=peak,areverse,"
                    "loudnorm=I=-18:TP=-1.5:LRA=11", "-ar", "44100", "-ac", "2", str(dst)], check=True)


MAX_CHARS = 300


def pieces(text, limit=MAX_CHARS):
    """Text cut at sentence ends, then at commas or semicolons, then at spaces, into parts of at
    most `limit` characters (short text stays whole)."""
    text = text.strip()
    if len(text) <= limit:
        return [text]
    out = []
    for unit in re.split(r"(?<=[.?!])\s+", text):
        while len(unit) > limit:
            cut = max(unit.rfind(", ", 0, limit), unit.rfind("; ", 0, limit))
            cut = cut + 1 if cut > limit // 3 else (unit.rfind(" ", 0, limit) if unit.rfind(" ", 0, limit) > 0 else limit)
            out.append(unit[:cut].strip()); unit = unit[cut:].strip()
        if out and len(out[-1]) + 1 + len(unit) <= limit:
            out[-1] += " " + unit
        elif unit:
            out.append(unit)
    return [p for p in out if p]


class Say:
    def __init__(self, voice="", rate=175):
        self.voice, self.rate = voice, rate

    def key(self):
        return f"say|{self.voice}|{self.rate}"

    def synth(self, text, out):
        raw = pathlib.Path(out).with_suffix(".aiff")
        cmd = ["say", "-r", str(self.rate), "-o", str(raw)] + (["-v", self.voice] if self.voice else []) + [text]
        subprocess.run(cmd, check=True)
        _post(raw, out)
        raw.unlink(missing_ok=True)

    def close(self):
        pass


class Chatterbox:
    """Long-lived model server (voice_server.py) in the Python that has chatterbox-tts."""

    def __init__(self, python, reference, exaggeration=0.4, cfg=0.5):
        self.python, self.ref = python, reference
        self.exaggeration, self.cfg = exaggeration, cfg
        self.proc = None

    def key(self):
        ref = pathlib.Path(self.ref)
        return f"cb|{ref}|{ref.stat().st_mtime_ns}|{self.exaggeration}|{self.cfg}"

    def _start(self):
        env = dict(os.environ, PYTORCH_MPS_LOW_WATERMARK_RATIO="0.25", PYTORCH_ENABLE_MPS_FALLBACK="1")
        self.proc = subprocess.Popen([self.python, str(HERE / "voice_server.py"), self.ref],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1, env=env)
        msg = self._reply()
        if not msg.get("ok"):
            raise RuntimeError(f"voice server failed: {msg.get('error')}")

    def _reply(self):
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("voice server exited (see terminal log)")
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue

    def synth(self, text, out):
        """Long text is recorded in pieces of at most MAX_CHARS and joined: the voice model's memory
        grows with the length of what it says, and a table read as one sentence can crash it."""
        parts = pieces(text)
        raws = [pathlib.Path(out).with_suffix(f".raw{k}.wav") for k in range(len(parts))]
        try:
            for part, raw in zip(parts, raws):
                if self.proc is None or self.proc.poll() is not None:
                    self._start()
                self.proc.stdin.write(json.dumps({"text": part, "out": str(raw), "exaggeration": self.exaggeration,
                                                  "cfg_weight": self.cfg}) + "\n")
                self.proc.stdin.flush()
                msg = self._reply()
                if not msg.get("ok"):
                    raise RuntimeError(msg.get("error"))
            if len(raws) == 1:
                _post(raws[0], out)
            else:                                   # a short breath between the pieces
                joined = pathlib.Path(out).with_suffix(".joined.wav")
                inputs = sum((["-i", str(r)] for r in raws), [])
                chain = "".join(f"[{k}:a]apad=pad_dur=0.25[a{k}];" for k in range(len(raws)))
                subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex",
                                chain + "".join(f"[a{k}]" for k in range(len(raws))) + f"concat=n={len(raws)}:v=0:a=1",
                                str(joined)], check=True)
                _post(joined, out)
                joined.unlink(missing_ok=True)
        finally:
            for r in raws:
                r.unlink(missing_ok=True)

    def close(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write(json.dumps({"quit": True}) + "\n"); self.proc.stdin.flush()
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()
        self.proc = None


def backend(settings):
    if settings.get("voice") == "chatterbox":
        return Chatterbox(settings["chatterbox_python"], settings["voice_reference"],
                          float(settings.get("exaggeration", 0.4)), float(settings.get("cfg", 0.5)))
    return Say(settings.get("say_voice", ""), int(settings.get("say_rate", 175)))


def narrate(items, out_dir, settings, progress, restart_every=6):
    """items: [(sentence_id, spoken_text)] → {sentence_id: seconds}."""
    out_dir = pathlib.Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    cache = out_dir / ".cache"; cache.mkdir(exist_ok=True)
    tts = backend(settings)
    durs, fresh = {}, 0
    try:
        for i, (sid, text) in enumerate(items):
            progress(i, len(items), f"{sid}: {text[:60]}")
            key = hashlib.sha256(f"{tts.key()}|{text}".encode()).hexdigest()[:20]
            cached, out = cache / f"{key}.wav", out_dir / f"{sid}.wav"
            if not cached.exists():
                tts.synth(text, cached)
                fresh += 1
                if isinstance(tts, Chatterbox) and fresh % restart_every == 0:
                    tts.close()                     # fresh server caps GPU memory growth
            out.unlink(missing_ok=True)
            try:
                os.link(cached, out)                # a second name, not a second copy: hours of clips are GBs
            except OSError:
                out.write_bytes(cached.read_bytes())
            durs[sid] = round(duration(out), 3)
    finally:
        tts.close()
    progress(len(items), len(items), f"{sum(durs.values()) / 60:.1f} min of narration")
    return durs
