#!/usr/bin/env python3
"""Chatterbox voice-cloning server for paperlamp (runs in the Python that has chatterbox-tts).

Speaks a JSON-lines protocol on stdin/stdout so the model loads once per run:
    <-  {"text": "...", "out": "/tmp/a.wav", "exaggeration": 0.4, "cfg_weight": 0.5}
    ->  {"ok": true, "dur": 1.21, "sr": 24000}

Start:  <python-with-chatterbox> voice_server.py <reference.wav>
The reference should be 7-20 s of clean single-speaker speech of a voice you
have permission to clone (your own). Output carries Resemble's Perth watermark.

On Apple silicon the MPS allocator keeps every outgrown KV-cache buffer, so a
single sentence can grow past 19 GB. The caller sets
PYTORCH_MPS_LOW_WATERMARK_RATIO=0.25 and this server frees the cache after
every utterance; together they keep peak memory around 8 GB.
"""
from __future__ import annotations

import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("TQDM_DISABLE", "1")      # per-token sampling bars flood the log


# The protocol owns a private duplicate of fd 1; the real stdout is then pointed
# at stderr so that model-loading chatter from torch/perth/chatterbox — some of
# it printed from C, below Python's reach — cannot corrupt the wire.
_WIRE = os.fdopen(os.dup(1), "w")
os.dup2(2, 1)
sys.stdout = sys.stderr


def _reply(**kw) -> None:
    """Emit one protocol line on the private wire."""
    _WIRE.write(json.dumps(kw) + "\n")
    _WIRE.flush()


def _log(msg: str) -> None:
    print(f"[voice-clone] {msg}", file=sys.stderr, flush=True)


def _pick_device(torch) -> str:
    """Prefer Apple's GPU, then CUDA, then CPU."""
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _patch_torch_load(torch, device: str) -> None:
    """Chatterbox ships CUDA-serialized checkpoints; without a default
    map_location they fail to load on Apple silicon."""
    original = torch.load

    def patched(*args, **kwargs):
        kwargs.setdefault("map_location", torch.device(device))
        return original(*args, **kwargs)

    torch.load = patched


def _free(torch, device: str) -> None:
    """Hand cached GPU blocks back to the OS between utterances."""
    import gc
    gc.collect()
    if device == "mps":
        torch.mps.empty_cache()
    elif device == "cuda":
        torch.cuda.empty_cache()


def main() -> int:
    if len(sys.argv) < 2:
        _reply(ok=False, error="usage: voice_clone_server.py <reference.wav>")
        return 2
    ref = sys.argv[1]
    if not os.path.exists(ref):
        _reply(ok=False, error=f"reference wav not found: {ref}")
        return 2

    import torch
    import torchaudio

    device = os.environ.get("PAPERLAMP_VOICE_DEVICE") or _pick_device(torch)
    _patch_torch_load(torch, device)

    from chatterbox.tts import ChatterboxTTS

    _log(f"loading model on {device} (first run downloads ~1GB of weights)")
    try:
        model = ChatterboxTTS.from_pretrained(device=device)
    except Exception as e:                      # MPS gaps → retry on CPU
        if device == "cpu":
            _reply(ok=False, error=f"model load failed: {e}")
            return 1
        _log(f"{device} load failed ({e}); falling back to CPU")
        device = "cpu"
        _patch_torch_load(torch, device)
        model = ChatterboxTTS.from_pretrained(device=device)

    # Embed the target voice once — every later generate() reuses these conds.
    exaggeration = float(os.environ.get("PAPERLAMP_VOICE_EXAGGERATION", "0.4"))
    model.prepare_conditionals(ref, exaggeration=exaggeration)
    _log(f"ready — voice reference: {os.path.basename(ref)}")
    _reply(ok=True, ready=True, sr=model.sr, device=device)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            _reply(ok=False, error=f"bad request: {e}")
            continue
        if req.get("quit"):
            break

        text, out = req.get("text", "").strip(), req.get("out")
        if not text or not out:
            _reply(ok=False, error="request needs both 'text' and 'out'")
            continue
        try:
            with torch.inference_mode():
                wav = model.generate(
                    text,
                    exaggeration=float(req.get("exaggeration", exaggeration)),
                    cfg_weight=float(req.get("cfg_weight", 0.5)),
                    temperature=float(req.get("temperature", 0.7)),
                )
            wav = wav.detach().cpu()
            torchaudio.save(out, wav, model.sr)
            _reply(ok=True, dur=wav.shape[-1] / model.sr, sr=model.sr)
            del wav
            _free(torch, device)
        except Exception as e:
            _reply(ok=False, error=f"{type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
