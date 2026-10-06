"""Command-line runner (same pipeline as the web UI).

    python3 -m paperlamp.cli paper.pdf [--minutes 8 | --auto-length | --key-sections | --study | --whole-paper [--with-appendix] | --pass 1|2|3] [--model ornith:9b]
                                 [--voice say|chatterbox] [--say-voice Samantha]
                                 [--chatterbox-python PY --reference WAV] [--quiz N]
                                 [--import script.json] [--resume JOB_ID --from STAGE]
"""
import argparse, json, pathlib, sys, time

from . import pipeline


def main():
    ap = argparse.ArgumentParser(description="Turn a research paper PDF into an explainer video, offline.")
    ap.add_argument("pdf", nargs="?")
    ap.add_argument("--minutes", type=float, default=8)
    ap.add_argument("--auto-length", action="store_true",
                    help="each section as long as its content needs; nothing padded or trimmed (ignores --minutes)")
    ap.add_argument("--key-sections", action="store_true",
                    help="the methodology, results and discussion in detail; the introduction and related work "
                         "only in a short opening (ignores --minutes)")
    ap.add_argument("--whole-paper", action="store_true",
                    help="every sentence of the paper explained in order, the highlighter on that sentence "
                         "(ignores --minutes; long: about 5 minutes of video per page)")
    ap.add_argument("--with-appendix", action="store_true", help="--whole-paper also goes through the appendix")
    ap.add_argument("--study", action="store_true",
                    help="a study video built on learning research: key terms first, a question before each "
                         "segment and its answer after, recall questions at the end")
    ap.add_argument("--every-section", action="store_true",
                    help="the older whole-paper explainer: every section summarised in order")
    ap.add_argument("--permission", default="",
                    help="the paper's license does not allow the video, but the authors or publisher gave "
                         "permission: say who and when (it goes into the description)")
    ap.add_argument("--pass", dest="read_pass", type=int, choices=[1, 2, 3],
                    help="a reading-pass video: 1 = is it relevant (5-10 min), 2 = the evidence, 3 = the method in depth")
    ap.add_argument("--quiz", type=int, default=0, help="self-check questions at the end of the video (default none)")
    ap.add_argument("--model", default=pipeline.DEFAULTS["model"])
    ap.add_argument("--voice", choices=["say", "chatterbox"],
                    help="which voice narrates (default: your cloned voice, when --chatterbox-python and "
                         "--reference are given)")
    ap.add_argument("--say-voice", default="")
    ap.add_argument("--chatterbox-python", default="")
    ap.add_argument("--reference", default="")
    ap.add_argument("--import", dest="script", help="render this script JSON instead of writing one")
    ap.add_argument("--resume", help="job id to resume")
    ap.add_argument("--from", dest="from_stage", help="re-run from this stage (with --resume)")
    a = ap.parse_args()
    if a.resume:
        job = pipeline.Job(a.resume)
        if a.from_stage:
            job.reset_from(a.from_stage)
    else:
        if not a.pdf:
            ap.error("give a PDF, or --resume JOB_ID")
        settings = dict(minutes=a.minutes, model=a.model, say_voice=a.say_voice,
                        chatterbox_python=a.chatterbox_python, voice_reference=a.reference,
                        script_source="import" if a.script else "ollama", quiz_in_video=a.quiz,
                        depth=f"pass{a.read_pass}" if a.read_pass else "study" if a.study else "read" if a.whole_paper
                        else "full" if a.every_section else "core" if a.key_sections
                        else "auto" if a.auto_length else "summary",
                        read_appendix=int(a.with_appendix), permission=a.permission)
        if a.voice:
            settings["voice"] = a.voice
        job = pipeline.Job.create(pathlib.Path(a.pdf).read_bytes(), pathlib.Path(a.pdf).name, settings)
        if a.script:
            job.write("script_import.json", json.loads(pathlib.Path(a.script).read_text()))
    print(f"job {job.id}  →  {job.dir}")
    t = job.start()
    last = ""
    while t.is_alive():
        time.sleep(1)
        st = json.loads(job.state_path.read_text())
        def mark(s):
            if s["status"] in ("done", "skipped"):
                return "✓"
            return f"{int(s['progress'] * 100)}%" if s["status"] == "running" else "·"
        line = "  ".join(f"{k}:{mark(s)}" for k, s in st["stages"].items())
        run = next((s for s in st["stages"].values() if s["status"] == "running"), None)
        line += f"   {run['msg'][:60] if run else ''}"
        if line != last:
            print("\r" + line[:200].ljust(200), end="", flush=True)
            last = line
    st = json.loads(job.state_path.read_text())
    print()
    if st["error"]:
        print("ERROR:", st["error"]); sys.exit(1)
    print("video:", job.dir / st["outputs"]["video"], f"({st['outputs']['seconds']:.0f}s)")


if __name__ == "__main__":
    main()
