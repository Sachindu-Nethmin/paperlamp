#!/usr/bin/env python3
"""Export the Claude-written narrations (from the hand-built videos) into the
tool's script.json format, so they can be compared with — and rendered exactly
like — scripts written by the local model.

    python3 bench/export_claude_scripts.py <videos_root> examples/

<videos_root> holds video/ (Watanabe et al., TOSEM) and video_swebench/
(Jimenez et al., ICLR 2024), each with a narration.py defining BEATS, CHAPTERS
and sentences().
"""
import importlib.util, json, pathlib, sys

META = {
    "video": dict(title="On the Use of Agentic Coding: An Empirical Study of Pull Requests on GitHub",
                  authors=["Miku Watanabe", "Hao Li", "Yutaro Kashiwa", "Brittany Reid", "Hajimu Iida",
                           "Ahmed E. Hassan"], venue="ACM TOSEM", year="2025",
                  one_line="An empirical study of 567 pull requests created with Claude Code across 157 projects."),
    "video_swebench": dict(title="SWE-bench: Can Language Models Resolve Real-World GitHub Issues?",
                           authors=["Carlos E. Jimenez", "John Yang", "Alexander Wettig", "Shunyu Yao", "Kexin Pei",
                                    "Ofir Press", "Karthik Narasimhan"], venue="ICLR", year="2024",
                           one_line="A benchmark of 2,294 real GitHub issues from 12 Python repositories."),
}
NAMES = {"video": "claude_tosem", "video_swebench": "claude_swebench"}


def export(project, out_dir):
    spec = importlib.util.spec_from_file_location(f"n_{project.name}", project / "narration.py")
    sys.path.insert(0, str(project))
    n = importlib.util.module_from_spec(spec); spec.loader.exec_module(n)
    chapters, cur = [], None
    for sid, beat, _visual, caption, spoken in n.sentences():
        if beat != cur:
            cur = beat
            chapters.append(dict(key=beat, title=n.CHAPTERS.get(beat, beat), sentences=[]))
        chapters[-1]["sentences"].append(dict(text=caption, spoken=spoken))
    sc = dict(meta=META[project.name], chapters=chapters, source="Claude (hand-checked in Claude Code)")
    out = pathlib.Path(out_dir) / f"{NAMES[project.name]}.script.json"
    out.write_text(json.dumps(sc, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{out}: {sum(len(c['sentences']) for c in chapters)} sentences")


if __name__ == "__main__":
    root, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    for p in ("video", "video_swebench"):
        export(root / p, out)
