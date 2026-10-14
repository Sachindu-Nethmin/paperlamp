"""arXiv: a paper's details, its PDF and its LaTeX source, from a link or an ID.

Playlists are made from pasted arXiv links, so this reads arXiv's public API for what the
Library shows (title, every author, the date the paper first appeared, the comment that often
names the venue) and downloads the PDF. In-depth videos also use the LaTeX source arXiv keeps
for most papers: it holds every equation exactly as the authors typed it, which the PDF's text
layer garbles. Nothing here guesses: a field arXiv does not give stays empty.
"""
import gzip, io, json, pathlib, re, shutil, tarfile, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET

API = "https://export.arxiv.org/api/query"
UA = {"User-Agent": "paperlamp/1.0 (offline paper explainer; playlist import)"}
NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
# new-style IDs (2310.06770, 2310.06770v3) and old-style ones (cs/0601001)
ID = re.compile(r"(?<![\w.])(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?(?![\d])")


def ids(text):
    """Every arXiv ID in pasted text (links to abs/, pdf/ or html/ pages, or bare IDs), in order,
    without versions and without repeats."""
    out = []
    for m in ID.finditer(text or ""):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def _get(url, timeout=60, tries=3, deadline=900):
    """The body of url. timeout is per read; deadline caps the whole download, since a stalled
    connection that trickles a few bytes a minute never trips a per-read timeout."""
    last = None
    for k in range(tries):
        try:
            t0, parts = time.time(), []
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                while chunk := r.read(1 << 16):
                    parts.append(chunk)
                    if time.time() - t0 > deadline:
                        raise TimeoutError(f"still downloading after {deadline} s")
            return b"".join(parts)
        except Exception as e:                      # arXiv asks for a pause between retries
            last = e
            time.sleep(3 * (k + 1))
    raise RuntimeError(f"could not reach arXiv ({url}): {last}")


def metadata(id_list):
    """{id: {arxiv, version, title, authors, published, updated, comment, journal_ref, doi,
    category, abs_url, pdf_url}} for each ID arXiv knows. published is the first version's
    date (YYYY-MM-DD): the date the paper was first made public."""
    out = {}
    for k in range(0, len(id_list), 20):
        part = id_list[k:k + 20]
        q = urllib.parse.urlencode({"id_list": ",".join(part), "max_results": len(part)})
        root = ET.fromstring(_get(f"{API}?{q}"))
        for e in root.findall("a:entry", NS):
            full = (e.findtext("a:id", "", NS) or "").rsplit("/abs/", 1)[-1]
            m = re.match(r"(.+?)(v\d+)?$", full)
            aid, ver = m.group(1), (m.group(2) or "")
            title = " ".join((e.findtext("a:title", "", NS) or "").split())
            if not aid or title.lower() == "error":
                continue
            text = lambda tag: " ".join((e.findtext(tag, "", NS) or "").split())
            prim = e.find("x:primary_category", NS)
            out[aid] = dict(arxiv=aid, version=ver, title=title,
                            authors=[" ".join(a.findtext("a:name", "", NS).split()) for a in e.findall("a:author", NS)],
                            published=text("a:published")[:10], updated=text("a:updated")[:10],
                            comment=text("x:comment"), journal_ref=text("x:journal_ref"), doi=text("x:doi"),
                            category=prim.get("term") if prim is not None else "",
                            abstract=text("a:summary"),
                            abs_url=f"https://arxiv.org/abs/{aid}", pdf_url=f"https://arxiv.org/pdf/{aid}")
        if k + 20 < len(id_list):
            time.sleep(3)                           # arXiv's API terms: one request every 3 seconds
    return out


def venue_hint(comment, journal_ref=""):
    """Where the paper says it appears ("To appear in ESEM 2026", "ICLR 2024"), or "".
    Only what arXiv's own fields say; never inferred."""
    if journal_ref:
        return journal_ref
    m = re.search(r"(?:to appear in|accepted (?:at|to|by)|published (?:at|in)|in proceedings of)\s+(?:the\s+)?"
                  r"(?:proceedings of (?:the\s+)?)?([A-Z][\w\-&' ]{1,60}?\d{4})", comment or "", re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"\b((?:ICLR|NeurIPS|ICML|ACL|EMNLP|NAACL|COLM|AAAI|IJCAI|ICSE|FSE|ESEC/FSE|ASE|ISSTA|MSR|"
                  r"ESEM|SANER|ICSME|TOSEM|TSE|EMSE|CVPR|ICCV|ECCV|KDD|WWW|CHI)\s*'?\d{2,4})\b", comment or "")
    return m.group(1) if m else ""


def download_pdf(aid, dest):
    data = _get(f"https://export.arxiv.org/pdf/{aid}", timeout=180)       # arXiv's host for programs
    if not data.startswith(b"%PDF"):
        raise RuntimeError(f"arXiv sent no PDF for {aid}")
    pathlib.Path(dest).write_bytes(data)
    return dest


def source(aid, dest):
    """The paper's LaTeX source, unpacked into dest; dest, or None when arXiv has none (a
    PDF-only submission) or cannot be reached. Kept once fetched."""
    dest = pathlib.Path(dest)
    if dest.exists() and any(dest.rglob("*.tex")):
        return dest
    if not aid:
        return None
    try:
        data = _get(f"https://export.arxiv.org/e-print/{aid}", timeout=180, tries=2)
    except RuntimeError:
        return None
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    try:
        raw = gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data
    except OSError:
        raw = data
    if raw.startswith(b"%PDF"):                      # the authors uploaded only a PDF
        shutil.rmtree(dest, ignore_errors=True)
        return None
    try:
        with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
            for m in tf.getmembers():
                target = (dest / m.name).resolve()
                if not str(target).startswith(str(dest.resolve())) or not (m.isfile() or m.isdir()):
                    continue                          # never write outside the folder
                if m.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif m.name.lower().endswith((".tex", ".sty", ".cls", ".bbl", ".def", ".ltx")):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(tf.extractfile(m).read())
    except tarfile.TarError:                          # a single gzipped .tex file
        (dest / "main.tex").write_bytes(raw)
    if not any(dest.rglob("*.tex")):
        shutil.rmtree(dest, ignore_errors=True)
        return None
    (dest / ".paperlamp-source.json").write_text(json.dumps(dict(arxiv=aid, fetched=time.strftime("%Y-%m-%d"))))
    return dest
