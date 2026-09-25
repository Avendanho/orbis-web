"""APIs de acesso aberto: Unpaywall, OpenAlex, CORE, Semantic Scholar e afins.

São os índices que sabem *onde* existe uma cópia legal de um artigo. Nenhum
hospeda o PDF (com a exceção parcial da OpenAlex, que serve uma cópia em
cache); o que devolvem é um endereço, que a camada de download depois tenta.

Duas escolhas que valem explicação:

* **A landing page também é candidata.** Quando o índice conhece o repositório
  mas não o arquivo, a página costuma trazer o PDF numa meta tag
  ``citation_pdf_url``, que ``pdf_links`` sabe ler. Descartá-la jogava fora
  recuperação barata.
* **Chave de API onde existir.** A OpenAlex passou a medir o uso anônimo e o
  Semantic Scholar estrangula o pool sem chave; sem elas, as duas fontes
  simplesmente param de responder no meio de uma execução longa.

O acesso ao cliente HTTP e ao emissor de eventos passa por ``runtime`` — veja
lá por que não é um import direto de ``fetch``.
"""
from __future__ import annotations

import json
import os
import urllib.parse

import runtime
from identity import normalize_doi
from title_match import _title_similarity

def try_unpaywall(doi: str, *, timeout: int, errors: list | None = None) -> tuple[str | None, dict]:
    url = f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={runtime.EMAIL}"
    try:
        d = runtime._get_json(url, timeout=timeout)
    except Exception as e:
        runtime._progress("source_miss", source="unpaywall", reason=str(e))
        if errors is not None and runtime._is_transport_exc(e):
            errors.append({"source": "unpaywall", "detail": str(e)})
        return None, {}
    meta = {
        "title": d.get("title"),
        "year": d.get("year"),
        "author": (d.get("z_authors") or [{}])[0].get("family") if d.get("z_authors") else None,
        "journal": d.get("journal_name"),
    }
    locations = [d.get("best_oa_location") or {}, *(d.get("oa_locations") or [])]
    urls = []
    for loc in locations:
        for key in ("url_for_pdf", "url_for_landing_page"):
            url = loc.get(key)
            if url and url not in urls:
                urls.append(url)
    meta["pdf_candidates"] = urls
    return (urls[0] if urls else None), meta


def try_semantic_scholar(doi: str, *, timeout: int, errors: list | None = None) -> tuple[str | None, dict, dict]:
    url = (
        f"https://api.semanticscholar.org/graph/v1/paper/DOI:{urllib.parse.quote(doi)}"
        "?fields=title,year,authors,openAccessPdf,externalIds,venue"
    )
    try:
        d = runtime._get_json(url, timeout=timeout)
    except Exception as e:
        runtime._progress("source_miss", source="semantic_scholar", reason=str(e))
        if errors is not None and runtime._is_transport_exc(e):
            errors.append({"source": "semantic_scholar", "detail": str(e)})
        return None, {}, {}
    meta = {
        "title": d.get("title"),
        "year": d.get("year"),
        "author": (d.get("authors") or [{}])[0].get("name"),
        "journal": d.get("venue") or None,
    }
    pdf = (d.get("openAccessPdf") or {}).get("url")
    return pdf, meta, d.get("externalIds") or {}


def try_semantic_scholar_copy_by_title(doi: str, title: str, *, timeout: int) -> str | None:
    """Open copy of the same paper filed under a second Semantic Scholar record.

    The DOI's own record often lists no PDF while a sibling record (usually
    the arXiv or repository version) does. Needs SEMANTIC_SCHOLAR_API_KEY:
    the search endpoint answers 429 to anonymous clients. A hit counts only
    when its DOI matches or, lacking one, its title is a near-exact match.
    """
    if not os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip() or not title:
        return None
    params = urllib.parse.urlencode({"query": title, "fields": "title,openAccessPdf,externalIds", "limit": "5"})
    try:
        data = runtime._get_json("https://api.semanticscholar.org/graph/v1/paper/search?" + params, timeout=timeout)
    except Exception:
        return None
    for hit in data.get("data") or []:
        ext = hit.get("externalIds") or {}
        hit_doi = normalize_doi(ext.get("DOI") or "")
        if hit_doi:
            if hit_doi != normalize_doi(doi):
                continue
        elif _title_similarity(hit.get("title") or "", title) < 0.95:
            continue
        pdf = (hit.get("openAccessPdf") or {}).get("url")
        if pdf:
            return pdf
        if ext.get("ArXiv"):
            return runtime.try_arxiv(ext["ArXiv"])
    return None


def try_openalex(
    doi: str,
    *,
    timeout: int,
    errors: list | None = None,
) -> tuple[list[str], dict]:
    """Resolve DOI para URLs de PDFs OA usando OpenAlex."""

    params = {
        "filter": f"doi:https://doi.org/{doi}",
        "per_page": "1",
        "select": (
            "title,publication_year,authorships,"
            "primary_location,best_oa_location,locations"
        ),
    }
    mailto = os.environ.get("OPENALEX_MAILTO") or runtime.EMAIL
    if mailto:
        params["mailto"] = mailto

    url = (
        "https://api.openalex.org/works?"
        + urllib.parse.urlencode(params)
    )

    try:
        data = runtime._get_json(
            url,
            timeout=timeout,
        )

    except Exception as e:
        runtime._progress(
            "source_miss",
            source="openalex",
            reason=str(e),
        )

        if errors is not None and runtime._is_transport_exc(e):
            errors.append({
                "source": "openalex",
                "detail": str(e),
            })

        return [], {}

    resultados = data.get("results") or []

    if not resultados:
        return [], {}

    work = resultados[0]

    # -------------------------
    # Metadados
    # -------------------------

    authorships = work.get("authorships") or []

    first_author = None

    if authorships:
        author = authorships[0].get("author") or {}
        first_author = author.get("display_name")

    primary_location = (
        work.get("primary_location") or {}
    )

    source = (
        primary_location.get("source") or {}
    )

    meta = {
        "title": work.get("title"),
        "year": work.get("publication_year"),
        "author": first_author,
        "journal": source.get("display_name"),
    }

    # -------------------------
    # PDFs encontrados
    # -------------------------

    pdf_urls: list[str] = []

    def add_pdf(url: str | None) -> None:
        if not url:
            return

        if url not in pdf_urls:
            pdf_urls.append(url)

    # Primeiro a melhor localização OA escolhida pelo OpenAlex.
    best = work.get("best_oa_location") or {}

    if best.get("is_oa"):
        add_pdf(best.get("pdf_url"))

    # Depois todas as demais cópias abertas.
    for location in work.get("locations") or []:

        if not location.get("is_oa"):
            continue

        add_pdf(
            location.get("pdf_url")
        )

    # Por fim, as páginas de destino das cópias abertas que não declararam um
    # PDF. O OpenAlex frequentemente conhece o repositório mas não o arquivo;
    # a página em si costuma trazê-lo em <meta name="citation_pdf_url">, que
    # `_download` já sabe seguir. Vêm por último justamente por exigirem esse
    # passo extra — um pdf_url direto é sempre preferível.
    landing_pages: list[str] = []

    for location in work.get("locations") or []:

        if not location.get("is_oa"):
            continue

        url = location.get("landing_page_url")

        if url and url not in pdf_urls and url not in landing_pages:
            landing_pages.append(url)

    return pdf_urls + landing_pages, meta


def try_core(
    doi: str,
    *,
    timeout: int,
    errors: list | None = None,
) -> list[str]:
    """Procura cópias de acesso aberto de um DOI usando a CORE API."""

    # Skip Core API if requested (e.g., in special mode to avoid delays)
    if os.environ.get("PAPER_FETCH_SKIP_CORE") == "1":
        return []

    if not runtime.CORE_API_KEY:
        return []

    # Fielded DOI query: an unfielded phrase ("10.x/...") makes CORE answer
    # HTTP 500 after ~6 s, which always blew the timeout below.
    params = urllib.parse.urlencode({
        "q": f'doi:"{doi}"',
        "limit": "10",
    })

    url = (
        "https://api.core.ac.uk/v3/search/works/?"
        + params
    )

    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {runtime.CORE_API_KEY}",
            "User-Agent": runtime.UA,
            "Accept": "application/json",
        },
    )

    try:
        runtime._rate_limit_gate()

        # Use shorter timeout for Core API to avoid long delays when it's slow/unresponsive
        core_timeout = min(timeout, 10)
        with urllib.request.urlopen(
            request,
            timeout=core_timeout,
        ) as response:

            data = json.loads(
                response.read().decode("utf-8")
            )

    except urllib.error.HTTPError as e:

        # 401/403 normalmente indicam problema de autenticação.
        if e.code in (401, 403):
            runtime._progress(
                "source_miss",
                source="core",
                reason=f"authentication_failed_http_{e.code}",
            )
            return []

        if errors is not None and runtime._is_transport_exc(e):
            errors.append({
                "source": "core",
                "detail": str(e),
            })

        return []

    except Exception as e:

        if errors is not None and runtime._is_transport_exc(e):
            errors.append({
                "source": "core",
                "detail": str(e),
            })

        return []

    resultados = data.get("results") or []

    pdf_urls: list[str] = []

    def add_url(url: str | None) -> None:

        if not url:
            return

        url = str(url).strip()

        if not url:
            return

        if url not in pdf_urls:
            pdf_urls.append(url)

    target_doi = normalize_doi(doi)

    for artigo in resultados:
        # Extract all DOIs from the record
        record_dois_list = runtime.record_dois(artigo)

        # Check if any of the record's DOIs match our target DOI
        doi_match = False
        for record_doi in record_dois_list:
            if normalize_doi(record_doi) == target_doi:
                doi_match = True
                break

        if not doi_match:
            continue

        # Only if we have a DOI match, extract URLs
        add_url(
            artigo.get("downloadUrl")
        )

        for fulltext_url in (
            artigo.get("sourceFulltextUrls") or []
        ):
            add_url(fulltext_url)

    return pdf_urls


def try_openalex_content(doi: str, *, timeout: int, errors: list | None = None) -> str | None:
    """OpenAlex Content API: OpenAlex's own cached copy of the work's PDF.

    Needs OPENALEX_API_KEY (each download is metered; the free key allowance
    covers ~100 PDFs/day). Only works flagged has_content.pdf are requested,
    so no credit is spent on misses. PAPER_FETCH_NO_OPENALEX_CONTENT=1 opts out.
    """
    key = os.environ.get("OPENALEX_API_KEY", "").strip()
    if not key or os.environ.get("PAPER_FETCH_NO_OPENALEX_CONTENT"):
        return None
    params = {"select": "id,has_content"}
    mailto = os.environ.get("OPENALEX_MAILTO") or runtime.EMAIL
    if mailto:
        params["mailto"] = mailto
    url = f"https://api.openalex.org/works/doi:{urllib.parse.quote(doi)}?" + urllib.parse.urlencode(params)
    try:
        work = runtime._get_json(url, timeout=timeout)
    except Exception as e:
        if errors is not None and runtime._is_transport_exc(e):
            errors.append({"source": "openalex_content", "detail": str(e)})
        return None
    if not (work.get("has_content") or {}).get("pdf"):
        return None
    work_id = str(work.get("id") or "").rsplit("/", 1)[-1]
    if not work_id.startswith("W"):
        return None
    return f"https://content.openalex.org/works/{work_id}.pdf?api_key={urllib.parse.quote(key)}"


def try_osti(doi: str, *, timeout: int) -> str | None:
    """Accepted manuscript of a US DOE-funded article, from OSTI.GOV.

    DOE's public-access policy puts the accepted manuscript on osti.gov about
    a year after publication; open-access indexes usually list only the OSTI
    landing page. osti.gov is unreachable from some networks, so after a few
    consecutive transport failures it is skipped for the rest of the run.
    PAPER_FETCH_NO_OSTI=1 opts out.
    """
    if os.environ.get("PAPER_FETCH_NO_OSTI") or runtime._osti_failures >= runtime.OSTI_MAX_FAILURES:
        return None
    url = "https://www.osti.gov/api/v1/records?" + urllib.parse.urlencode({"doi": doi})
    try:
        records = runtime._get_json(url, timeout=min(timeout, 8))
    except Exception as e:
        if runtime._is_transport_exc(e) or isinstance(e, (TimeoutError, OSError)):
            with runtime._osti_lock:
                runtime.set_state("_osti_failures", runtime._osti_failures + 1)
        return None
    with runtime._osti_lock:
        runtime.set_state("_osti_failures", 0)
    if not isinstance(records, list) or not records:
        return None
    record = records[0]
    if normalize_doi(record.get("doi") or "") != normalize_doi(doi):
        return None
    fulltext = [link.get("href") for link in record.get("links") or [] if link.get("rel") == "fulltext" and link.get("href")]
    if fulltext:
        return fulltext[0]
    return f"https://www.osti.gov/servlets/purl/{record['osti_id']}" if record.get("osti_id") else None


def try_oa_button(doi: str, *, timeout: int, errors: list | None = None) -> str | None:
    """Try to find an Open Access PDF via the Open Access Button API.
    
    This aggregates data from Unpaywall, CORE, BASE, and PMC.
    """
    url = f"https://api.openaccessbutton.org/find?id={urllib.parse.quote(doi)}"
    try:
        raw = runtime._get(
            url,
            accept="application/json",
            timeout=timeout,
            user_agent=runtime.DOWNLOAD_UA,
        )
        data = json.loads(raw)
        
        # Look for the best availability record
        availability = data.get("data", {}).get("availability", [])
        for item in availability:
            if item.get("type") == "article" and item.get("url"):
                pdf_url = item["url"]
                # The API sometimes returns landing pages instead of direct PDFs,
                # but bypass403 and the PDF validator will handle that.
                return pdf_url
    except Exception as e:
        if errors is not None and runtime._is_transport_exc(e):
            errors.append({"source": "oa_button", "detail": str(e)})
            
    return None
