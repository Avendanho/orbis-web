"""PubMed, PubMed Central e Europe PMC.

O corpus biomédico tem uma particularidade que o resto não tem: quase todo
artigo está indexado no PubMed, e boa parte tem cópia depositada no PMC. Por
isso a cadeia aqui é ``DOI → PMID → PMCID`` — o DOI é o identificador da
editora, mas o que leva ao arquivo é o PMCID.

Desde a migração de agosto/2026, as páginas de artigo do PMC respondem com um
desafio reCAPTCHA a clientes HTTP e o serviço ``oa.fcgi`` foi desligado. A via
que continua servindo texto é o bucket na AWS, tratado em ``pmc_s3.py``; o que
existe aqui são os resolvedores de identificador e os links que o Europe PMC
agrega.

O acesso ao cliente HTTP e ao emissor de eventos passa por ``runtime`` — veja
lá por que não é um import direto de ``fetch``.
"""
from __future__ import annotations

import os
import time
import urllib.parse
import xml.etree.ElementTree as ET

import runtime
from identity import normalize_doi

def _norm_pmcid(pmcid: str) -> str:
    pmcid = str(pmcid).strip().upper()
    return pmcid if pmcid.startswith("PMC") else f"PMC{pmcid}"


def try_pmc(pmcid: str, *, timeout: int = 15) -> str | None:
    """PDF of a PMC article from the PMC Cloud Service (public AWS bucket).

    Since 2026 the PMC website answers /articles/<id>/pdf/ with a reCAPTCHA
    page and the OA web service (oa.fcgi) is retired; NCBI's supported route
    is the open `pmc-oa-opendata` bucket, which serves the article PDF
    directly for the OA subset and author manuscripts. Objects are keyed by
    version (PMC123.1/PMC123.1.pdf), so the bucket is listed to find the
    latest one. Returns None when the article has no PDF there.
    """
    pmcid = _norm_pmcid(pmcid)
    params = urllib.parse.urlencode({"list-type": "2", "prefix": f"{pmcid}.", "max-keys": "100"})
    try:
        body = runtime._get(f"{runtime.PMC_S3_BUCKET_URL}/?{params}", accept="application/xml", timeout=timeout).decode("utf-8", "replace")
    except Exception:
        return None
    keys = [(int(ver), key) for key, ver in runtime._PMC_S3_KEY_RE.findall(body)]
    if not keys:
        return None
    return f"{runtime.PMC_S3_BUCKET_URL}/{max(keys)[1]}"


def try_europe_pmc(pmcid: str) -> str:
    """Europe PMC's PDF render of a PMC article.

    Covers "free to read" PMC articles that are not in the PMC open-data
    bucket. Plain clients get a 403 here, but the browser-TLS transport used
    by _download is served the PDF.
    """
    return f"https://europepmc.org/articles/{_norm_pmcid(pmcid)}?pdf=render"


def try_pmc_idconv(identifier: str, *, timeout: int) -> dict:
    """Map a DOI or PMID to its PMC ids with NCBI's official ID converter.

    Returns {"pmcid": ..., "pmid": ...} (keys absent when unknown).
    """
    params = {"ids": identifier, "format": "json", "tool": "paper-fetch"}
    email = os.environ.get("NCBI_EMAIL", "").strip() or runtime.EMAIL
    if email:
        params["email"] = email
    url = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/?" + urllib.parse.urlencode(params)
    try:
        data = runtime._get_json(url, timeout=timeout)
    except Exception:
        return {}
    for rec in data.get("records") or []:
        if rec.get("status") == "error":
            continue
        out = {}
        if rec.get("pmcid"):
            out["pmcid"] = _norm_pmcid(rec["pmcid"])
        if rec.get("pmid"):
            out["pmid"] = str(rec["pmid"])
        return out
    return {}


def try_europe_pmc_links(doi: str, *, timeout: int, errors: list | None = None) -> tuple[list[str], str | None]:
    """Open-access full-text links Europe PMC aggregates for a DOI.

    Europe PMC's `fullTextUrlList` merges publisher, Unpaywall and repository
    locations; only entries flagged open/free access are returned, PDFs first.
    Also returns the PMCID when Europe PMC knows it.
    """
    params = urllib.parse.urlencode({
        "query": f'DOI:"{doi}"', "format": "json", "pageSize": "1", "resultType": "core",
    })
    data = {}
    for _attempt in range(3):
        try:
            data = runtime._get_json("https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + params, timeout=timeout)
        except Exception as e:
            if errors is not None and runtime._is_transport_exc(e):
                errors.append({"source": "europe_pmc", "detail": str(e)})
            return [], None
        # The API intermittently answers 200 with only {"version": ...}.
        if "hitCount" in data:
            break
        time.sleep(0.5)
    results = (data.get("resultList") or {}).get("result") or []
    if not results:
        return [], None
    rec = results[0]
    if normalize_doi(rec.get("doi") or "") != normalize_doi(doi):
        return [], None
    pdfs, others = [], []
    for entry in (rec.get("fullTextUrlList") or {}).get("fullTextUrl") or []:
        url = entry.get("url")
        if not url or entry.get("availabilityCode") not in ("OA", "F"):
            continue
        # europepmc.org article pages answer bots with 403; the PMC bucket covers them.
        if "europepmc.org" in url:
            continue
        bucket = pdfs if entry.get("documentStyle") == "pdf" else others
        if url not in bucket:
            bucket.append(url)
    pmcid = _norm_pmcid(rec["pmcid"]) if rec.get("pmcid") else None
    return pdfs + [u for u in others if u not in pdfs], pmcid


def try_europe_pmc_by_doi(
    doi: str,
    *,
    timeout: int,
    errors: list | None = None,
) -> str | None:
    """Resolve um DOI diretamente para PMCID usando a API do Europe PMC."""

    params = urllib.parse.urlencode({
        "query": f'DOI:"{doi}"',
        "format": "json",
        "pageSize": "1",
    })

    url = (
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
        + params
    )

    try:
        data = runtime._get_json(url, timeout=timeout)

    except Exception as e:
        if errors is not None and runtime._is_transport_exc(e):
            errors.append({
                "source": "europe_pmc",
                "detail": str(e),
            })

        return None

    resultados = (
        data
        .get("resultList", {})
        .get("result", [])
    )

    if not resultados:
        return None

    artigo = resultados[0]

    pmcid = artigo.get("pmcid")

    if not pmcid:
        return None

    pmcid = str(pmcid).strip().upper()

    if not pmcid.startswith("PMC"):
        pmcid = f"PMC{pmcid}"

    return pmcid


def try_pmcid_from_pmid(pmid: str, *, timeout: int) -> str | None:
    """Convert a PMID to its PMCID (NCBI ID converter, then PubMed efetch).

    PubMed's efetch XML carries the PMCID as <ArticleId IdType="pmc">; the
    JATS-style <article-id pub-id-type="pmc"> only appears in PMC's own XML.
    """
    pmid = "".join(filter(str.isdigit, str(pmid)))
    if not pmid:
        return None
    pmcid = try_pmc_idconv(pmid, timeout=timeout).get("pmcid")
    if pmcid:
        return pmcid
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={pmid}&retmode=xml"
    try:
        root = ET.fromstring(runtime._get(url, accept="application/xml", timeout=timeout))
    except Exception:
        return None
    for tag, attr in (("ArticleId", "IdType"), ("article-id", "pub-id-type")):
        for el in root.iter(tag):
            if el.get(attr) == "pmc" and el.text and el.text.strip():
                return _norm_pmcid(el.text)
    return None


def try_pmid(
    doi: str,
    *,
    timeout: int,
    errors: list | None = None,
) -> str | None:
    """Extract PMID from DOI using external IDs from Semantic Scholar or Crossref.

    First tries to get PMID from Semantic Scholar's externalIds, then falls back
    to Crossref if needed.
    """
    # Try Semantic Scholar first (often has good external IDs)
    try:
        url = (
            f"https://api.semanticscholar.org/graph/v1/paper/DOI:{urllib.parse.quote(doi)}"
            "?fields=externalIds"
        )
        d = runtime._get_json(url, timeout=timeout)
        ext_ids = d.get("externalIds") or {}
        pmid = ext_ids.get("PMID")
        if pmid:
            # Normalize PMID - just digits
            pmid_digits = ''.join(filter(str.isdigit, str(pmid)))
            if pmid_digits:
                return pmid_digits
    except Exception:
        pass

    # Fallback to Crossref
    try:
        url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
        d = runtime._get_json(url, timeout=timeout)
        # Crossref might have PMID in alternative-id or similar
        alt_ids = (d.get("message") or {}).get("alternative-id") or []
        for alt_id in alt_ids:
            if isinstance(alt_id, str) and alt_id.upper().startswith("PMID"):
                # Extract digits from PMID:12345 format
                pmid_digits = ''.join(filter(str.isdigit, alt_id))
                if pmid_digits:
                    return pmid_digits
    except Exception:
        pass

    return None
