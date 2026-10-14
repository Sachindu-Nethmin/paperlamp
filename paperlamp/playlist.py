"""Playlists: a set of papers, each with its video, shown in the order the papers first appeared.

A playlist is made from pasted arXiv links (or IDs). Each paper's details come from arXiv (title,
every author, the date of its first version, the comment that often names the venue, its
license), its PDF is downloaded and a job is made for it with the playlist's kind of video, but
not started: "Make all" queues every paper whose license allows a video, in playlist order. A
paper that already has a job of that kind is linked instead; one that has a job of another kind
gets a new job that reuses its parsed text, figures and title (Job.derive).

A line may give a second link to a PDF to use instead of arXiv's copy, for example the journal's
open-access version: "2212.01479 https://link.springer.com/content/pdf/10.1007/....pdf".

Stored in playlists.json in the app's data folder; job folders are not changed by it.
"""
import json, pathlib, re, shutil, threading, time, urllib.request, uuid

from . import arxiv, pdf, pipeline, youtube

LOCK = threading.RLock()
ORDERS = ("paper_date", "paper_date_desc", "made", "added")


def path():
    return pipeline.HOME / "playlists.json"


def load():
    with LOCK:
        try:
            data = json.loads(path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data.setdefault("playlists", [])
        return data


def save(data):
    with LOCK:
        path().parent.mkdir(parents=True, exist_ok=True)
        tmp = path().with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path())


def update(pid, fn):
    """Change one playlist under the lock: fn(playlist) edits it in place."""
    with LOCK:
        data = load()
        pl = next((p for p in data["playlists"] if p["id"] == pid), None)
        if pl is None:
            return None
        fn(pl)
        save(data)
        return pl


def get(pid):
    return next((p for p in load()["playlists"] if p["id"] == pid), None)


def create(name, depth="deep", settings=None, description=""):
    pl = dict(id=time.strftime("pl-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4], name=(name or "New playlist").strip()[:120],
              description=description.strip()[:2000], created=time.time(), depth=depth, order="paper_date",
              settings=dict(settings or {}), items=[])
    with LOCK:
        data = load()
        data["playlists"].append(pl)
        save(data)
    return pl


def lines_of(text):
    """[(arxiv id, PDF link or "")] for each pasted line that names an arXiv paper."""
    out, seen = [], set()
    for line in (text or "").splitlines():
        found = arxiv.ids(line)
        if not found:
            continue
        other = [u for u in re.findall(r"https?://\S+", line) if "arxiv.org" not in u]
        for aid in found:
            if aid not in seen:
                seen.add(aid)
                out.append((aid, other[0] if other and len(found) == 1 else ""))
    return out


def add(pid, text, config, on_ready=None):
    """Add the papers named in text: they appear at once as "fetching", and a background thread
    fills in their details, downloads them and makes their jobs. Returns the IDs added."""
    pl = get(pid)
    if not pl:
        raise KeyError(pid)
    have = {it["arxiv"] for it in pl["items"]}
    todo = [(a, u) for a, u in lines_of(text) if a not in have]
    if not todo:
        return []

    def put(p):
        for a, u in todo:
            p["items"].append(dict(key=f"arxiv:{a}", arxiv=a, pdf_url=u, status="fetching", added=time.time(),
                                   title=f"arXiv {a}", authors=[], published="", job=None, error=""))
    update(pid, put)
    threading.Thread(target=fetch_all, args=(pid, todo, config, on_ready), daemon=True).start()
    return [a for a, _ in todo]


def set_item(pid, aid, **fields):
    def fn(p):
        for it in p["items"]:
            if it["arxiv"] == aid:
                it.update(fields)
    update(pid, fn)


def fetch_all(pid, todo, config, on_ready=None):
    try:
        metas = arxiv.metadata([a for a, _ in todo])
    except RuntimeError as e:
        for a, _ in todo:
            set_item(pid, a, status="error", error=str(e)[:300])
        return
    for a, url in todo:
        m = metas.get(a)
        if not m:
            set_item(pid, a, status="error", error="arXiv has no paper with this number")
            continue
        set_item(pid, a, title=m["title"], authors=m["authors"], published=m["published"], updated=m["updated"],
                 comment=m["comment"], venue=arxiv.venue_hint(m["comment"], m["journal_ref"]), doi=m["doi"],
                 abs_url=m["abs_url"], version=m["version"])
        try:
            fetch_one(pid, a, url, m, config)
        except Exception as e:                          # one paper failing never stops the others
            set_item(pid, a, status="error", error=f"{type(e).__name__}: {e}"[:300])
        else:
            if on_ready:
                on_ready(pid, a)
        time.sleep(1)                                  # be gentle with arXiv


def same_paper_jobs(aid, title):
    """Jobs of this paper, newest first: by arXiv number, else by exact title."""
    out = []
    norm = lambda s: re.sub(r"[^a-z0-9]", "", str(s or "").lower())
    for d in sorted(pipeline.JOBS.glob("*/state.json"), reverse=True):
        try:
            st = json.loads(d.read_text(encoding="utf-8"))
            doc = json.loads((d.parent / "doc.json").read_text(encoding="utf-8")) if (d.parent / "doc.json").exists() else {}
            meta = json.loads((d.parent / "meta.json").read_text(encoding="utf-8")) if (d.parent / "meta.json").exists() else {}
        except (OSError, ValueError):
            continue
        ax = doc.get("arxiv") or (st.get("arxiv") or {}).get("arxiv") or pdf.arxiv_id(str(doc.get("license") or ""))
        if (ax and ax == aid) or (title and norm(meta.get("title")) == norm(title)):
            out.append(st)
    return out


def paper_dir(aid):
    """Where a playlist keeps a paper's PDF and LaTeX source until its job is made."""
    return pipeline.HOME / "papers" / f"arxiv-{aid.replace('/', '_')}"


def fetch_one(pid, aid, url, m, config):
    """The paper's license, PDF and (for in-depth videos) LaTeX source. No job yet: jobs are
    made when the playlist is started, so a stopped or older app never sees half a playlist."""
    pl = get(pid)
    depth = pl["depth"]
    folder = paper_dir(aid)
    folder.mkdir(parents=True, exist_ok=True)
    jobs = [] if url else same_paper_jobs(aid, m["title"])
    mine = next((st for st in jobs if (st.get("settings") or {}).get("depth") == depth), None)
    base = next((st for st in jobs if all((st.get("stages") or {}).get(k, {}).get("status") == "done"
                                          for k in ("parse", "figures", "meta"))), None)
    if url:                                            # the journal's own open-access copy
        req = urllib.request.Request(url, headers=arxiv.UA)
        data = urllib.request.urlopen(req, timeout=180).read()
        if not data.startswith(b"%PDF"):
            raise RuntimeError(f"{url} did not give a PDF")
        (folder / "paper.pdf").write_bytes(data)
        lic = pdf.license_hint("\n".join(pdf.page_texts(folder / "paper.pdf")))
    else:
        lic = pdf.arxiv_license(aid) or ""
        if not mine and not base and not (folder / "paper.pdf").exists():     # a job of it has the PDF already
            arxiv.download_pdf(aid, folder / "paper.pdf")
    if depth == "deep" and not mine:                   # the exact equations, fetched now while online
        arxiv.source(aid, folder / "src")
    (folder / "arxiv.json").write_text(json.dumps(dict(m, pdf_url=url or m["pdf_url"]), indent=1), encoding="utf-8")
    set_item(pid, aid, license=lic, allowed=pdf.video_allowed(lic), license_from="the PDF" if url else "arXiv",
             status="ready", **({"job": mine["id"], "linked": True} if mine else {}))


def ensure_job(pid, it, config):
    """The paper's job, made now if it has none: a new job of the playlist's kind that reuses an
    existing job's parsed text, figures and title when there is one, else one from the PDF."""
    if it.get("job") and (pipeline.JOBS / it["job"] / "state.json").exists():
        return it["job"]
    pl = get(pid)
    depth, extra = pl["depth"], pl.get("settings") or {}
    folder = paper_dir(it["arxiv"])
    m = json.loads((folder / "arxiv.json").read_text(encoding="utf-8")) if (folder / "arxiv.json").exists() else {}
    jobs = [] if it.get("pdf_url") else same_paper_jobs(it["arxiv"], it.get("title"))
    mine = next((st for st in jobs if (st.get("settings") or {}).get("depth") == depth), None)
    if mine:
        set_item(pid, it["arxiv"], job=mine["id"], linked=True)
        return mine["id"]
    base = next((st for st in jobs if all((st.get("stages") or {}).get(k, {}).get("status") == "done"
                                          for k in ("parse", "figures", "meta"))), None)
    settings = dict({k: v for k, v in (config or {}).items() if k in pipeline.DEFAULTS},
                    **{k: v for k, v in extra.items() if k in pipeline.DEFAULTS}, depth=depth)
    if base:                                           # another kind exists: reuse its text, figures and title
        job = pipeline.Job.derive(pipeline.Job(base["id"]), depth)
        job.state["settings"].update({k: v for k, v in settings.items() if k != "permission"})
    else:
        if not (folder / "paper.pdf").exists():
            arxiv.download_pdf(it["arxiv"], folder / "paper.pdf")
        job = pipeline.Job.create((folder / "paper.pdf").read_bytes(), it.get("title") or it["arxiv"], settings)
    if (folder / "src").exists() and not (job.dir / "src").exists():
        shutil.copytree(folder / "src", job.dir / "src")
    if it.get("license_from") == "arXiv" and not (job.dir / "arxiv-license.txt").exists():
        # what arXiv said when the paper was added, so the job's license check needs no second lookup
        (job.dir / "arxiv-license.txt").write_text((it.get("license") or "none") + "\n", encoding="utf-8")
    job.state.update(arxiv=m or dict(arxiv=it["arxiv"]), playlist=pid)
    job.save(force=True)
    if (job.dir / "meta.json").exists():              # a reused title page that only says "arXiv"
        job.write("meta.json", pipeline.arxiv_venue(job.read("meta.json"), m))
    job.log(f"made for the playlist \"{pl['name']}\" from arXiv {it['arxiv']}"
            + (f" (PDF from {it['pdf_url']})" if it.get("pdf_url") else "") + (f", reusing job {base['id']}" if base else ""))
    set_item(pid, it["arxiv"], job=job.id)
    return job.id


# ── what the Library shows ────────────────────────────────────────────────────
def job_status(jid, running=None, queued=lambda j: 0):
    d = pipeline.JOBS / str(jid)
    try:
        st = json.loads((d / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return dict(state="missing")
    stages = list((st.get("stages") or {}).values())
    overall = sum(1 if s["status"] in ("done", "skipped") else s.get("progress", 0) for s in stages) / max(len(stages), 1)
    done = bool(stages) and all(s["status"] in ("done", "skipped") for s in stages) and (d / "out/video.mp4").exists()
    now = next((s for s in stages if s["status"] == "running"), None)
    state = ("done" if done else "license" if st.get("license_stop") else "error" if st.get("error") else
             "making" if running == jid else "waiting" if queued(jid) else "not started" if overall == 0 else "paused")
    yt = d / "out/youtube/thumbnail.png"
    meta = {}
    if (d / "meta.json").exists():
        try:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        except ValueError:
            pass
    return dict(state=state, progress=round(overall, 4), stage=now["label"] if now else "", msg=(now or {}).get("msg", ""),
                error=st.get("error"), license_stop=st.get("license_stop"), made=st.get("created"),
                seconds=(st.get("outputs") or {}).get("seconds"), depth=(st.get("settings") or {}).get("depth"),
                thumbnail=f"/jobs/{jid}/out/youtube/thumbnail.png" if yt.exists() else None,
                video=f"/jobs/{jid}/out/video.mp4" if done else None, queued=queued(jid),
                venue=meta.get("venue", ""), exported_to=st.get("exported_to"),
                permission=bool(((st.get("settings") or {}).get("permission") or "").strip()))


def paper_date(it):
    return it.get("published") or (f"{it['year']}-12-31" if it.get("year") else "")


def ordered(items, order):
    """Oldest paper first (the default), newest first, the order the videos were made, or the
    order the papers were added. Papers with no date go last."""
    if order == "made":
        return sorted(items, key=lambda it: ((it.get("status_info") or {}).get("made") or float("inf"), paper_date(it)))
    if order == "added":
        return sorted(items, key=lambda it: it.get("added") or 0)
    dated = sorted([it for it in items if paper_date(it)], key=lambda it: (paper_date(it), it.get("title", "")),
                   reverse=order == "paper_date_desc")
    return dated + [it for it in items if not paper_date(it)]


def view(pid, running=None, queued=lambda j: 0):
    """The playlist with each paper's video state, in the playlist's order, and totals."""
    pl = get(pid)
    if not pl:
        return None
    items = []
    for it in pl["items"]:
        it = dict(it)
        it["status_info"] = job_status(it["job"], running, queued) if it.get("job") else dict(state=it.get("status", ""))
        if it["status_info"].get("venue") and not it.get("venue"):
            it["venue"] = it["status_info"]["venue"]
        items.append(it)
    items = ordered(items, pl.get("order", "paper_date"))
    secs = sum((it["status_info"].get("seconds") or 0) for it in items)
    counts = {}
    for it in items:
        counts[it["status_info"].get("state", "")] = counts.get(it["status_info"].get("state", ""), 0) + 1
    return dict(pl, items=items, seconds=secs, counts=counts)


def summaries(running=None, queued=lambda j: 0):
    out = []
    for pl in load()["playlists"]:
        v = view(pl["id"], running, queued)
        out.append(dict(id=v["id"], name=v["name"], depth=v["depth"], created=v["created"], n=len(v["items"]),
                        seconds=v["seconds"], counts=v["counts"],
                        span=[min(dates), max(dates)] if (dates := [it["published"] for it in v["items"]
                                                                    if it.get("published")]) else [],
                        thumbs=[it["status_info"].get("thumbnail") for it in v["items"] if it["status_info"].get("thumbnail")][:4]))
    return out


def make_all(pid, add_job, config=None, running=None, queued=lambda j: 0, only=None):
    """Queue every paper whose license allows its video (or that has permission recorded), in
    playlist order; only: just the paper with this arXiv ID. Returns (queued ids, skipped [(title, why)])."""
    v = view(pid, running, queued)
    started, skipped = [], []
    for it in v["items"]:
        if only and it["arxiv"] != only:
            continue
        si = it["status_info"]
        if it.get("status") != "ready" and not it.get("job"):
            skipped.append((it["title"], "still being fetched" if it.get("status") == "fetching" else it.get("error") or "not fetched"))
            continue
        if not (it.get("allowed") or si.get("permission")) and not it.get("job"):
            skipped.append((it["title"], f"its license ({it.get('license') or 'none found'}) does not allow a video"))
            continue
        if not it.get("job"):
            it["job"] = ensure_job(pid, it, config)
            si = job_status(it["job"], running, queued)
        if si["state"] == "done":
            continue
        elif not (it.get("allowed") or si.get("permission")):
            skipped.append((it["title"], f"license ({it.get('license') or 'none found'}) does not allow a video; "
                                         "open the job to record permission"))
        elif si["state"] in ("making", "waiting"):
            started.append(it["job"])
        else:
            job = pipeline.Job(it["job"])
            if si["state"] == "error" and not si.get("license_stop"):
                bad = next((k for k, s in job.state["stages"].items() if s["status"] == "error"), None)
                if bad:
                    job.reset_from(bad)
            add_job(it["job"])
            started.append(it["job"])
    return started, skipped


def export(pid, root):
    """A folder for the whole playlist: every finished video numbered in playlist order, its
    thumbnail and captions, and the playlist's own title and description for YouTube."""
    v = view(pid)
    folder = pathlib.Path(root).expanduser() / re.sub(r'[/\\:*?"<>|\x00-\x1f]', "", v["name"]).strip(" .")
    folder.mkdir(parents=True, exist_ok=True)
    lines, n, files = [], 0, 0
    for it in v["items"]:
        n += 1
        si = it["status_info"]
        d = pipeline.JOBS / str(it.get("job"))
        when = it.get("published", "")
        info = {}
        if (d / "out/youtube/youtube.json").exists():
            info = json.loads((d / "out/youtube/youtube.json").read_text(encoding="utf-8"))
        title = info.get("title") or it["title"]
        lines.append(f"{n}. {title}" + (f" (first posted {when})" if when else "") +
                     (f"\n   {it.get('abs_url')}" if it.get("abs_url") else ""))
        if si.get("state") != "done":
            lines[-1] += "\n   (video not made yet)"
            continue
        stem = f"{n:02d} - " + re.sub(r'[/\\:*?"<>|\x00-\x1f]', "", title.replace(" | ", " - ")).strip(" .")[:140]
        for src, name in ((d / "out/video.mp4", f"{stem}.mp4"), (d / "out/youtube/thumbnail.png", f"{stem} - Thumbnail.png"),
                          (d / "out/captions.srt", f"{stem} - Captions.srt"),
                          (d / "out/youtube/details.txt", f"{stem} - YouTube details.txt"),
                          (d / "out/glossary.md", f"{stem} - Glossary.md")):
            if src.exists():
                youtube.clone(src, folder / name)
                files += 1
    total = v["seconds"]
    head = [v["name"], "", "PLAYLIST DESCRIPTION",
            (v.get("description") or f"{len(v['items'])} research papers, each explained in full, in the order "
             "they were first published.") + (f" Total length about {total / 3600:.1f} hours." if total > 3600 else ""),
            "", "VIDEOS, IN PLAYLIST ORDER", *lines]
    (folder / "Playlist - YouTube details.txt").write_text("\n".join(head) + "\n", encoding="utf-8")
    return dict(folder=str(folder), files=files)


def remove(pid, aid):
    return update(pid, lambda p: p.__setitem__("items", [it for it in p["items"] if it["arxiv"] != aid]))


def delete(pid):
    with LOCK:
        data = load()
        data["playlists"] = [p for p in data["playlists"] if p["id"] != pid]
        save(data)


def permission(pid, aid, note, config):
    """The authors or publisher allowed the video: make the paper's job and record who and when
    (it goes into the description, as for any job)."""
    pl = get(pid)
    it = next((x for x in (pl or {}).get("items", []) if x["arxiv"] == aid), None)
    if not it or it.get("status") != "ready":
        raise KeyError(aid)
    jid = ensure_job(pid, it, config)
    job = pipeline.Job(jid)
    job.state["settings"]["permission"] = note[:500]
    job.state["error"] = None
    job.state.pop("license_stop", None)
    job.save(force=True)
    job.log(f"permission recorded from the playlist: {note[:200]}")
    return jid


def resume(config):
    """Papers still marked "fetching" when the app last stopped are fetched again (the app
    calls this when it starts)."""
    for pl in load()["playlists"]:
        todo = [(it["arxiv"], it.get("pdf_url") or "") for it in pl["items"] if it.get("status") == "fetching"]
        if todo:
            threading.Thread(target=fetch_all, args=(pl["id"], todo, config), daemon=True).start()
