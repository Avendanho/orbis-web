"""Crossref, servidores de preprint e as fontes de último recurso.

Reúne quatro coisas que não pertencem a nenhum dos outros grupos:

* **Crossref** — o registro da própria editora. Sabe o título e às vezes
  publica links de mineração de texto; serve também para achar o DOI a partir
  do título quando a lista de entrada veio sem identificador.
* **Preprints** (arXiv, bioRxiv/medRxiv) — onde a versão aberta costuma estar
  quando a publicada é fechada.
* **ACL Anthology e PaperDL** — nichos com URL previsível.
* **Wayback Machine** — a última tentativa: uma URL de acesso aberto que hoje
  responde 404 pode ter sido arquivada quando ainda funcionava. Tem passo
  próprio de espera porque responde 429 com facilidade.

O acesso ao cliente HTTP e ao emissor de eventos passa por ``runtime`` — veja
lá por que não é um import direto de ``fetch``.
"""
from __future__ import annotations

import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

import runtime
from identity import normalize_doi
from pdf_links import extract_pdf_links
from title_match import _MIN_TITLE_LEN

def try_arxiv(arxiv_id: str) -> str:
    return f"https://arxiv.org/pdf/{arxiv_id}.pdf"


def try_arxiv_metadata(arxiv_id: str, *, timeout: int) -> dict:
    """Fetch title / year / first-author from arXiv's Atom API.

    Used when neither Unpaywall nor S2 returned metadata — typical for
    arXiv-only papers reached via the synthesized 10.48550/arXiv.<id> DOI
    form, which S2's by-DOI endpoint does not index. Without this, the
    deterministic filename falls back to encoding the DOI literal.

    Best-effort: returns an empty dict on any failure (offline, malformed
    response, paper not found).
    """
    bare = re.sub(r"v\d+$", "", arxiv_id)
    try:
        body = runtime._get(
            f"http://export.arxiv.org/api/query?id_list={bare}",
            accept="application/atom+xml",
            timeout=timeout,
        )
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(body)
        entry = root.find("atom:entry", ns)
        if entry is None:
            return {}
        title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
        published = entry.findtext("atom:published", default="", namespaces=ns) or ""
        year = int(published[:4]) if published[:4].isdigit() else None
        author = entry.findtext("atom:author/atom:name", default=None, namespaces=ns)
        return {"title": title or None, "year": year, "author": author}
    except Exception:
        return {}


def try_biorxiv(doi: str, *, timeout: int) -> str | None:
    if not doi.startswith("10.1101/"):
        return None
    for server in ("biorxiv", "medrxiv"):
        try:
            d = runtime._get_json(f"https://api.biorxiv.org/details/{server}/{doi}", timeout=timeout)
            coll = d.get("collection") or []
            if coll:
                latest = coll[-1]
                return f"https://www.{server}.org/content/10.1101/{latest['doi'].split('/')[-1]}v{latest.get('version', 1)}.full.pdf"
        except Exception:
            continue
    return None


def try_crossref_title(title: str, *, timeout: int) -> tuple[str | None, dict, list[dict]]:
    """Resolve a paper title to a DOI via Crossref.

    Crossref's relevance score is unitless and scales with title length, so we
    don't gate on an absolute threshold — we hand the top match plus the
    top 3 candidates back to the caller so an agent can sanity-check.

    Returns ``(top_doi, top_meta, candidates)``:
      - ``top_doi``: best-match DOI, or ``None`` if Crossref returned no items
      - ``top_meta``: ``{title, year, author, journal, score}`` for the top hit
      - ``candidates``: list of up to 3 candidate dicts in score order
    """
    q = title.strip()
    if len(q) < _MIN_TITLE_LEN:
        return None, {}, []
    # query.title outranks query.bibliographic for this use case: the input is
    # explicitly a paper title, and bibliographic mode also weights authors/year
    # equally — empirically that demoted the canonical AlphaFold paper below
    # secondary "Faculty Opinions recommendation of ..." entries that share
    # all the user's title tokens.
    params = {
        "query.title": q,
        "rows": "3",
        "select": "DOI,title,score,author,issued,container-title",
    }
    # Crossref's polite pool gives priority to requests that identify the
    # caller via mailto. We already pass runtime.UA but also include mailto when the
    # operator set UNPAYWALL_EMAIL, since the same address is theirs.
    if runtime.EMAIL:
        params["mailto"] = runtime.EMAIL
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    try:
        data = runtime._get_json(url, timeout=timeout)
    except Exception as e:
        runtime._progress("title_resolve_failed", reason=str(e))
        return None, {}, []

    items = ((data.get("message") or {}).get("items")) or []
    if not items:
        return None, {}, []

    candidates: list[dict] = []
    for it in items[:3]:
        title_list = it.get("title") or []
        author_list = it.get("author") or []
        first_author = ""
        if author_list:
            a0 = author_list[0]
            first_author = a0.get("family") or a0.get("name") or ""
        issued = ((it.get("issued") or {}).get("date-parts") or [[None]])[0]
        year = issued[0] if issued and issued[0] else None
        cont = it.get("container-title") or []
        candidates.append({
            "doi": it.get("DOI"),
            "title": title_list[0] if title_list else None,
            "year": year,
            "author": first_author or None,
            "journal": cont[0] if cont else None,
            "score": it.get("score"),
        })

    top = candidates[0]
    top_meta = {k: v for k, v in top.items() if k != "doi"}
    return top.get("doi"), top_meta, candidates


def try_semantic_scholar_match(title: str, *, timeout: int) -> tuple[str | None, dict]:
    """Resolve a title to a DOI via Semantic Scholar's ``/paper/search/match``.

    S2's match endpoint returns at most one paper — its closest title in the
    corpus. Better than the relevance endpoint for our use case because we
    want exactness, not breadth. Critically, S2's corpus includes arXiv-only
    papers that never get a Crossref DOI; for those we synthesize the
    canonical arXiv DOI ``10.48550/arXiv.{id}`` so the downstream fetch
    chain treats the result uniformly.

    Returns ``(doi, meta)``. ``meta`` carries ``title``, ``year``, ``author``,
    ``journal``, ``paper_id``, and ``external_ids`` for caller transparency.
    """
    q = title.strip()
    if len(q) < _MIN_TITLE_LEN:
        return None, {}
    params = {
        "query": q,
        "fields": "title,authors,year,venue,externalIds",
    }
    url = "https://api.semanticscholar.org/graph/v1/paper/search/match?" + urllib.parse.urlencode(params)
    try:
        d = runtime._get_json(url, timeout=timeout)
    except Exception as e:
        # 404 (no match) is the expected miss path here — the helper logs
        # the same way for any failure since the caller treats them as miss.
        runtime._progress("title_resolver_miss", resolver="semantic_scholar", reason=str(e))
        return None, {}

    items = d.get("data") or []
    if not items:
        return None, {}
    top = items[0]
    ext = top.get("externalIds") or {}
    doi = ext.get("DOI")
    if not doi and ext.get("ArXiv"):
        # arXiv assigns DataCite DOIs as 10.48550/arXiv.<id> (since 2022;
        # for older preprints the DOI may not be registered, but the fetch
        # chain's arXiv source resolver doesn't need a registered DOI —
        # it builds the PDF URL from the arXiv id itself once S2 / Unpaywall
        # surface it via externalIds during the download phase).
        doi = f"10.48550/arXiv.{ext['ArXiv']}"
    if not doi:
        return None, {}
    authors = top.get("authors") or []
    return doi, {
        "doi": doi,
        "title": top.get("title"),
        "year": top.get("year"),
        "author": authors[0].get("name") if authors else None,
        "journal": top.get("venue") or None,
        "paper_id": top.get("paperId"),
        "external_ids": ext,
    }


def try_wayback(url: str, *, timeout: int) -> str | None:
    """Archived PDF capture of an OA URL that is dead or blocking the live fetch.

    Queries the Wayback CDX index for the newest status-200 capture whose
    MIME type is application/pdf, and returns its raw-bytes (`id_`) URL so
    the archived file comes back unmodified. Calls are paced process-wide
    and paused for a while after a 429. PAPER_FETCH_NO_WAYBACK=1 opts out.
    """
    if os.environ.get("PAPER_FETCH_NO_WAYBACK") or not url or "web.archive.org" in url:
        return None
    with runtime._wayback_lock:
        now = time.monotonic()
        if now < runtime._wayback_paused_until:
            return None
        wait = runtime._wayback_next_at - now
        runtime.set_state("_wayback_next_at",
                          max(now, runtime._wayback_next_at) + runtime.WAYBACK_MIN_INTERVAL)
    if wait > 0:
        time.sleep(wait)
    params = urllib.parse.urlencode([
        ("url", re.sub(r"^https?://", "", url)),
        ("output", "json"),
        ("limit", "-1"),
        ("fl", "timestamp,original"),
        ("filter", "statuscode:200"),
        ("filter", "mimetype:application/pdf"),
    ])
    try:
        rows = runtime._get_json("https://web.archive.org/cdx/search/cdx?" + params, timeout=min(timeout, 15))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            with runtime._wayback_lock:
                runtime.set_state("_wayback_paused_until",
                                  time.monotonic() + runtime.WAYBACK_429_PAUSE)
        return None
    except Exception:
        return None
    # First row is the header (["timestamp", "original"]).
    if not isinstance(rows, list) or len(rows) < 2:
        return None
    ts, original = rows[-1][0], rows[-1][1]
    return f"https://web.archive.org/web/{ts}id_/{original}"


def try_doi_resolver(doi: str, *, timeout: int, errors: list | None = None) -> tuple[str | None, dict]:
    """Try to resolve a DOI using doi.org resolver directly.

    This follows redirects to find the final landing page, then attempts to
    extract a PDF link from common patterns on publisher sites.
    """
    if runtime._deadline_exceeded():
        return None, {}
    # Normalize DOI
    doi_norm = normalize_doi(doi)
    if not doi_norm:
        return None, {}

    # Try doi.org resolver
    url = f"https://doi.org/{doi_norm}"
    try:
        # Follow redirects to get the final URL
        req = urllib.request.Request(url, headers={"User-Agent": runtime.UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final_url = resp.geturl()
            # Read a limited amount of HTML to look for PDF links
            html = resp.read(1024 * 1024).decode("utf-8", "ignore")  # 1MB max
    except Exception as e:
        if errors is not None and runtime._is_transport_exc(e):
            errors.append({"source": "doi_resolver", "detail": str(e)})
        return None, {}

    # Check if we got a challenge/interstitial page (e.g., Cloudflare) that prevents
    # us from seeing the real content without JavaScript/cookies.
    challenge_indicators = [
        "Just a moment",
        "Enable JavaScript and cookies",
        "Checking your browser before accessing",
        "attention required!|please stand by",
        "browser check",
        "DDoS protection by Cloudflare",
        "Access denied",
        "Please wait while we are checking your browser",
        "jschallenge",
        "cf-chl-bypass",
        "cf-error-details",
        "cf-browser-verification",
    ]
    html_lower = html.lower()
    for indicator in challenge_indicators:
        if indicator.lower() in html_lower:
            # Likely a challenge page; skip PDF extraction as we won't get the real content.
            return None, {}

    pdf_urls = extract_pdf_links(html, final_url)
    pdf_url = pdf_urls[0] if pdf_urls else None

    # Extract some metadata from the HTML title for filename generation
    meta = {}
    title_match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
    if title_match:
        meta["title"] = title_match.group(1).strip()

    meta["pdf_candidates"] = pdf_urls
    return pdf_url, meta


def try_crossref_links(
    doi: str,
    *,
    timeout: int,
    errors: list | None = None,
) -> tuple[list[str], dict]:
    """Extract direct fulltext PDF URLs and metadata from Crossref works API."""
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
    if runtime.EMAIL:
        url += f"?mailto={urllib.parse.quote(runtime.EMAIL)}"
    try:
        data = runtime._get_json(url, timeout=timeout)
    except Exception as e:
        if errors is not None and runtime._is_transport_exc(e):
            errors.append({"source": "crossref", "detail": str(e)})
        return [], {}

    msg = data.get("message") or {}
    title_list = msg.get("title") or []
    author_list = msg.get("author") or []
    first_author = ""
    if author_list:
        a0 = author_list[0]
        first_author = a0.get("family") or a0.get("name") or ""
    issued = ((msg.get("issued") or {}).get("date-parts") or [[None]])[0]
    year = issued[0] if issued and issued[0] else None
    cont = msg.get("container-title") or []

    meta = {
        "title": title_list[0] if title_list else None,
        "year": year,
        "author": first_author or None,
        "journal": cont[0] if cont else None,
    }

    pdf_urls: list[str] = []
    links = msg.get("link") or []
    for l in links:
        if isinstance(l, dict):
            ctype = (l.get("content-type") or "").lower()
            intended = (l.get("intended-application") or "").lower()
            url_cand = l.get("URL")
            if not url_cand or (("pdf" not in ctype) and "text-mining" not in intended and not url_cand.endswith(".pdf")):
                continue
            if runtime._crossref_link_is_futile(url_cand, ctype):
                continue
            if url_cand not in pdf_urls:
                pdf_urls.append(url_cand)

    return pdf_urls, meta


def try_acl_anthology(
    doi: str,
    *,
    timeout: int,
    errors: list | None = None,
) -> tuple[list[str], dict]:
    """Resolve ACL Anthology DOIs (e.g. 10.18653/v1/2020.acl-main.1) directly to PDF."""
    doi_lower = doi.lower()
    pdf_urls: list[str] = []
    meta: dict = {}
    if "10.18653/v1/" in doi_lower:
        acl_id = doi_lower.split("10.18653/v1/")[-1].strip("/")
        pdf_urls.append(f"https://aclanthology.org/{acl_id}.pdf")
    elif "10.18653/" in doi_lower:
        acl_id = doi_lower.split("10.18653/")[-1].strip("/")
        pdf_urls.append(f"https://aclanthology.org/{acl_id}.pdf")
    return pdf_urls, meta


def try_paperdl(
    query: str,
    *,
    timeout: int,
    errors: list | None = None,
) -> tuple[list[str], dict]:
    """Resolve papers via PaperDL async clients (ACL Anthology, PMLR)."""
    try:
        import asyncio
        from paperdl import PaperClient

        async def _run_search():
            async with PaperClient(
                ["acl_anthology"],
                default_init_kwargs={"verbose": False, "show_progress": False},
            ) as client:
                return await client.search(query, total_results=2)

        papers = asyncio.run(_run_search())
        pdf_urls: list[str] = []
        meta: dict = {}
        for p in papers or []:
            if getattr(p, "download_url", None) and p.download_url not in pdf_urls:
                pdf_urls.append(p.download_url)
                if not meta.get("title") and getattr(p, "title", None):
                    meta["title"] = p.title
                if not meta.get("author") and getattr(p, "authors", None):
                    if isinstance(p.authors, list) and p.authors:
                        meta["author"] = p.authors[0]
                    elif isinstance(p.authors, str):
                        meta["author"] = p.authors.split(",")[0]
        return pdf_urls, meta
    except Exception as e:
        if errors is not None and runtime._is_transport_exc(e):
            errors.append({"source": "paperdl", "detail": str(e)})
        return [], {}
