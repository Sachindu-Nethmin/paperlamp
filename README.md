# paper2video

Turn a research paper PDF into a narrated, captioned explainer video — **entirely offline**.
A small local model (via [Ollama](https://ollama.com)) writes the script, every number is
checked against the paper, and a "document camera" pans across the real PDF pages while a
highlighter marks the lines being explained. No internet connection or external AI service
is used at any point.

![stages](docs/ui.png)

## What it does

| Stage | What happens | Local tool |
|---|---|---|
| 1. Read the PDF | text, sections, metadata, a line-level index of every page | poppler (`pdftotext`, `pdfinfo`) |
| 2. Find figures and tables | caption detection + region finding, used to frame and outline them | poppler + NumPy |
| 3. Identify the paper | title, authors, venue from the first page | Ollama |
| 4. Take notes | per-section facts: key numbers (copied verbatim), claims, method, limitations | Ollama |
| 5. Write the script | chapter by chapter from the notes: intro, the paper, one chapter per main section, limitations, takeaway | Ollama |
| 6. Check every number | each number must occur in the paper; otherwise the model repairs the sentence or it is removed | Python |
| 7. Match sentences to the page | each sentence → page, lines to highlight, region to frame (figures/tables by label, else shared numbers and rare words) | Python |
| 8. Record narration | macOS system voice, or a clone of **your own** voice with Chatterbox | `say` / Chatterbox |
| 9. Render and assemble | document-camera video (pan/zoom, highlighter, figure outline), captions (ASS burned in + SRT), −14 LUFS audio, YouTube description with chapters | Pillow + ffmpeg |

Every stage has its own progress bar in the web UI, writes its result into the job folder,
and can be resumed or re-run (for example after editing `script.json`).

## Setup (macOS, Apple silicon tested)

```bash
brew install poppler ffmpeg
python3 -m pip install -r requirements.txt     # numpy, pillow
# Ollama app + one local model (the tool never downloads anything itself)
ollama list
```

Optional voice cloning (slow on a laptop, ~1 min per sentence): create a separate env with
`pip install chatterbox-tts "setuptools<81"`, record 7–20 s of your own voice as a WAV, and put
both paths in `config.json` (see `config.example.json`).

## Use

```bash
python3 app.py                     # web UI at http://127.0.0.1:8765
python3 -m p2v.cli paper.pdf --minutes 8 --voice say
python3 -m p2v.cli --resume <job-id> --from verify     # re-run after editing script.json
```

Outputs land in `jobs/<id>/out/`: `video.mp4`, `captions.srt`, `description.txt`, `script.md`.

## Choosing the local model

Benchmarked on a 16 GB Apple M5 with the models already installed (no downloads): each model
extracted the facts of SWE-bench §2 as JSON. *Precision* = share of reported numbers that really
occur in the text; *recall* = share of 12 key numbers found (`bench/model_bench.py`).

| Model | Size | Precision | Recall | Time | Speed |
|---|---|---|---|---|---|
| **ornith:9b** (default) | 5.6 GB | **100%** | 92% | 79 s | 15 tok/s |
| bonsai-27b (1-bit) | 4.4 GB | 95% | 92% | 76 s | 14 tok/s |
| gemma4:E4B | 9.6 GB | 93% | 83% | 43 s | 23 tok/s |
| qwen3:14b | 9.3 GB | 95% | 67% | 128 s | 9 tok/s |

**ornith:9b is the smallest model that invented no numbers**, and it leaves room for the voice
model in 16 GB. bonsai-27b is the absolute smallest and also works; the number check catches its
occasional slip. Run the `choose-local-model` skill (or the benchmark) on a new machine.

## Local model vs Claude

See [docs/COMPARISON.md](docs/COMPARISON.md): the same paper scripted by the local model and by
Claude, scored with metrics computed from the paper text (numbers verified, key-number coverage,
figure references, limitations, readability, repetition). Claude-written scripts for two papers are
in `examples/` and can be rendered by this tool (`--import`), so visuals and voice stay identical
and only the writing differs.

## Claude Code skills

`.claude/skills/` contains skills for working on this repo with Claude Code:

- **paper-to-video** — run, resume and troubleshoot the pipeline
- **paper-script-review** — have Claude fact-check and improve a local-model script, then re-render
- **choose-local-model** — benchmark installed Ollama models and pick the default
- **compare-scripts** — compare two scripts for the same paper and write up the result

## Accuracy and responsible use

- **Numbers:** a number that doesn't appear in the paper never reaches the video (repaired or removed).
  Labels like "Figure 3" are ignored. Derived arithmetic in an imported script is reported, not removed.
- **Limitations** get their own chapter, built only from what the authors state.
- **The paper's pages are shown on screen.** Check the paper's license (e.g. CC BY) before publishing and
  credit it; the description is generated with the citation.
- **Voice cloning:** only clone a voice you have permission to use. Chatterbox output carries Resemble
  AI's inaudible watermark. YouTube's disclosure rules list cloning *your own* voice for voiceovers as
  not requiring disclosure; check the current rules for your case.
- Small local models still write flatter, less insightful scripts than a frontier model — use the
  `paper-script-review` skill or edit `script.json` before publishing.

## Layout

```
app.py            web UI server (standard library only)      ui/index.html   the UI
p2v/pdf.py        text, sections, figure/table detection     p2v/llm.py      Ollama client
p2v/skills/*.md   prompt templates for each model task       p2v/analyze.py  per-section notes
p2v/script.py     chapter outline + narration                p2v/verify.py   number checking
p2v/align.py      sentence → page/lines/region               p2v/render.py   document camera
p2v/tts.py        say / Chatterbox backends                  p2v/voice_server.py  Chatterbox server
p2v/assemble.py   timeline, audio, captions, final mux       p2v/pipeline.py stages, state, resume
p2v/compare.py    script comparison metrics                  bench/          model benchmark, exports
```

## License

MIT — see [LICENSE](LICENSE).
