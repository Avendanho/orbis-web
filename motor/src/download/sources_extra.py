"""Fontes adicionais: rotas determinísticas por prefixo de DOI e buscas por título.

* ``try_doi_patterns`` — servidores de preprint e editoras abertas cuja URL do
  PDF se deriva do próprio DOI (arXiv, OSF/PsyArXiv/SocArXiv, Research Square,
  Preprints.org, SSRN, Copernicus, eLife, Zenodo, Figshare, ChemRxiv, ACM, IOP).
* ``try_arxiv_title`` — preprint no arXiv de um artigo publicado com outro DOI.
* ``try_google_scholar`` — links diretos de PDF da página de resultados.
* ``try_cyberleninka`` — repositório aberto de revistas russas.

Mesma assinatura das fontes de ``sources_repositories``: ``(doi=None, *,
title=None, timeout)`` -> ``(urls, meta, registros_brutos)``. Os candidatos
passam pela validação de identidade do PDF baixado, então uma busca por título
que traga o artigo errado é recusada lá.
"""
from __future__ import annotations

import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import identity as _identity
import runtime
from identity import normalize_doi

BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
_TITLE_MATCH = 0.85


def _title_ok(requested: str, found: str | None) -> bool:
    if not found:
        return False
    found = found.strip()
    if found.endswith(("…", "...")):
        stem = found.rstrip("….").strip()
        return len(stem) >= 20 and _identity.normalize_title_text(requested).startswith(
            _identity.normalize_title_text(stem)
        )
    return _identity.title_similarity(requested, found) >= _TITLE_MATCH


def _strip_tags(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()


# ---------------------------------------------------------------------------
# DOI-prefix routes
# ---------------------------------------------------------------------------

def _zenodo_files(record_id: str, timeout: int) -> list[str]:
    data = runtime._get_json(f"https://zenodo.org/api/records/{record_id}", timeout=timeout)
    urls = []
    for f in (data or {}).get("files") or []:
        key = f.get("key") or f.get("filename") or ""
        if key.lower().endswith(".pdf"):
            link = (f.get("links") or {}).get("self") or (f.get("links") or {}).get("download")
            urls.append(link or f"https://zenodo.org/records/{record_id}/files/{urllib.parse.quote(key)}?download=1")
    return urls


def _figshare_files(article_id: str, timeout: int) -> list[str]:
    data = runtime._get_json(f"https://api.figshare.com/v2/articles/{article_id}", timeout=timeout)
    return [
        f["download_url"] for f in (data or {}).get("files") or []
        if f.get("download_url") and (f.get("name") or "").lower().endswith(".pdf")
    ]


def _elife_pdf(number: str, timeout: int) -> list[str]:
    raw = runtime._get(
        f"https://api.elifesciences.org/articles/{number}", accept="*/*", timeout=timeout,
    )
    pdf = (json.loads(raw.decode("utf-8", "replace")) or {}).get("pdf")
    return [pdf] if pdf else []


def _chemrxiv_pdf(doi: str, timeout: int) -> list[str]:
    api = "https://chemrxiv.org/engage/chemrxiv/public-api/v1/items/doi/" + urllib.parse.quote(doi, safe="/")
    try:
        raw = runtime._get(api, timeout=timeout, user_agent=BROWSER_UA)
    except urllib.error.HTTPError as e:
        # The API sits behind Cloudflare, which refuses plain clients.
        if e.code != 403:
            raise
        from bypass403 import bypass_get
        res = bypass_get(api, timeout=timeout, headers={"Accept": "application/json"})
        if not (res.success and res.data):
            return []
        raw = res.data
    item = json.loads(raw.decode("utf-8", "replace")) or {}
    url = ((item.get("asset") or {}).get("original") or {}).get("url")
    return [url] if url else []


def doi_pattern_urls(doi: str) -> list[str]:
    """PDF URLs derivable from the DOI string alone (no network)."""
    d = normalize_doi(doi)
    low = d.lower()
    urls: list[str] = []

    m = re.match(r"^10\.48550/arxiv\.(.+)$", low)
    if m:
        urls.append(f"https://arxiv.org/pdf/{m.group(1)}")

    # OSF-hosted preprint servers (OSF, PsyArXiv, SocArXiv, EdArXiv, ...) all
    # carry the OSF guid in the DOI suffix; versioned DOIs add "_v2".
    m = re.search(r"osf\.io/([a-z0-9]{5,})(?:_v\d+)?$", low)
    if m:
        urls.append(f"https://osf.io/{m.group(1)}/download")

    m = re.match(r"^10\.21203/rs\.3\.(rs-\d+)/(v\d+)$", low)
    if m:
        urls.append(f"https://www.researchsquare.com/article/{m.group(1)}/{m.group(2)}.pdf")

    m = re.match(r"^10\.20944/preprints(\d{6}\.\d{3,5})\.(v\d+)$", low)
    if m:
        urls.append(f"https://www.preprints.org/manuscript/{m.group(1)}/{m.group(2)}/download")

    m = re.match(r"^10\.2139/ssrn\.(\d+)$", low)
    if m:
        urls.append(f"https://papers.ssrn.com/sol3/Delivery.cfm?abstractid={m.group(1)}")

    m = re.match(r"^10\.5194/([a-z]+)-(\d+)-(\d+)-(\d{4})$", low)
    if m:
        j, vol, page, year = m.groups()
        urls.append(f"https://{j}.copernicus.org/articles/{vol}/{page}/{year}/{j}-{vol}-{page}-{year}.pdf")

    if low.startswith("10.1145/"):
        urls.append(f"https://dl.acm.org/doi/pdf/{d}")

    if low.startswith("10.1088/"):
        urls.append(f"https://iopscience.iop.org/article/{d}/pdf")

    return urls


def try_doi_patterns(doi: str | None = None, *, title: str | None = None, timeout: int = 20) -> tuple[list[str], dict, list[dict]]:
    if not doi:
        return [], {}, []
    d = normalize_doi(doi)
    low = d.lower()
    urls = doi_pattern_urls(d)
    t = min(timeout, 10)
    lookups = []
    m = re.match(r"^10\.7554/elife\.(\d+)", low)
    if m:
        lookups.append(lambda: _elife_pdf(m.group(1), t))
    mz = re.match(r"^10\.5281/zenodo\.(\d+)$", low)
    if mz:
        lookups.append(lambda: _zenodo_files(mz.group(1), t))
    mf = re.match(r"^10\.6084/m9\.figshare\.(\d+)", low)
    if mf:
        lookups.append(lambda: _figshare_files(mf.group(1), t))
    if low.startswith("10.26434/chemrxiv"):
        lookups.append(lambda: _chemrxiv_pdf(d, t))
    for lookup in lookups:
        try:
            urls.extend(lookup())
        except Exception as exc:
            runtime._progress("source_miss", source="doi_patterns", reason=str(exc)[:120])
    return list(dict.fromkeys(urls)), {}, []


# ---------------------------------------------------------------------------
# arXiv title search
# ---------------------------------------------------------------------------

def try_arxiv_title(doi: str | None = None, *, title: str | None = None, timeout: int = 20) -> tuple[list[str], dict, list[dict]]:
    # The export API's search_query answers 406; only id_list still works there.
    if not title or len(title.split()) < 3:
        return [], {}, []
    words = re.sub(r"[^\w\s-]", " ", title).split()
    url = "https://arxiv.org/search/?" + urllib.parse.urlencode(
        {"query": f'"{" ".join(words)}"', "searchtype": "title", "size": 50}
    )
    try:
        page = runtime._get(url, accept="text/html", timeout=min(timeout, 12), user_agent=BROWSER_UA).decode("utf-8", "replace")
    except Exception as exc:
        runtime._progress("source_miss", source="arxiv_title", reason=str(exc)[:120])
        return [], {}, []
    entries = []
    for block in page.split('<li class="arxiv-result">')[1:]:
        m = re.search(r"arxiv\.org/abs/([^\"\s<]+?)(v\d+)?[\"<\s]", block)
        t = re.search(r'<p class="title is-5 mathjax">(.*?)</p>', block, re.S)
        entry_title = " ".join(_strip_tags(t.group(1)).split()) if t else ""
        if m and _title_ok(title, entry_title):
            entries.append({"title": entry_title, "arxiv_id": m.group(1)})
    entries.sort(key=lambda e: _identity.title_similarity(title, e["title"]), reverse=True)
    urls = [f"https://arxiv.org/pdf/{e['arxiv_id']}" for e in entries]
    return urls, ({"arxiv_id": entries[0]["arxiv_id"]} if entries else {}), entries


# ---------------------------------------------------------------------------
# Google Scholar
# ---------------------------------------------------------------------------

# Scholar serves a CAPTCHA after a burst of queries from one IP; once it does,
# every further query this run would be wasted, so the source switches off.
_scholar_lock = threading.Lock()
_scholar_last = 0.0
_scholar_blocked = False
SCHOLAR_MIN_INTERVAL = 4.0
_SCHOLAR_BLOCK_MARKERS = ("gs_captcha", "unusual traffic", "/sorry/", "recaptcha")


def _parse_scholar_results(page: str) -> list[dict]:
    results = []
    for block in re.split(r'<div class="gs_r gs_or', page)[1:]:
        t = re.search(r'<h3 class="gs_rt"[^>]*>(.*?)</h3>', block, re.S)
        if not t:
            continue
        found_title = re.sub(r"^\s*(\[[A-Z]+\]\s*)+", "", _strip_tags(t.group(1)))
        pdfs = re.findall(r'<div class="gs_or_ggsm"[^>]*>\s*<a href="([^"]+)"', block)
        results.append({"title": found_title, "pdfs": [html.unescape(p) for p in pdfs]})
    return results


def try_google_scholar(doi: str | None = None, *, title: str | None = None, timeout: int = 20) -> tuple[list[str], dict, list[dict]]:
    global _scholar_last, _scholar_blocked
    if _scholar_blocked or os.environ.get("PAPER_FETCH_NO_SCHOLAR") or not title or len(title.split()) < 3:
        return [], {}, []
    url = "https://scholar.google.com/scholar?" + urllib.parse.urlencode({"q": f'"{title}"', "hl": "en"})
    with _scholar_lock:
        wait = SCHOLAR_MIN_INTERVAL - (time.monotonic() - _scholar_last)
        if wait > 0:
            time.sleep(wait)
        _scholar_last = time.monotonic()
        try:
            page = runtime._get(url, accept="text/html", timeout=min(timeout, 12), user_agent=BROWSER_UA).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                _scholar_blocked = True
                runtime._progress("source_skip", source="google_scholar", reason=f"blocked_http_{e.code}")
            return [], {}, []
        except Exception as exc:
            runtime._progress("source_miss", source="google_scholar", reason=str(exc)[:120])
            return [], {}, []
    if any(marker in page for marker in _SCHOLAR_BLOCK_MARKERS):
        _scholar_blocked = True
        runtime._progress("source_skip", source="google_scholar", reason="captcha")
        return [], {}, []
    matched = [r for r in _parse_scholar_results(page) if r["pdfs"] and _title_ok(title, r["title"])]
    urls = [u for r in matched for u in r["pdfs"]]
    return list(dict.fromkeys(urls)), {}, matched


# ---------------------------------------------------------------------------
# CyberLeninka
# ---------------------------------------------------------------------------

def try_cyberleninka(doi: str | None = None, *, title: str | None = None, timeout: int = 20) -> tuple[list[str], dict, list[dict]]:
    if not title or len(title.split()) < 3 or runtime._deadline_exceeded():
        return [], {}, []
    body = json.dumps({"mode": "articles", "q": title, "size": 10, "from": 0}).encode()
    req = urllib.request.Request(
        "https://cyberleninka.ru/api/search",
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": BROWSER_UA, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=min(timeout, 12)) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as exc:
        runtime._progress("source_miss", source="cyberleninka", reason=str(exc)[:120])
        return [], {}, []
    matched = []
    for art in (data or {}).get("articles") or []:
        name = _strip_tags(art.get("name") or "")
        link = art.get("link") or ""
        if link.startswith("/article/") and _title_ok(title, name):
            matched.append({"title": name, "link": link, "year": art.get("year"), "journal": art.get("journal")})
    urls = [f"https://cyberleninka.ru{m['link']}/pdf" for m in matched]
    return urls, {}, matched
