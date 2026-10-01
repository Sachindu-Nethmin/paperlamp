"""Memory housekeeping for a 16 GB laptop.

PaperLamp's heavy parts are the local language model (Ollama, about 6-7 GB while
loaded), the voice-cloning model (Chatterbox, several GB on the GPU) and the render
workers (a few hundred MB each). The app runs one job at a time, and within a job
one of these at a time; this module frees whatever a finished step left behind and
sizes the render pool to the memory that is actually free.
"""
import os, re, signal, subprocess

from . import llm

VOICE = "paperlamp/voice_server.py"


def _run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return ""


def total_gb():
    out = _run(["sysctl", "-n", "hw.memsize"]).strip()
    return round(int(out) / 2 ** 30, 1) if out.isdigit() else 0.0


def available_gb():
    """Memory the system could hand out now: free + inactive + speculative + purgeable pages."""
    out = _run(["vm_stat"])
    size = re.search(r"page size of (\d+) bytes", out)
    size = int(size.group(1)) if size else 16384
    pages = 0
    for key in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"):
        m = re.search(rf"{key}:\s+(\d+)", out)
        pages += int(m.group(1)) if m else 0
    return round(pages * size / 2 ** 30, 1)


def swap_used_gb():
    m = re.search(r"used = ([\d.]+)M", _run(["sysctl", "-n", "vm.swapusage"]))
    return round(float(m.group(1)) / 1024, 1) if m else 0.0


def top_processes(k=5):
    """The biggest memory users on the machine (name, GB), so the user can see what
    else is competing (a VM, browser tabs ...)."""
    rows = []
    for line in _run(["ps", "-Ao", "rss=,comm="]).splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            name = os.path.basename(parts[1]).replace("com.apple.", "")
            rows.append((name, int(parts[0]) / 2 ** 20))
    merged = {}
    for name, gb in rows:
        merged[name] = merged.get(name, 0) + gb
    return [dict(name=n, gb=round(g, 1)) for n, g in sorted(merged.items(), key=lambda x: -x[1])[:k] if g >= 0.3]


def voice_servers():
    out = _run(["pgrep", "-f", VOICE])
    return [int(p) for p in out.split() if p.isdigit() and int(p) != os.getpid()]


def stop_voice_servers():
    pids = voice_servers()
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    return len(pids)


def status():
    return dict(total_gb=total_gb(), available_gb=available_gb(), swap_used_gb=swap_used_gb(),
                ollama=[dict(name=n, gb=round(b / 1e9, 1)) for n, b in llm.loaded()],
                voice_server=bool(voice_servers()), top=top_processes())


def free_all():
    """Unload every Ollama model and stop any voice server; returns what was freed."""
    return dict(ollama_gb=llm.unload_all(), voice_servers=stop_voice_servers())


def render_workers(max_workers=4, per_worker_gb=0.8):
    """Parallel render workers the free memory can take (each holds page images and an encoder)."""
    return max(1, min(max_workers, int(available_gb() / per_worker_gb)))
