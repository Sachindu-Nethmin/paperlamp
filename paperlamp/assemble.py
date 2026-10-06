"""Stage: timeline from measured audio → rendered segments → captions → final mp4.

Nothing uses estimated timings: each sentence starts where the real audio of the
previous one ended, plus a short pause (longer between chapters).
"""
import json, pathlib, re, shutil, subprocess, wave

from . import memory, render

FPS = render.FPS
LEAD, GAP_IN, GAP_CH, TAIL = 0.5, 0.35, 0.85, 2.5


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        raise RuntimeError(f"{' '.join(map(str, cmd))[:200]}\n{r.stderr[-1500:]}")
    return r


def timeline(sentences, durs):
    rows, t, prev = [], LEAD, None
    for s in sentences:
        if prev is not None:
            t += GAP_CH if s["chapter"] != prev else GAP_IN
        d = durs[s["id"]]
        rows.append(dict(s, start=round(t, 3), end=round(t + d, 3)))
        t += d + (s.get("pause") or 0)                   # e.g. thinking time after a quiz question
        prev = s["chapter"]
    total = round(t + TAIL, 3)
    for i, r in enumerate(rows):                         # segment spans the pause after it
        a = 0.0 if i == 0 else rows[i]["start"] - 0.15
        b = rows[i + 1]["start"] - 0.15 if i + 1 < len(rows) else total
        r["seg"] = [a, b]
        r["frames"] = round(b * FPS) - round(a * FPS)
    return rows, total


def build_audio(rows, total, audio_dir, out):
    params, frames = None, bytearray()
    for r in rows:
        with wave.open(str(pathlib.Path(audio_dir) / f"{r['id']}.wav")) as w:
            p = (w.getnchannels(), w.getsampwidth(), w.getframerate())
            params = params or p
            data = w.readframes(w.getnframes())
        ch, sw, sr = params
        frames += b"\0" * max(int(round(r["start"] * sr)) * ch * sw - len(frames), 0)
        frames += data
    ch, sw, sr = params
    frames += b"\0" * max(int(round(total * sr)) * ch * sw - len(frames), 0)
    raw = pathlib.Path(out).with_name("narration_raw.wav")
    with wave.open(str(raw), "wb") as w:
        w.setnchannels(ch); w.setsampwidth(sw); w.setframerate(sr); w.writeframes(bytes(frames))
    ln = "loudnorm=I=-14:TP=-1.5:LRA=11"
    m = run(["ffmpeg", "-hide_banner", "-i", str(raw), "-af", ln + ":print_format=json", "-f", "null", "-"]).stderr
    j = json.loads(m[m.rindex("{"):m.rindex("}") + 1])
    ap = (f"{ln}:measured_I={j['input_i']}:measured_TP={j['input_tp']}:measured_LRA={j['input_lra']}"
          f":measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true")
    run(["ffmpeg", "-y", "-hide_banner", "-i", str(raw), "-af", ap, "-ar", "48000", str(out)])
    raw.unlink(missing_ok=True)


# ── captions ─────────────────────────────────────────────────────────────────
MAXC, LINE = 84, 46


def wrap(card):
    if len(card) <= LINE:
        return [card]
    mid = len(card) / 2
    fits = [k for k, c in enumerate(card) if c == " " and max(k, len(card) - k - 1) <= LINE]
    i = min(fits or [k for k, c in enumerate(card) if c == " "],
            key=lambda k: (card[k - 1] not in ".,:;?!", abs(k - mid)))
    return [card[:i], card[i + 1:]]


def chunk(text):
    parts = []
    for p in re.split(r"(?<=[.?!:])\s+", text):
        cur = ""
        for s in re.split(r"(?<=,)\s+", p):
            if cur and len(cur) + 1 + len(s) > MAXC:
                parts.append(cur); cur = s
            else:
                cur = f"{cur} {s}".strip()
        parts.append(cur)
    hard = []
    for p in parts:
        while len(p) > MAXC:
            cut = p.rfind(" ", 0, MAXC)
            hard.append(p[:cut]); p = p[cut + 1:]
        hard.append(p)
    cards = []
    for p in hard:
        if cards and min(len(p), len(cards[-1])) < 28 and max(map(len, wrap(cards[-1] + " " + p))) <= LINE:
            cards[-1] += " " + p
        else:
            cards.append(p)
    return [c for c in cards if c]


def captions(rows, out_dir):
    cues = []
    for i, r in enumerate(rows):
        cards = chunk(r["text"])
        span, t = r["end"] - r["start"], r["start"]
        nxt = rows[i + 1]["start"] if i + 1 < len(rows) else r["end"] + 1
        for k, c in enumerate(cards):
            d = span * len(c) / sum(len(x) for x in cards)
            end = t + d if k < len(cards) - 1 else min(r["end"] + 0.4, nxt - 0.05)
            cues.append((t, end, wrap(c), not r.get("card")))   # quiz cards already show their text
            t += d
    ass_t = lambda s: f"{int(s // 3600)}:{int(s % 3600 // 60):02d}:{s % 60:05.2f}"

    def srt_t(s):
        ms = int(round(s * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    nl = "\\N"
    ass = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 2\n"
           "ScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, "
           "SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, "
           "Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
           "Style: Cap,Helvetica Neue,46,&H00FAF7F5,&H000000FF,&H30261709,&H30261709,0,0,0,0,100,100,0,0,3,16,0,2,"
           "200,200,56,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    ass += "".join(f"Dialogue: 0,{ass_t(a)},{ass_t(b)},Cap,,0,0,0,,{nl.join(l)}\n" for a, b, l, burn in cues if burn)
    srt = "".join(f"{n}\n{srt_t(a)} --> {srt_t(b)}\n" + "\n".join(l) + "\n\n" for n, (a, b, l, _) in enumerate(cues, 1))
    (out_dir / "captions.ass").write_text(ass, encoding="utf-8")
    (out_dir / "captions.srt").write_text(srt, encoding="utf-8")
    return len(cues)


def venue_year(meta, sep=" "):
    """ "ICLR 2024", not "ICLR 2024 2024" when the venue already carries the year."""
    venue, year = meta.get("venue", ""), str(meta.get("year", "") or "")
    return sep.join(x for x in (venue, "" if year and year in venue else year) if x)


def build(job, sentences, durs, meta, pages, progress, voice_note=""):
    job = pathlib.Path(job)
    out, work = job / "out", job / "build"
    out.mkdir(exist_ok=True)
    shutil.rmtree(work, ignore_errors=True); work.mkdir()
    rows, total = timeline(sentences, durs)
    progress(0, 4, "audio")
    build_audio(rows, total, job / "audio", out / "narration.wav")
    # one segment per sentence
    authors = ", ".join(meta.get("authors", []))           # every author; the card wraps long lists
    jobs, prev = [], None
    for i, r in enumerate(rows):
        card_spec = None
        if r["chapter_key"] == "outro":
            card_spec = dict(lines=[("SOURCE", 26, render.ACCENT, True), (meta.get("title", ""), 52, (245, 247, 250), True),
                                    (authors, 30, (143, 160, 188), False),
                                    (venue_year(meta, " · "), 30, (143, 160, 188), False)],
                             sub=" ".join(x for x in ("Pages shown are from the paper.", voice_note) if x))
        elif r.get("card"):
            card_spec = dict(quiz=r["card"])
        hud = dict(chapter=r["chapter"], pages=pages)
        jobs.append((work / f"seg_{i:04d}.mp4", str(job / "paper.pdf"), str(job / "cam"), r["frames"],
                     r.get("align"), prev, hud, card_spec))
        if not card_spec:
            prev = r.get("align")
    # build each page's camera canvas once, before the parallel workers read them
    for pg in sorted({r["align"]["page"] for r in rows if r.get("align")}):
        render.page_canvas(job / "paper.pdf", pg, job / "cam")
    workers = memory.render_workers()                    # as many as the free memory allows
    render.render_all(jobs, lambda d, n, m: progress(1 + d / n * 2, 4, f"{m} · {workers} workers"), workers=workers)
    (work / "concat.txt").write_text("".join(f"file '{j[0].name}'\n" for j in jobs))
    run(["ffmpeg", "-y", "-hide_banner", "-f", "concat", "-safe", "0", "-i", "concat.txt", "-c", "copy",
         "video_nocap.mp4"], cwd=work)
    ncues = captions(rows, out)
    progress(3, 4, f"burning {ncues} captions")
    shutil.copy(out / "captions.ass", work / "captions.ass")
    final = out / "video.mp4"
    run(["ffmpeg", "-y", "-hide_banner", "-i", "video_nocap.mp4", "-i", str(out / "narration.wav"),
         "-vf", "ass=captions.ass", "-r", str(FPS), "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "medium",
         "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(final)], cwd=work)
    (job / "timeline.json").write_text(json.dumps(dict(rows=rows, total=total), indent=1))
    shutil.rmtree(work, ignore_errors=True)               # the per-sentence clips are in the final video now
    progress(4, 4, f"{int(total // 60)}:{int(total % 60):02d} video")
    return final, total
