"""Orchestrates a job: PDF in → verified, narrated, captioned explainer video out.

Each stage writes its result into the job folder, so a job can resume, or be
re-run from any stage (e.g. after editing script.json). Progress for every
stage is kept in state.json, which the web UI polls.
"""
import json, os, pathlib, re, shutil, threading, time, traceback, uuid

from . import align, analyze, assemble, llm, memory, passes, pdf, quiz, script, tts, verify
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
                depth="summary")


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
        st = dict(DEFAULTS, **{k: v for k, v in (settings or {}).items() if v not in (None, "")})
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
        self.write("doc.json", dict(pages=doc["pages"], pdf_title=doc["title"], license=pdf.license_hint(full),
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
        if depth in passes.PASSES:                  # reading-pass videos (Keshav's three passes)
            sc = passes.write(self.read("meta.json"), self.read("notes.json"), s["model"], cb, depth,
                              self.read("figs.json"), self.read("doc.json"), (self.dir / "paper.txt").read_text())
        else:
            sc = script.write(self.read("meta.json"), self.read("notes.json"), float(s["minutes"]), s["model"], cb,
                              depth=depth, figures=self.read("figs.json"))
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
                if ch["key"] == "paper" and ch["sentences"] and head:
                    ch["sentences"][0]["align"] = dict(page=1, focus=head, highlight=title)
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
        note = ("Narration: AI voice clone." if s["voice"] == "chatterbox" else "Narration: synthetic voice.")
        final, total = assemble.build(self.dir, sents, durs, meta, pages, cb, voice_note=note)
        self.write_docs(sents, meta)
        self.state["outputs"] = dict(video="out/video.mp4", captions="out/captions.srt",
                                     description="out/description.txt", script="out/script.md",
                                     quiz="out/quiz.md" if (self.dir / "out/quiz.md").exists() else None,
                                     seconds=total)

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
        desc = (f"{meta.get('one_line', '')}\n\nCHAPTERS\n" + "\n".join(chapters) +
                f"\n\nPAPER\n{authors}. \"{meta.get('title', '')}\". {meta.get('venue', '')} {meta.get('year', '')}\n\n"
                f"{src}. Every number in the narration was checked against the paper text "
                f"({v.get('ok', 0)} sentences verified, {v.get('fixed', 0)} repaired, {v.get('dropped', 0)} removed). "
                "Pages shown are from the paper itself. "
                + (f"Paper license: {lic}.\n" if (lic := self.read("doc.json").get("license")) else
                   "Check the paper's license before publishing this video.\n"))
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
    whole = depth in ("full", "pass3")
    skip = script.skip_re(depth)
    return pdf.chunks([sc for sc in secs if (whole or not sc["appendix"])
                       and not skip.search(sc["title"] + " " + sc.get("parent", ""))])


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
