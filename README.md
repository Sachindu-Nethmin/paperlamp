# PaperLamp

Turn a research paper PDF into a narrated, captioned explainer video, **entirely offline**.
A small local model (via [Ollama](https://ollama.com)) writes the script, every number is
checked against the paper, and a "document camera" moves across the real PDF pages while a
highlighter sweeps along the lines being explained, like a lamp held over the page. The video
ends with a short self-check quiz. No internet connection or external AI service is used at
any point.

_Formerly "paper2video". Renamed because two research projects already use that name
(Show Lab's Paper2Video / PaperTalker, and the code of Preacher)._

![stages](docs/ui.png)

## What it does

| Stage | What happens | Local tool |
|---|---|---|
| 1. Read the PDF | text, metadata, a line-level index of every page; sections from the PDF's outline, or from headings checked by font (small-caps headings are joined back, figure labels are ignored) | poppler (`pdftotext`, `pdftohtml`, `pdfinfo`) |
| 2. Find figures and tables | caption detection + region finding, used to frame and outline them | poppler + NumPy |
| 3. Identify the paper | title, authors, venue from the first page, then checked against page 1 (wrong titles and invented authors are replaced or dropped; arXiv preprints are labelled) | Ollama + Python |
| 4. Take notes | per-section facts: key numbers (copied verbatim), claims, method, limitations | Ollama |
| 5. Write the script | chapter by chapter from the notes: intro, the paper, the paper's sections, limitations, takeaway. Each chapter is told which figures and tables it should walk through (with their captions) | Ollama |
| 6. Check every number | each number must occur in the paper; otherwise the model repairs the sentence or it is removed | Python |
| 7. Self-check questions | multiple-choice questions from the notes; every number in a question and its answer is checked; a few end the video, the full set is saved as `quiz.md` | Ollama + Python |
| 8. Match sentences to the page | each sentence → page, lines to highlight, region to frame (figures/tables by label, else shared numbers and rare words) | Python |
| 9. Record narration | macOS system voice, or a clone of **your own** voice with Chatterbox | `say` / Chatterbox |
| 10. Render and assemble | document-camera video (pan/zoom, a highlighter that sweeps along the matched lines as the sentence is spoken, figure outline), quiz cards with thinking time, captions (ASS burned in + SRT), −14 LUFS audio, YouTube description with chapters | Pillow + ffmpeg |

### Two lengths

- **Target minutes** (default 8): sections share a sentence budget, and the finished script is
  trimmed to a hard word budget (about 130 words per minute of video), so the video lands near the
  target.
- **Whole paper**: every section in order (related work and a brief pass over the appendix
  included), each subsection its own chapter sized by how much the paper says there, and every
  figure and table explained where the paper discusses it. Length follows the paper, often 15 to
  30 minutes.

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
python3 -m paperlamp.cli paper.pdf --minutes 8 --voice say
python3 -m paperlamp.cli paper.pdf --whole-paper --voice chatterbox --quiz 3
python3 -m paperlamp.cli --resume <job-id> --from verify     # re-run after editing script.json
```

Outputs land in `jobs/<id>/out/`: `video.mp4`, `captions.srt`, `description.txt`, `script.md`,
`quiz.md` (questions and answer key, e.g. for checking what students understood).

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

## How it compares with other paper-to-video systems

| | Input | What the viewer sees | Fact check | Runs on |
|---|---|---|---|---|
| **PaperLamp** | PDF | the paper's own pages, lines highlighted as they are explained | every number checked against the paper by code; title and authors checked against page 1 | a 16 GB laptop, offline |
| Paper2Video / PaperTalker ([Zhu et al. 2025](https://arxiv.org/abs/2510.05096), preprint) | LaTeX source, author photo and voice | new Beamer slides, a cursor, the author as a talking head | prompt instructions only | cloud LLM (GPT-4.1 recommended) and a 48 GB GPU |
| PresentAgent ([Shi et al. 2025](https://arxiv.org/abs/2507.04036)) | document | new slides with narration | none reported | cloud LLM |
| Preacher ([arXiv 2508.09632](https://arxiv.org/abs/2508.09632)) | paper | generated video segments (a "video abstract") | none reported | video generation models |
| NotebookLM Video Overviews ([Google, 2025](https://blog.google/innovation-and-ai/models-and-research/google-labs/notebooklm-video-overviews-studio-upgrades/)) | documents | narrated slides made from the sources | not published | Google cloud |

The difference is who it is for: PaperTalker presents a paper *as its author* (its scores reward
remembering the author's face). PaperLamp explains a paper to a reader who didn't write it, so it
shows the source instead of redrawing it, and it never animates anyone's face.

Ideas taken from that work, with thanks:
- the end-of-video quiz follows Paper2Video's **PresentQuiz** metric (questions generated from the
  paper), turned into a self-check for people;
- the highlighter sweep is backed by their cursor ablation, where a visible cursor raised an AI
  viewer's localisation accuracy from 0.084 to 0.633 (measured with an AI viewer, not people);
- a hard word budget, like their 50-words-per-slide cap, keeps target-length videos on target.

## Claude Code skills

`.claude/skills/` contains skills for working on this repo with Claude Code:

- **paper-to-video**: run, resume and troubleshoot the pipeline
- **paper-script-review**: have Claude fact-check and improve a local-model script, then re-render
- **choose-local-model**: benchmark installed Ollama models and pick the default
- **compare-scripts**: compare two scripts for the same paper and write up the result

## Accuracy and responsible use

- **Numbers:** a number that doesn't appear in the paper never reaches the video (repaired or removed).
  Labels like "Figure 3" are ignored. Derived arithmetic in an imported script is reported, not removed.
- **Limitations** get their own chapter, built only from what the authors state.
- **Quiz questions** go through the same number check; malformed or unsupported questions are dropped,
  not repaired. Review `quiz.md` before using it for assessment.
- **The paper's pages are shown on screen.** Check the paper's license (e.g. CC BY) before publishing and
  credit it; the description is generated with the citation.
- **Voice cloning:** only clone a voice you have permission to use. Chatterbox output carries Resemble
  AI's inaudible watermark. YouTube's disclosure rules list cloning *your own* voice for voiceovers as
  not requiring disclosure; check the current rules for your case.
- Small local models still write flatter, less insightful scripts than a frontier model. Use the
  `paper-script-review` skill or edit `script.json` before publishing.

## Layout

```
app.py            web UI server (standard library only)      ui/index.html   the UI
paperlamp/pdf.py        text, sections, figure/table detection     paperlamp/llm.py      Ollama client
paperlamp/skills/*.md   prompt templates for each model task       paperlamp/analyze.py  per-section notes
paperlamp/script.py     chapter outline + narration                paperlamp/verify.py   number checking
paperlamp/align.py      sentence → page/lines/region               paperlamp/render.py   document camera
paperlamp/tts.py        say / Chatterbox backends                  paperlamp/voice_server.py  Chatterbox server
paperlamp/assemble.py   timeline, audio, captions, final mux       paperlamp/pipeline.py stages, state, resume
paperlamp/compare.py    script comparison metrics                  paperlamp/quiz.py     self-check questions
bench/                  model benchmark, exports                   tests/                unit tests
```

## License

MIT, see [LICENSE](LICENSE).
