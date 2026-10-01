---
name: paper-to-video
description: Turn a research paper PDF into a narrated, captioned explainer video with this repo's offline pipeline (local Ollama model, PDF document-camera visuals, local voice). Use when the user wants a video, script or YouTube explainer made from a paper, or asks to run, resume or debug a PaperLamp job.
---

# Paper → explainer video (offline)

Everything runs locally: Ollama (script), poppler (PDF), Pillow + ffmpeg (video),
macOS `say` or Chatterbox (voice). No internet or external AI API is used.

## Run it

- Web UI: `python3 app.py` → open http://127.0.0.1:8765, drop the PDF, press **Make video**.
  Each of the 9 stages has its own progress bar; outputs appear under the player.
- CLI: `python3 -m paperlamp.cli paper.pdf --minutes 8 [--voice chatterbox --chatterbox-python PY --reference WAV]`
- Resume or redo part of a job: `python3 -m paperlamp.cli --resume <job-id> --from <stage>`
  Stages: parse, figures, meta, notes, script, verify, align, voice, render.

Outputs: `jobs/<id>/out/video.mp4`, `captions.srt`, `description.txt`, `script.md`,
and `jobs/<id>/script.json` (editable).

## Rules to keep

1. Never state a number the paper doesn't contain. The `verify` stage enforces this; if you
   edit `script.json` by hand, re-run from `verify` (the web UI does this when a script is saved).
2. Keep the Limitations chapter. A video without the paper's caveats misrepresents it.
3. The visuals are the paper's own pages. Check the paper's license before publishing
   (the tool shows its best guess in `doc.json` → `license`); credit the paper in the description.
4. Only clone a voice you have permission to use (normally your own).

## Troubleshooting

- "Ollama is not running": start the Ollama app; `curl 127.0.0.1:11434/api/tags` should list models.
- Chatterbox very slow / Mac swapping: the tool already sets `PYTORCH_MPS_LOW_WATERMARK_RATIO=0.25`
  and restarts the voice server every 6 new sentences; don't run other GPU jobs (e.g. image
  models) at the same time on a 16 GB machine.
- A sentence shows the wrong part of the page: that is the `align` stage matching by shared
  numbers/words. Mention the figure or table by label in the sentence ("Table 5 shows…") to pin it.
