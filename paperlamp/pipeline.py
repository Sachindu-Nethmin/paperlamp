"""Orchestrates a job: PDF in → verified, narrated, captioned explainer video out.

Each stage writes its result into the job folder, so a job can resume, or be
re-run from any stage (e.g. after editing script.json). Progress for every
stage is kept in state.json, which the web UI polls.
"""
import json, os, pathlib, re, shutil, threading, time, traceback, uuid

from . import align, analyze, assemble, llm, memory, passes, pdf, quiz, script, tts, verify, youtube
from .speak import to_spoken

ROOT = pathlib.Path(__file__).resolve().parent.parent
# the installed app keeps its data in ~/Library/Application Support/PaperLamp (PAPERLAMP_HOME)
HOME = pathlib.Path(os.environ.get("PAPERLAMP_HOME") or ROOT)
JOBS = HOME / "jobs"
STAGES = [("parse", "Read the PDF"), ("figures", "Find figures and tables"), ("meta", "Identify the paper"),
          ("notes", "Take notes on each section"), ("script", "Write the script"),
          ("verify", "Check every number against the paper"),
          ("quiz", "Write self-check questions"), ("align", "Match sentences to the page"),
          ("voice", "Record narration"), ("render", "Render and assemble the video")]
LLM_STAGES = {"meta", "notes", "script", "verify", "quiz"}      # stages that may call the local model
DEFAULTS = dict(model=llm.DEFAULT_MODEL, minutes=8, voice="say", say_voice="", say_rate=175,
                chatterbox_python="", voice_reference="", script_source="ollama", quiz_in_video=0,
                depth="summary", export_dir="", read_appendix=0, permission="")

# each finished file, and the name it takes inside the paper's own export folder
EXPORTED = [("video", "out/video.mp4", "video.mp4"), ("captions", "out/captions.srt", "captions.srt"),
            ("captions_burned_in", "out/captions.ass", "captions.ass"),
            ("description", "out/description.txt", "description.txt"), ("script", "out/script.md", "script.md"),
            ("quiz", "out/quiz.md", "quiz.md"), ("narration", "out/narration.wav", "narration.wav")]


def number_of(name):
    """The leading order number of a folder name, or None if it has none: "03 - A Paper" -> 3."""
    head = name.split(" - ", 1)[0]
    return int(head) if head.isdigit() else None


def unnumber(name):
    """The paper name without its order number."""
    return name.split(" - ", 1)[1] if number_of(name) is not None else name


def numbered(n, paper):
    """Order number first so folders sort into the order the videos were made: "01 - A Paper".
    Two digits so that 9 comes before 10."""
    return f"{n:02d} - {paper}"


def safe_name(meta, limit=90):
    """A filesystem-safe stem from the paper's own metadata: "Title (Venue Year)".
    Characters that break on Windows or in a shell glob are dropped, so the paper's own
    title is still readable but the folder travels: a colon becomes nothing, a slash a dash."""
    t = re.sub(r"\s+", " ", str(meta.get("title") or "Untitled")).strip()
    venue = re.sub(r"\s+", " ", str(meta.get("venue") or "")).strip()
    year = str(meta.get("year") or "")
    if venue and venue in t:                              # the title already says where it was published
        venue = ""
    if year and (year in t or year in venue):              # arXiv listings repeat the year inside the venue
        year = ""
    tail = f"({venue}{' ' if venue and year else ''}{year})" if venue or year else ""
    stem = re.sub(r'[/\\:*?"<>|\x00-\x1f]', "", f"{t} {tail}".strip()).replace(" - ", " ").rstrip(" .")
    if len(stem) > limit:                                 # keep the venue tail: it says which version this is
        stem = stem[:max(1, limit - len(tail) - 4)].rsplit(" ", 1)[0] + "..." + (" " + tail if tail else "")
    return stem or "Untitled"


def default_voice(settings):
    """The cloned voice when there is one to clone with, else the system voice. Cloning is the
    better narration, so it wins by default; it is only skipped when it could not work."""
    s = settings or {}
    if s.get("voice") in ("say", "chatterbox"):
        return s["voice"]
    return "chatterbox" if s.get("chatterbox_python") and s.get("voice_reference") else "say"


class Job:
    def __init__(self, jid):
        self.id, self.dir = jid, JOBS / jid
        self.state_path = self.dir / "state.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else None
        self._last = 0.0
        self.lock = threading.Lock()

    # ── state ──────────────────────────────────────────────────────────────
    @classmethod
    def create(cls, pdf_bytes, name, settings):
        jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        job = cls(jid)
        job.dir.mkdir(parents=True)
        (job.dir / "paper.pdf").write_bytes(pdf_bytes)
        chosen = {k: v for k, v in (settings or {}).items() if v not in (None, "")}
        st = dict(DEFAULTS, **chosen)
        st["voice"] = default_voice(chosen)             # your cloned voice unless you pick another
        job.state = dict(id=jid, name=name, created=time.time(), settings=st, error=None, outputs={},
                         stages={k: dict(label=l, status="pending", progress=0.0, msg="") for k, l in STAGES})
        job.save(force=True)
        return job

    def save(self, force=False):
        now = time.time()
        if force or now - self._last > 0.4:
            with self.lock:
                tmp = self.state_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.state, indent=1))
                tmp.replace(self.state_path)
            self._last = now

    def log(self, msg):
        with open(self.dir / "log.txt", "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + msg + "\n")

    def progress(self, stage):
        def cb(i, n, msg=""):
            s = self.state["stages"][stage]
            s["progress"] = round(min(max(i / max(n, 1), 0.0), 1.0), 4)
            if msg and msg != s["msg"]:
                s["msg"] = msg
                self.log(f"[{stage}] {msg}")
            self.save()
        return cb

    def read(self, name):
        return json.loads((self.dir / name).read_text(encoding="utf-8"))

    def write(self, name, obj):
        (self.dir / name).write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")

    # ── running ────────────────────────────────────────────────────────────
    def reset_from(self, stage):
        keys = [k for k, _ in STAGES]
        for k in keys[keys.index(stage):]:
            self.state["stages"][k].update(status="pending", progress=0.0, msg="")
        self.state["error"] = None
        self.save(force=True)

    def run(self):
        s = self.state["settings"]
        installed = llm.available()
        if installed and s["model"] not in installed:     # e.g. removed since the job was made
            s["model"] = llm.best_model(installed)
            self.log(f"the chosen model is not installed; using {s['model']}")
            self.save(force=True)
        if self.state["stages"].get("parse", {}).get("status") == "done" and self.stop_for_license():
            return
        try:
            for key, label in STAGES:
                st = self.state["stages"].setdefault(key, dict(label=label, status="pending", progress=0.0, msg=""))
                if st["status"] in ("done", "skipped"):
                    continue
                if s["script_source"] == "import" and key in ("meta", "notes", "script"):
                    st.update(status="skipped", progress=1.0, msg="imported script")
                    self.save(force=True)
                    continue
                st.update(status="running", started=time.time())
                self.save(force=True)
                if key in LLM_STAGES and memory.stop_voice_servers():
                    self.log("stopped a leftover voice server before using the language model")
                getattr(self, "stage_" + key)(self.progress(key))
                st.update(status="done", progress=1.0, ended=time.time())
                self.save(force=True)
                if key == "parse" and self.stop_for_license():
                    return
                later = [k for k, _ in STAGES[[k for k, _ in STAGES].index(key) + 1:]]
                if key in LLM_STAGES and not LLM_STAGES.intersection(later):
                    self.free_model_memory()            # the local model's work is done
        except Exception as e:
            for st in self.state["stages"].values():
                if st["status"] == "running":
                    st["status"] = "error"
            self.state["error"] = f"{type(e).__name__}: {e}"
            self.log(traceback.format_exc())
            self.save(force=True)
            self.free_model_memory()

    def license_problem(self):
        """Why this paper may not be made into a video, or None. Only a license that lets anyone
        publish an adapted copy is enough (see pdf.video_allowed), or the user's own note that
        the authors or the publisher gave permission."""
        if (self.state["settings"].get("permission") or "").strip():
            return None
        doc = self.read("doc.json") if (self.dir / "doc.json").exists() else {}
        lic = str(doc.get("license") or "")
        txt = self.dir / "paper.txt"
        ax = doc.get("arxiv") or pdf.arxiv_id(lic) or (pdf.arxiv_id(txt.read_text(encoding="utf-8")[:20000])
                                                          if txt.exists() else "")
        if not lic and ax:                                # older jobs never asked arXiv
            lic = pdf.resolve_license("", self.dir, ax)
            if lic:
                self.write("doc.json", dict(doc, license=lic, arxiv=ax))
        if pdf.video_allowed(lic):
            return None
        if lic:
            why = f"This paper is under {lic}, which does not allow publishing an adapted copy such as this video."
        elif ax:
            why = (f"No open license was found for this paper: neither the PDF nor its arXiv page "
                   f"(https://arxiv.org/abs/{ax}) shows one, or arXiv could not be reached to check. "
                   "arXiv's default license does not allow reuse.")
        else:
            why = "The paper does not state an open license, so it grants no right to reuse its pages."
        return why + (" Ask the authors or the publisher for permission; with it, you can continue this job "
                      "and the permission is noted in the video's description.")

    def stop_for_license(self):
        why = self.license_problem()
        if not why:
            self.state.pop("license_stop", None)
            return False
        self.state["license_stop"] = why
        self.state["error"] = "Stopped: the paper's license does not allow this video. " + why
        self.log("stopped before making the video: " + why)
        self.save(force=True)
        return True

    def retest_block(self, sc):
        """Study videos: the segment questions again, to answer from memory a day or two later
        (spaced retrieval beats rereading; Dunlosky et al. 2013). Answers are listed after the
        questions, so they can be covered while answering."""
        qs = sc.get("study_questions") or []
        if not qs:
            return ""
        return ("\n\nTEST YOURSELF TOMORROW\nAnswer these from memory a day or two after watching, then check:\n"
                + "\n".join(f"{i}. {q['question']}" for i, q in enumerate(qs, 1))
                + "\nAnswers:\n" + "\n".join(f"{i}. {q['answer']}" for i, q in enumerate(qs, 1)))

    def free_model_memory(self):
        """Unload every model Ollama holds, so voice cloning and rendering get that memory
        (Ollama itself keeps running idle, using a few MB)."""
        gb = llm.unload_all()
        if gb:
            self.log(f"freed {gb} GB: unloaded the local model; Ollama stays idle until the next job")

    def start(self):
        t = threading.Thread(target=self.run, daemon=True)
        t.start()
        return t

    # ── stages ─────────────────────────────────────────────────────────────
    def stage_parse(self, cb):
        doc = pdf.info(self.dir / "paper.pdf")
        cb(0, 3, f"{doc['pages']} pages")
        pages = pdf.page_texts(self.dir / "paper.pdf")
        (self.dir / "paper.txt").write_text("\n".join(pages), encoding="utf-8")
        heads = pdf.headings(self.dir / "paper.pdf")        # outline or font-checked headings
        secs = pdf.sections(pages, heads if sum(1 for h in heads if h.get("num")) >= 2 else None)
        cb(1, 3, f"{len(secs)} sections")
        full = "\n".join(pages)
        self.write("doc.json", dict(pages=doc["pages"], pdf_title=doc["title"],
                                    license=pdf.resolve_license(full, self.dir, pdf.arxiv_id(full)),
                                    arxiv=pdf.arxiv_id(full),
                                    first_page=pages[0][:4000] if pages else "",
                                    sections=[dict(sc, words=len(sc["text"].split())) for sc in secs]))
        self.write("chunks.json", select_chunks(secs, self.state["settings"].get("depth")))
        idx = align.build_index(self.dir / "paper.pdf", doc["pages"])
        self.write("index.json", idx)
        cb(3, 3, f"{doc['pages']} pages · {len(full.split()):,} words · {len(secs)} sections")

    def stage_figures(self, cb):
        figs = pdf.find_figures(self.dir / "paper.pdf", self.dir / "pages", lambda i, n: cb(i, n, f"page {i}/{n}"))
        self.write("figs.json", figs)
        shutil.rmtree(self.dir / "pages", ignore_errors=True)       # 300-dpi scans only needed for detection
        cb(1, 1, f"{len(figs)} figures and tables located")

    def stage_meta(self, cb):
        self.write("meta.json", analyze.paper_meta(self.read("doc.json"), self.state["settings"]["model"], cb))

    def stage_notes(self, cb):
        known = {n["title"]: n for n in self.read("notes.json")} if (self.dir / "notes.json").exists() else {}
        self.write("notes.json", analyze.section_notes(self.read("chunks.json"), self.state["settings"]["model"], cb,
                                                       known=known))

    @classmethod
    def derive(cls, src, depth):
        """Another kind of video of the same paper (e.g. pass 2 after pass 1): the parsed
        text, figures, title and notes are reused, so only sections the new kind needs
        and the old one skipped are read by the model."""
        settings = dict(src.state["settings"], depth=depth)
        name = re.sub(r",\s*(reading pass \d|whole paper|recorded so far.*)$", "", src.state["name"], flags=re.I)
        job = cls.create((src.dir / "paper.pdf").read_bytes(), name, settings)
        for name in ("doc.json", "index.json", "paper.txt", "figs.json", "meta.json", "notes.json"):
            if (src.dir / name).exists():
                shutil.copy(src.dir / name, job.dir / name)
        if (src.dir / "figs").exists():
            shutil.copytree(src.dir / "figs", job.dir / "figs")
        job.write("chunks.json", select_chunks(job.read("doc.json")["sections"], depth))
        for k in ("parse", "figures", "meta"):
            job.state["stages"][k].update(status="done", progress=1.0, msg=f"from job {src.id}")
        job.state["derived_from"] = src.id
        job.save(force=True)
        return job

    def stage_script(self, cb):
        s = self.state["settings"]
        t0 = time.time()
        depth = s.get("depth", "summary")
        if depth == "read":                         # the whole paper, every sentence explained in order
            from . import reader
            sc = reader.write(self.dir / "paper.pdf", self.read("meta.json"), self.read("figs.json"),
                              pdf.headings(self.dir / "paper.pdf"), s["model"], cb,
                              appendix=bool(s.get("read_appendix")))
        elif depth in passes.PASSES:                # reading-pass videos (Keshav's three passes)
            sc = passes.write(self.read("meta.json"), self.read("notes.json"), s["model"], cb, depth,
                              self.read("figs.json"), self.read("doc.json"), (self.dir / "paper.txt").read_text())
        else:
            sc = script.write(self.read("meta.json"), self.read("notes.json"), float(s["minutes"]), s["model"], cb,
                              depth=depth, figures=self.read("figs.json"))
        sc["tidied"] = script.tidy(sc["chapters"])
        sc["seconds"] = round(time.time() - t0, 1)
        self.write("script.json", sc)

    def stage_verify(self, cb):
        s = self.state["settings"]
        if s["script_source"] == "import" and not (self.dir / "script.json").exists():
            shutil.copy(self.dir / "script_import.json", self.dir / "script.json")
        sc = self.read("script.json")
        sc["chapters"] = [c for c in sc["chapters"] if c["key"] != "quiz"]     # the quiz stage rebuilds it
        notes = self.read("notes.json") if (self.dir / "notes.json").exists() else []
        if s["script_source"] == "import":             # report only: don't rewrite someone else's script
            known = verify.paper_numbers((self.dir / "paper.txt").read_text())
            st = dict(total=0, ok=0, flagged=0, numbers=0)
            for ch in sc["chapters"]:
                for x in ch["sentences"]:
                    bad = verify.check(x["text"], known)
                    st["total"] += 1; st["numbers"] += len(verify.numbers_in(x["text"]))
                    x["status"] = "flagged" if bad else "ok"; x["unverified"] = bad
                    st["flagged" if bad else "ok"] += 1
            sc["verification"] = st
            cb(1, 1, f"{st['ok']} ok · {st['flagged']} flagged (imported script, not rewritten)")
        else:
            sc = verify.run(sc, (self.dir / "paper.txt").read_text(), notes, s["model"], cb,
                            figures=self.read("figs.json"), index=self.read("index.json"))
        self.write("script.json", sc)

    def stage_quiz(self, cb):
        s = self.state["settings"]
        sc = self.read("script.json")
        if s.get("depth") == "study" and not int(s.get("quiz_in_video") or 0):
            s["quiz_in_video"] = 3                      # a study video always ends with recall questions
        if not int(s.get("quiz_in_video") or 0):          # off by default: no questions, no model time
            sc["chapters"] = [ch for ch in sc["chapters"] if ch["key"] != "quiz"]
            self.write("script.json", sc)
            cb(1, 1, "off")
            return
        notes = self.read("notes.json") if (self.dir / "notes.json").exists() else []
        said = [x["text"] for ch in sc["chapters"] if ch["key"] not in ("quiz", "outro") for x in ch["sentences"]]
        meta = sc.get("meta") or {}
        qz = quiz.make(meta, notes, said, (self.dir / "paper.txt").read_text(), s["model"], cb)
        self.write("quiz.json", qz)
        (self.dir / "out").mkdir(exist_ok=True)
        (self.dir / "out/quiz.md").write_text(quiz.to_markdown(qz, meta), encoding="utf-8")
        sc["chapters"] = [ch for ch in sc["chapters"] if ch["key"] != "quiz"]      # re-runs replace it
        ch = quiz.video_chapter(qz, k=int(s.get("quiz_in_video", 3) or 0)) if s.get("quiz_in_video") else None
        if ch:
            at = next((i for i, c in enumerate(sc["chapters"]) if c["key"] == "outro"), len(sc["chapters"]))
            sc["chapters"].insert(at, ch)
        self.write("script.json", sc)
        cb(1, 1, f"{len(qz['questions'])} questions · {len(qz['rejected'])} rejected"
                 + (f" · {len(ch['sentences']) // 2} in the video" if ch else ""))

    def sentences(self):
        sc, out = self.read("script.json"), []
        for ci, ch in enumerate(sc["chapters"]):
            for si, x in enumerate(ch["sentences"]):
                out.append(dict(id=f"c{ci:02d}_s{si:03d}", chapter=ch["title"], chapter_key=ch["key"], text=x["text"],
                                spoken=x.get("spoken") or to_spoken(x["text"]), align=x.get("align"),
                                card=x.get("card"), pause=x.get("pause", 0)))
        return out

    def stage_align(self, cb):
        sc = self.read("script.json")
        sc = align.run(sc, self.read("index.json"), self.read("figs.json"), cb)
        # "The paper" chapter opens on the real title on page 1
        idx = [ln for ln in self.read("index.json") if ln["page"] == 1]
        if idx:
            # upright text only: arXiv stamps run vertically up the margin and look "tallest"
            top = [ln for ln in idx if ln["box"][1] < 792 * 0.4
                   and (ln["box"][2] - ln["box"][0]) > 3 * (ln["box"][3] - ln["box"][1])]
            hmax = max((ln["box"][3] - ln["box"][1]) for ln in top) if top else 0
            title = [ln["box"] for ln in top if (ln["box"][3] - ln["box"][1]) >= 0.8 * hmax][:3]
            head = [min(b[0] for b in title), min(b[1] for b in title) - 10,
                    max(b[2] for b in title), max(b[3] for b in title) + 120] if title else None
            for ch in sc["chapters"]:
                # the opening sentence is the title, so it belongs on the title page even if
                # the title box wasn't found: page 1's top half beats wherever the text
                # match landed, which can be deep in the paper
                if ch["key"] == "paper" and ch["sentences"]:
                    ch["sentences"][0]["align"] = dict(page=1, focus=head or [54, 54, 558, 420],
                                                      highlight=title)
            # the author sentences: frame the title and the whole author block, and mark
            # each name as it is read
            names = (sc.get("meta") or {}).get("authors") or []
            found = align.author_boxes(idx, names) if names else {}
            if found:
                boxes = list(found.values()) + ([head] if head else [])
                block = [min(b[0] for b in boxes) - 12, min(b[1] for b in boxes) - 12,
                         max(b[2] for b in boxes) + 12, max(b[3] for b in boxes) + 12]
                for ch in sc["chapters"]:
                    for s in ch["sentences"]:
                        if "authors" in s:
                            s["align"] = dict(page=1, focus=block,
                                              highlight=[found[n] for n in s["authors"] if n in found])
        self.write("script.json", sc)

    def stage_voice(self, cb):
        self.free_model_memory()                    # e.g. a resumed job: the voice model needs that memory
        items = [(x["id"], x["spoken"]) for x in self.sentences()]
        durs = tts.narrate(items, self.dir / "audio", self.state["settings"], cb)
        self.write("durations.json", durs)

    def stage_render(self, cb):
        s = self.state["settings"]
        sents = self.sentences()
        durs = self.read("durations.json")
        meta = self.read("script.json").get("meta") or {}
        pages = self.read("doc.json")["pages"]
        final, total = assemble.build(self.dir, sents, durs, meta, pages, cb)
        self.write_docs(sents, meta)
        self.state["outputs"] = dict(video="out/video.mp4", captions="out/captions.srt",
                                     description="out/description.txt", script="out/script.md",
                                     quiz="out/quiz.md" if (self.dir / "out/quiz.md").exists() else None,
                                     seconds=total)
        self.package_youtube()
        self.export()

    def package_youtube(self, rebuild_docs=False):
        """out/youtube/: thumbnail, title, tags and upload settings (paperlamp/youtube.py).
        rebuild_docs: write the description again with today's code first (older videos)."""
        meta = self.read("script.json").get("meta") or {}
        self.license_problem()                        # older jobs: look the license up now (stored in doc.json)
        if rebuild_docs and (self.dir / "timeline.json").exists():
            self.write_docs(self.sentences(), meta)
        doc = self.read("doc.json") if (self.dir / "doc.json").exists() else {}
        s = self.state.get("settings") or {}
        info = youtube.package(self.dir, meta, s.get("depth", "summary"), str(doc.get("license") or ""),
                               (s.get("permission") or "").strip(), (self.state.get("outputs") or {}).get("seconds") or 0)
        self.state["youtube"] = dict(title=info["title"], allowed=info["allowed"])
        self.save(force=True)
        self.log(f"YouTube files ready: {info['title']}")
        return info

    def export(self, cb=lambda *a: None):
        """Copy the finished video and its side files into a folder of their own, named after
        the paper, so one folder holds one paper and nothing is ever overwritten. The job
        folder keeps everything; this is the publishable copy. Jobs with no export_dir
        setting export nothing."""
        folder = (self.state.get("settings") or {}).get("export_dir") or ""
        if not folder:
            return {}
        try:
            root = pathlib.Path(folder).expanduser()
            root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.log(f"export folder is not usable ({e}); the video stays in the job folder")
            return {}
        meta = self.read("script.json").get("meta") or {}
        paper = safe_name(meta)
        kinds = self.kinds_of_same_paper()
        folder_name = numbered(self.number_for(root, paper), paper)
        dest = root / folder_name
        if len(kinds) > 1:                    # a second video of the same paper gets its own folder
            dest /= (self.state.get("settings") or {}).get("depth", "summary")
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.log(f"could not make {dest} ({e}); the video stays in the job folder")
            return {}
        out = {}
        taken = self.exports_in(root)
        for i, (key, rel, name) in enumerate(EXPORTED):
            src = self.dir / rel
            if not src.exists():
                continue
            cb(i, len(EXPORTED), f"exporting {name}")
            target = dest / name
            if target.exists() and taken.get("files", {}).get(str(target.relative_to(root))) != self.id:
                target = self.undisturbed(target)      # never overwrite another video by accident
            youtube.clone(src, target)                   # copy, never move: the job stays re-runnable
            out[key] = str(target)
        yt = self.dir / "out" / "youtube"
        if (yt / "youtube.json").exists() and (self.dir / "out/video.mp4").exists():
            # a "YouTube" folder named for people, like FlowCast: YouTube takes the video's file
            # name as its first title; clones take no extra disk space
            info = json.loads((yt / "youtube.json").read_text(encoding="utf-8"))
            name = re.sub(r'[/\\:*?"<>|\x00-\x1f]', "", info["title"].replace(" | ", " - ")).strip(" .")[:150]
            ydir = dest / "YouTube"
            ydir.mkdir(exist_ok=True)
            for key, src, fname in (("yt_video", self.dir / "out/video.mp4", f"{name}.mp4"),
                                    ("yt_thumbnail", yt / "thumbnail.png", f"{name} - Thumbnail.png"),
                                    ("yt_details", yt / "details.txt", f"{name} - YouTube details.txt"),
                                    ("yt_captions", self.dir / "out/captions.srt", f"{name} - Captions.srt")):
                if not src.exists():
                    continue
                target = ydir / fname
                if target.exists() and taken.get("files", {}).get(str(target.relative_to(root))) != self.id:
                    target = self.undisturbed(target)
                youtube.clone(src, target)
                out[key] = str(target)
            (dest / "video.json").write_text(json.dumps(dict(
                info, job=self.id, made=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.state.get("created", 0))),
                files={k: str(pathlib.Path(v).relative_to(dest)) for k, v in out.items()
                       if str(v).startswith(str(dest))}), indent=1), encoding="utf-8")
        for path in out.values():
            taken.setdefault("files", {})[str(pathlib.Path(path).relative_to(root))] = self.id
        taken.setdefault("papers", {})[folder_name] = self.id
        (root / ".paperlamp-exports.json").write_text(json.dumps(taken, indent=1, sort_keys=True),
                                                     encoding="utf-8")
        self.state["exported"] = out
        self.state["exported_to"] = str(dest)
        self.save(force=True)
        self.log(f"exported {len(out)} files to {dest}")
        cb(len(EXPORTED), len(EXPORTED), f"exported to {paper}")
        return out

    def number_for(self, root, paper):
        """This paper's place in the export folder, counted in the order videos were made, so
        the folders sort into the order you made them. Stable: a job that already has a number
        keeps it, and a number is never handed out twice."""
        owned = self.exports_in(root).get("papers", {})
        for name, who in owned.items():
            if who == self.id and unnumber(name) == paper:
                return number_of(name)
        used = [number_of(n) for n in owned if number_of(n)] + \
               [number_of(p.name) for p in pathlib.Path(root).glob("*") if p.is_dir() and number_of(p.name)]
        return max(used, default=0) + 1

    def exports_in(self, root):
        """Which job last exported what in this folder: {"files": {path: job}, "papers":
        {"01 - Name": job}}. Kept in the export folder itself, so re-exporting a job
        overwrites its own copies and nobody else's."""
        path = pathlib.Path(root) / ".paperlamp-exports.json"
        try:
            taken = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return taken if isinstance(taken, dict) and ("files" in taken or "papers" in taken) \
            else {"files": taken, "papers": {}}

    @staticmethod
    def undisturbed(path):
        """A free filename beside one that is already taken: "Paper (2).mp4"."""
        n, ext = path.stem, path.suffix
        for i in range(2, 100):
            if not path.with_name(f"{n} ({i}){ext}").exists():
                return path.with_name(f"{n} ({i}){ext}")
        return path.with_name(f"{n} ({int(time.time())}){ext}")

    def kinds_of_same_paper(self):
        """How many finished videos exist of this paper. Used to name them apart."""
        mine = str(self.read("script.json").get("meta", {}).get("title") or "")
        kinds = set()
        for d in JOBS.glob("*/state.json"):
            try:
                st = json.loads(d.read_text(encoding="utf-8"))
                p = d.parent / "script.json"
                if (d.parent / "out" / "video.mp4").exists() and p.exists() and \
                        json.loads(p.read_text(encoding="utf-8")).get("meta", {}).get("title") == mine:
                    kinds.add((st.get("settings") or {}).get("depth", "summary"))
            except (OSError, ValueError):
                continue
        return kinds | {(self.state.get("settings") or {}).get("depth", "summary")}

    def write_docs(self, sents, meta):
        tl = self.read("timeline.json")["rows"]
        mmss = lambda t: f"{int(t // 60)}:{int(t % 60):02d}"
        chapters, seen = [], set()
        for r in tl:
            if r["chapter"] not in seen:
                seen.add(r["chapter"])
                chapters.append(f"{mmss(0 if not chapters else r['seg'][0])} {r['chapter']}")
        sc = self.read("script.json")
        v = sc.get("verification", {})
        src = (f"Script written offline by the local model {sc.get('model', '')} (Ollama)"
               if sc.get("source") == "ollama" else f"Script: {sc.get('source', 'imported')}")
        if sc.get("review"):                        # say who changed the model's draft, and how much
            r = sc["review"]
            src += f", then fact-checked against the paper by {r.get('by', 'a reviewer')} ({r.get('sentences_edited', 0)} sentences edited)"
        authors = ", ".join(meta.get("authors", []))
        doc = self.read("doc.json")
        # doc.json is hand-editable, so older jobs carry the arXiv id inside the license
        # string ("CC BY 4.0 (arXiv:2510.05096)") and no arxiv field of their own
        lic = str(doc.get("license") or "")
        ax = doc.get("arxiv") or pdf.arxiv_id(lic)
        lic = re.sub(r"\s*\(.*?\)\s*", " ", lic).strip()
        link = f"https://arxiv.org/abs/{ax}" if ax else ""
        cite = " ".join(x for x in (f"{authors}." if authors else "",
                             f'"{meta.get("title", "")}".' if meta.get("title") else "",
                             assemble.venue_year(meta)) if x)
        if link:
            cite += ("" if cite.endswith((".", "?", "!")) else ".") + " " + link
        one = str(meta.get("one_line") or "").strip()
        desc = (f"{one[:1].upper() + one[1:]}\n\nCHAPTERS\n" + "\n".join(chapters) +
                f"\n\nPAPER\n{cite}\n\nLICENSE AND WHAT YOU MAY DO\n" + (
                    f"Published with permission: {perm}\n" if (perm := (self.state["settings"].get("permission") or "").strip())
                    else "") + pdf.license_block(lic, ax) +
                self.retest_block(sc) +
                f"\n\nHOW THIS WAS MADE\n{src}. Every number in the narration was checked against the paper text "
                f"({v.get('ok', 0)} sentences verified, {v.get('fixed', 0)} repaired, {v.get('dropped', 0)} removed).\n")
        (self.dir / "out/description.txt").write_text(desc, encoding="utf-8")
        md = [f"# {meta.get('title', '')}", "", f"_{src}_", ""]
        cur = None
        for r in tl:
            if r["chapter"] != cur:
                cur = r["chapter"]; md += ["", f"## {mmss(r['start'])} {cur}", ""]
            md.append(f"- `{mmss(r['start'])}` {r['text']}")
        (self.dir / "out/script.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def select_chunks(secs, depth):
    """The sections a kind of video needs the model to read. Acknowledgements etc. never;
    related work and appendices only for the whole-paper and pass-3 videos."""
    if depth == "read":                             # explained sentence by sentence: no notes needed
        return []
    whole = depth in ("full", "pass3")
    skip = script.skip_re(depth)
    return pdf.chunks([sc for sc in secs if (whole or not sc["appendix"])
                       and not skip.search(sc["title"] + " " + sc.get("parent", ""))])


def library():
    """Every finished video, newest first, with what the Library shows: its YouTube title,
    thumbnail, length, kind and whether its paper's license allows publishing it."""
    JOBS.mkdir(parents=True, exist_ok=True)
    out = []
    for d in sorted(JOBS.iterdir(), reverse=True):
        if not (d / "out" / "video.mp4").exists() or not (d / "state.json").exists():
            continue
        try:
            st = json.loads((d / "state.json").read_text())
            sc = json.loads((d / "script.json").read_text()) if (d / "script.json").exists() else {}
            yt = json.loads((d / "out/youtube/youtube.json").read_text()) if (d / "out/youtube/youtube.json").exists() else None
        except (OSError, ValueError):
            continue
        meta, s = sc.get("meta") or {}, st.get("settings") or {}
        out.append(dict(id=st["id"], created=st.get("created"), depth=s.get("depth", "summary"),
                        paper=meta.get("title") or st.get("name"), authors=meta.get("authors") or [],
                        venue=meta.get("venue", ""), year=meta.get("year", ""),
                        seconds=(st.get("outputs") or {}).get("seconds"), voice=s.get("voice"),
                        youtube=yt, exported_to=st.get("exported_to"),
                        thumbnail=f"/jobs/{st['id']}/out/youtube/thumbnail.png" if yt else None,
                        video=f"/jobs/{st['id']}/out/video.mp4",
                        captions=f"/jobs/{st['id']}/out/captions.srt"))
    return out


def list_jobs():
    JOBS.mkdir(parents=True, exist_ok=True)
    out = []
    for d in sorted(JOBS.iterdir(), reverse=True):
        if (d / "state.json").exists():
            st = json.loads((d / "state.json").read_text())
            title = ""
            if (d / "meta.json").exists():                # the paper itself, so its videos group together
                try:
                    title = json.loads((d / "meta.json").read_text()).get("title", "")
                except ValueError:
                    pass
            out.append(dict(id=st["id"], name=st["name"], created=st["created"], error=st["error"],
                            depth=st.get("settings", {}).get("depth", "summary"), title=title,
                            done=all(x["status"] in ("done", "skipped") for x in st["stages"].values())))
    return out
