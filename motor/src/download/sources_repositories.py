"""Repositórios e agregadores de acesso aberto.

Dez fontes que guardam ou indexam cópias abertas fora do site da editora:
OpenAIRE, HAL, Zenodo, DataCite, DOAJ, Dryad, Figshare, NASA NTRS, BASE e o
Internet Archive Scholar (fatcat).

Todas expõem a mesma assinatura — ``(doi=None, *, title=None, timeout)`` que
devolve ``(urls, meta, registros_brutos)`` — porque são consultadas em
paralelo pela etapa de recuperação por título, que trata as respostas de forma
uniforme. Nenhuma aceita um resultado só por ser parecido: o registro precisa
casar pelo DOI ou pelo título (ver ``title_match._match_records``), senão o
PDF de outro artigo entraria na revisão.

O acesso ao cliente HTTP e ao emissor de eventos passa por ``runtime`` — veja
lá a explicação de por que não é um import direto de ``fetch``.
"""
from __future__ import annotations

import os
import urllib.parse

import identity as _identity
import runtime
from identity import normalize_doi
from title_match import _deep_find_pdf_urls, _match_records

def try_openaire(doi: str | None = None, *, title: str | None = None, timeout: int = 20) -> tuple[list[str], dict, list[dict]]:
    """Search OpenAIRE Graph v3 for a DOI or title."""
    try:
        params = {
            "page": 1,
            "pageSize": 10,
        }
        if doi:
            params["filter"] = f"type:publication,ids.doi:{doi}"
        elif title:
            params["search"] = title
            params["type"] = "publication"
        else:
            return [], {}, []
        url = "https://api.openaire.eu/graph/v3/research-products?" + urllib.parse.urlencode(params)
        data = runtime._get_json(url, timeout=timeout)
        results = [r for r in ((data or {}).get("results") or []) if isinstance(r, dict)]

        # OpenAIRE returns several hits for a query; a result page is a set
        # of candidates, never proof that every hit is the requested article.
        # When we searched by DOI, only hits whose own metadata carries that
        # exact DOI may contribute PDF URLs/metadata (Level 1 identity).
        # When we searched by title, only the best title-matching hit may.
        if doi:
            use_results = _identity.records_matching_doi(results, doi)
        else:
            use_results = _identity.filter_hits_by_title(
                results, title,
                title_getter=lambda r: r.get("mainTitle") or r.get("title"),
            )

        urls: list[str] = []
        candidates: list[dict] = []
        for item in use_results:
            candidates.append(item)
            urls.extend(_deep_find_pdf_urls(item))
        meta: dict = {}
        if use_results:
            first = use_results[0] or {}
            meta = {
                "title": first.get("mainTitle") or first.get("title"),
                "year": first.get("publicationDate") or first.get("year"),
                "author": None,
                "journal": None,
                "openaire_id": first.get("id"),
            }
            authors = first.get("authors") or first.get("author") or []
            if isinstance(authors, list) and authors:
                a0 = authors[0]
                if isinstance(a0, dict):
                    meta["author"] = a0.get("fullName") or a0.get("name")
                elif isinstance(a0, str):
                    meta["author"] = a0
        return list(dict.fromkeys(urls)), meta, candidates[:10]
    except Exception as exc:
        runtime._progress("source_miss", source="openaire", reason=str(exc))
        return [], {}, []


def try_hal(doi: str | None = None, *, title: str | None = None, timeout: int = 20) -> tuple[list[str], dict, list[dict]]:
    """Search HAL Open Archive."""
    try:
        if doi:
            q = f'doiId_s:"{doi}"'
        elif title:
            q = title
        else:
            return [], {}, []
        params = {
            "q": q,
            "wt": "json",
            "rows": 10,
            "fl": "*,uri_s,fileMain_s,fileMain,fileAnnexes_s,title_s,docType_s,doiId_s,uri_s",
        }
        url = "https://api.archives-ouvertes.fr/search/?" + urllib.parse.urlencode(params)
        data = runtime._get_json(url, timeout=timeout)
        docs = [d for d in (((data or {}).get("response") or {}).get("docs") or []) if isinstance(d, dict)]

        def _hal_doc_title(d: dict) -> str | None:
            t = d.get("title_s") or d.get("title")
            return t[0] if isinstance(t, list) and t else (t if isinstance(t, str) else None)

        # Same rule as OpenAIRE: a HAL result page holds independent
        # candidates. Only a hit whose own doiId_s matches (DOI search) or
        # whose own title matches (title search) may contribute files.
        if doi:
            use_docs = _identity.records_matching_doi(
                docs, doi, doi_getter=lambda d: d.get("doiId_s") if isinstance(d.get("doiId_s"), str) else None,
            )
        else:
            use_docs = _identity.filter_hits_by_title(docs, title, title_getter=_hal_doc_title)

        urls: list[str] = []
        for doc in use_docs:
            for key in ("fileMain_s", "fileMain", "fileAnnexes_s", "uri_s"):
                value = doc.get(key)
                values = value if isinstance(value, list) else [value]
                for v in values:
                    if isinstance(v, str) and v.startswith(("http://", "https://")):
                        low = v.casefold()
                        if ".pdf" in low or "/file/" in low or key.startswith("file"):
                            urls.append(v)
        meta: dict = {}
        if use_docs:
            first = use_docs[0]
            title_value = first.get("title_s") or first.get("title")
            if isinstance(title_value, list):
                title_value = title_value[0] if title_value else None
            meta = {
                "title": title_value,
                "doi": first.get("doiId_s") if isinstance(first.get("doiId_s"), str) else None,
                "hal_id": first.get("docid"),
                "document_type": first.get("docType_s"),
            }
        return list(dict.fromkeys(urls)), meta, use_docs[:10]
    except Exception as exc:
        runtime._progress("source_miss", source="hal", reason=str(exc))
        return [], {}, []


def try_zenodo(doi: str | None = None, *, title: str | None = None, timeout: int = 20) -> tuple[list[str], dict, list[dict]]:
    """Search Zenodo published records and extract file download URLs."""
    try:
        q = doi if doi else title
        if not q:
            return [], {}, []
        params = {
            "q": q,
            "page": 1,
            "size": 10,
            "sort": "bestmatch",
        }
        url = "https://zenodo.org/api/records?" + urllib.parse.urlencode(params)
        data = runtime._get_json(url, timeout=timeout)
        hits = [h for h in (((data or {}).get("hits") or {}).get("hits") or []) if isinstance(h, dict)]

        # CRITICAL: Zenodo's ``q=`` parameter is a free-text search, not an
        # exact-DOI filter — searching q=<doi> can and does return unrelated
        # deposits that merely mention similar tokens. Blindly pooling
        # _deep_find_pdf_urls() across every hit (the original bug) lets
        # article B's PDF get attached to article A's requested DOI. Only a
        # hit whose OWN metadata.doi equals the requested DOI (Level 1), or
        # whose own title matches the requested title (Level 2, title-mode),
        # may contribute PDF URLs/metadata.
        if doi:
            use_hits = _identity.records_matching_doi(
                hits, doi, doi_getter=lambda h: (h.get("metadata") or {}).get("doi"),
            )
        else:
            use_hits = _identity.filter_hits_by_title(
                hits, title, title_getter=lambda h: (h.get("metadata") or {}).get("title"),
            )

        urls: list[str] = []
        candidates: list[dict] = []
        for hit in use_hits:
            candidates.append(hit)
            urls.extend(_deep_find_pdf_urls(hit))
        meta: dict = {}
        if use_hits:
            first = use_hits[0].get("metadata") or {}
            creators = first.get("creators") or []
            meta = {
                "title": first.get("title"),
                "year": first.get("publication_date") or first.get("date"),
                "author": (creators[0].get("name") if creators and isinstance(creators[0], dict) else None),
                "zenodo_id": use_hits[0].get("id"),
                "doi": first.get("doi"),
            }
        return list(dict.fromkeys(urls)), meta, candidates[:10]
    except Exception as exc:
        runtime._progress("source_miss", source="zenodo", reason=str(exc))
        return [], {}, []


def try_datacite(doi: str | None = None, *, title: str | None = None, timeout: int = 20) -> tuple[list[str], dict, list[dict]]:
    """Search DataCite public DOI metadata."""
    try:
        if doi:
            url = f"https://api.datacite.org/dois/{urllib.parse.quote(doi, safe='')}"
            data = runtime._get_json(url, timeout=timeout)
            records = [data.get("data")] if isinstance(data, dict) and data.get("data") else []
        elif title:
            params = {
                "query": title,
                "page[size]": 10,
            }
            url = "https://api.datacite.org/dois?" + urllib.parse.urlencode(params)
            data = runtime._get_json(url, timeout=timeout)
            records = [r for r in ((data or {}).get("data") or []) if isinstance(r, dict)]
        else:
            return [], {}, []

        # The /dois/{doi} lookup above is an exact-DOI-keyed endpoint, so its
        # single record is already Level-1 identity evidence. The title
        # search endpoint, however, returns multiple independent candidates
        # — only the best title-matching one may contribute PDF URLs.
        if title and not doi:
            def _dc_title(r: dict) -> str | None:
                titles = ((r.get("attributes") or {}).get("titles")) or []
                return titles[0].get("title") if titles and isinstance(titles[0], dict) else None

            records = _identity.filter_hits_by_title(records, title, title_getter=_dc_title)

        urls: list[str] = []
        candidates: list[dict] = []
        for record in records:
            candidates.append(record)
            urls.extend(_deep_find_pdf_urls(record))
            attrs = record.get("attributes") or {}
            url_value = attrs.get("url")
            if isinstance(url_value, str) and url_value.startswith(("http://", "https://")) and ".pdf" in url_value.casefold():
                urls.append(url_value)
        meta: dict = {}
        if records:
            attrs = records[0].get("attributes") or {}
            titles = attrs.get("titles") or []
            title_value = titles[0].get("title") if titles and isinstance(titles[0], dict) else None
            creators = attrs.get("creators") or []
            author = None
            if creators and isinstance(creators[0], dict):
                author = creators[0].get("name") or creators[0].get("familyName")
            meta = {
                "title": title_value,
                "year": attrs.get("publicationYear"),
                "author": author,
                "publisher": attrs.get("publisher"),
                "resource_type": (attrs.get("types") or {}).get("resourceTypeGeneral"),
                "datacite_doi": attrs.get("doi"),
            }
        return list(dict.fromkeys(urls)), meta, candidates[:10]
    except Exception as exc:
        runtime._progress("source_miss", source="datacite", reason=str(exc))
        return [], {}, []


def try_doaj(doi: str | None = None, *, title: str | None = None, timeout: int = 20) -> tuple[list[str], dict, list[dict]]:
    """Best-effort DOAJ article search. DOAJ is used for metadata/direct OA links."""
    try:
        if doi:
            endpoint = "https://doaj.org/api/search/articles/doi:" + urllib.parse.quote(doi, safe="")
        elif title:
            endpoint = "https://doaj.org/api/search/articles/title:" + urllib.parse.quote(title, safe="")
        else:
            return [], {}, []
        data = runtime._get_json(endpoint, timeout=timeout)
        results = [r for r in ((data or {}).get("results") or []) if isinstance(r, dict)]

        def _doaj_title(r: dict) -> str | None:
            return (r.get("bibjson") or {}).get("title")

        # DOAJ's search endpoint is a free-text query even when scoped with
        # a "doi:" / "title:" field prefix — it can return near matches
        # rather than an exact single record, so identity must still be
        # verified per hit before pooling PDF URLs.
        if doi:
            use_results = _identity.records_matching_doi(
                results, doi, doi_getter=lambda r: (r.get("bibjson") or {}).get("identifier") and next(
                    (i.get("id") for i in (r.get("bibjson") or {}).get("identifier") or []
                     if isinstance(i, dict) and str(i.get("type", "")).lower() == "doi"), None,
                ),
            )
        else:
            use_results = _identity.filter_hits_by_title(results, title, title_getter=_doaj_title)

        urls: list[str] = []
        for item in use_results[:10]:
            urls.extend(_deep_find_pdf_urls(item))
        meta: dict = {}
        if use_results:
            bib = ((use_results[0].get("bibjson") or {}) if isinstance(use_results[0], dict) else {})
            authors = bib.get("author") or []
            meta = {
                "title": bib.get("title"),
                "year": bib.get("year"),
                "author": authors[0].get("name") if authors and isinstance(authors[0], dict) else None,
                "journal": (bib.get("journal") or {}).get("title") if isinstance(bib.get("journal"), dict) else None,
            }
        return list(dict.fromkeys(urls)), meta, use_results[:10]
    except Exception as exc:
        runtime._progress("source_miss", source="doaj", reason=str(exc))
        return [], {}, []


def try_dryad(doi: str | None = None, *, title: str | None = None, timeout: int = 20) -> tuple[list[str], dict, list[dict]]:
    """Dryad datasets (often carrying the article's accepted manuscript)."""
    query = doi or title
    if not query:
        return [], {}, []
    try:
        url = "https://datadryad.org/api/v2/search?" + urllib.parse.urlencode({"q": query, "per_page": 10})
        data = runtime._get_json(url, timeout=timeout)
        datasets = ((data or {}).get("_embedded") or {}).get("stash:datasets") or []
        use = _match_records(
            datasets, doi, title,
            doi_getter=lambda d: d.get("relatedPublicationISSN") or d.get("identifier"),
            title_getter=lambda d: d.get("title"),
        )
        urls: list[str] = []
        for record in use:
            for url_candidate in _deep_find_pdf_urls(record):
                urls.append(url_candidate)
        meta = {}
        if use:
            authors = use[0].get("authors") or []
            meta = {
                "title": use[0].get("title"),
                "author": (authors[0].get("lastName") if authors and isinstance(authors[0], dict) else None),
                "year": str(use[0].get("publicationDate") or "")[:4] or None,
            }
        return list(dict.fromkeys(urls)), meta, use[:10]
    except Exception as exc:
        runtime._progress("source_miss", source="dryad", reason=str(exc))
        return [], {}, []


def try_figshare(doi: str | None = None, *, title: str | None = None, timeout: int = 20) -> tuple[list[str], dict, list[dict]]:
    """Figshare (and its institutional portals) by DOI or title."""
    query = doi or title
    if not query:
        return [], {}, []
    try:
        url = "https://api.figshare.com/v2/articles?" + urllib.parse.urlencode(
            {"search_for": query, "page_size": 10, "item_type": 3}
        )
        records = runtime._get_json(url, timeout=timeout)
        if not isinstance(records, list):
            return [], {}, []
        use = _match_records(records, doi, title, doi_getter=lambda r: r.get("doi"), title_getter=lambda r: r.get("title"))
        urls: list[str] = []
        meta: dict = {}
        for record in use[:3]:
            try:
                detail = runtime._get_json(f"https://api.figshare.com/v2/articles/{record['id']}", timeout=timeout)
            except Exception:
                continue
            for f in detail.get("files") or []:
                name = (f.get("name") or "").lower()
                if f.get("download_url") and (name.endswith(".pdf") or "pdf" in (f.get("mimetype") or "")):
                    urls.append(f["download_url"])
            if not meta:
                authors = detail.get("authors") or []
                meta = {
                    "title": detail.get("title"),
                    "author": (authors[0].get("last_name") or authors[0].get("full_name")) if authors else None,
                    "year": str(detail.get("published_date") or "")[:4] or None,
                }
        return list(dict.fromkeys(urls)), meta, use[:10]
    except Exception as exc:
        runtime._progress("source_miss", source="figshare", reason=str(exc))
        return [], {}, []


def try_ntrs(doi: str | None = None, *, title: str | None = None, timeout: int = 20) -> tuple[list[str], dict, list[dict]]:
    """NASA Technical Reports Server: accepted manuscripts of NASA-funded work."""
    query = doi or title
    if not query:
        return [], {}, []
    try:
        url = "https://ntrs.nasa.gov/api/citations/search?" + urllib.parse.urlencode({"q": query, "page.size": 10})
        data = runtime._get_json(url, timeout=timeout)
        records = (data or {}).get("results") or []
        use = _match_records(
            records, doi, title,
            doi_getter=lambda r: (r.get("otherReportNumbers") or [None])[0] if r.get("doi") is None else r.get("doi"),
            title_getter=lambda r: r.get("title"),
        )
        urls: list[str] = []
        for record in use:
            urls.extend(_ntrs_pdf_urls_for_record(record, timeout))
        meta = {}
        if use:
            authors = use[0].get("authorAffiliations") or []
            meta = {
                "title": use[0].get("title"),
                "author": ((authors[0].get("meta") or {}).get("author") or {}).get("name") if authors else None,
                "year": str(use[0].get("publicationDate") or "")[:4] or None,
            }
        return list(dict.fromkeys(urls)), meta, use[:10]
    except Exception as exc:
        runtime._progress("source_miss", source="ntrs", reason=str(exc))
        return [], {}, []


def try_base_search(doi: str | None = None, *, title: str | None = None, timeout: int = 20) -> tuple[list[str], dict, list[dict]]:
    """BASE (Bielefeld Academic Search Engine): the largest OA repository index.

    Uses BASE's documented HTTP search interface, which answers only to IP
    addresses registered with BASE (free, requested at
    https://www.base-search.net/about/en/faq_data.php). Unregistered hosts
    get HTTP 401/403 and the source is skipped for the rest of the run.
    """
    if runtime._base_blocked or os.environ.get("PAPER_FETCH_NO_BASE"):
        return [], {}, []
    query = f'dcdoi:"{doi}"' if doi else f'dctitle:"{title}"' if title else None
    if not query:
        return [], {}, []
    params = {
        "func": "PerformSearch",
        "query": query,
        "format": "json",
        "hits": "10",
        "coll": "all",
    }
    try:
        data = runtime._get_json("https://api.base-search.net/cgi-bin/BaseHttpSearchInterface.fcgi?" + urllib.parse.urlencode(params), timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            runtime.set_state("_base_blocked", True)
            runtime._progress("source_skip", source="base", reason=f"ip_not_registered_http_{e.code}")
        return [], {}, []
    except Exception as exc:
        runtime._progress("source_miss", source="base", reason=str(exc))
        return [], {}, []

    # BASE answers an unregistered IP with HTTP 200 and an error payload,
    # so the refusal has to be recognised here and not only from a status code.
    denied = str((data or {}).get("error") or "")
    if denied:
        runtime.set_state("_base_blocked", True)
        runtime._progress("source_skip", source="base", reason=denied[:120])
        return [], {}, []

    docs = (((data or {}).get("response") or {}).get("docs")) or []
    use = _match_records(
        docs, doi, title,
        doi_getter=lambda d: d.get("dcdoi") or d.get("dcidentifier"),
        title_getter=lambda d: d.get("dctitle"),
    )
    urls: list[str] = []
    for doc in use:
        for key in ("dclink", "dcidentifier", "dcsource"):
            value = doc.get(key)
            for candidate in (value if isinstance(value, list) else [value]):
                if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
                    urls.append(candidate)
    meta = {}
    if use:
        meta = {
            "title": use[0].get("dctitle"),
            "author": (use[0].get("dccreator") or [None])[0] if isinstance(use[0].get("dccreator"), list) else use[0].get("dccreator"),
            "year": use[0].get("dcyear"),
        }
    return list(dict.fromkeys(urls))[:8], meta, use[:10]


def try_fatcat(doi: str | None = None, *, title: str | None = None, timeout: int = 20) -> tuple[list[str], dict, list[dict]]:
    """Internet Archive Scholar's catalogue (fatcat), which links web captures.

    Only the public fatcat API is used, with an identified user agent. The
    scholar.archive.org search UI sits behind a proof-of-work challenge and
    is not touched. The API has been offline for stretches, so a couple of
    transport failures disable it for the rest of the run.
    """
    if runtime._fatcat_failures >= runtime.FATCAT_MAX_FAILURES or os.environ.get("PAPER_FETCH_NO_FATCAT") or not doi:
        return [], {}, []
    url = f"https://api.fatcat.wiki/v0/release/lookup?doi={urllib.parse.quote(doi)}&expand=files&hide=refs,abstracts"
    try:
        release = runtime._get_json(url, timeout=min(timeout, 10))
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return [], {}, []
        runtime.set_state("_fatcat_failures", runtime._fatcat_failures + 1)
        return [], {}, []
    except Exception:
        runtime.set_state("_fatcat_failures", runtime._fatcat_failures + 1)
        return [], {}, []
    if normalize_doi(release.get("doi") or "") != normalize_doi(doi):
        return [], {}, []
    urls: list[str] = []
    for f in release.get("files") or []:
        if (f.get("mimetype") or "application/pdf") != "application/pdf":
            continue
        for u in f.get("urls") or []:
            if isinstance(u, dict) and u.get("url"):
                urls.append(u["url"])
    meta = {
        "title": release.get("title"),
        "year": release.get("release_year"),
        "journal": ((release.get("extra") or {}).get("container_name")) or release.get("container_name"),
    }
    return list(dict.fromkeys(urls))[:6], meta, [release]


def _ntrs_pdf_urls_for_record(record: dict, timeout: int) -> list[str]:
    """Expand an NTRS citation record into downloadable PDF URLs."""
    urls = _deep_find_pdf_urls(record)
    citation_id = record.get("ntrs_id") or record.get("id")
    if not citation_id:
        return list(dict.fromkeys(urls))
    base = os.environ.get("NTRS_API_BASE", "https://ntrs.nasa.gov/api").rstrip("/")
    try:
        data = runtime._get_json(f"{base}/citations/{urllib.parse.quote(str(citation_id), safe='')}/downloads", timeout=timeout)
        urls.extend(_deep_find_pdf_urls(data))
        if isinstance(data, dict):
            for item in (data.get("downloads") or data.get("data") or []):
                if isinstance(item, dict):
                    fname = item.get("filename") or item.get("name")
                    if fname:
                        urls.append(f"{base}/citations/{urllib.parse.quote(str(citation_id), safe='')}/downloads/{urllib.parse.quote(str(fname), safe='')}")
    except Exception:
        pass
    return list(dict.fromkeys(urls))
