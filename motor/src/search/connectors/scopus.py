"""Scopus (Elsevier): base multidisciplinar curada. Requer SCOPUS_API_KEY (ou ELSEVIER_API_KEY)."""
import os
import re

from query_utils import clean_doi, get_json, max_results, to_plain_boolean

SEARCH_URL = "https://api.elsevier.com/content/search/scopus"
PAGE_SIZE = 25          # máximo da visão STANDARD
SCOPUS_START_CAP = 5000  # a API não pagina além de start=5000 sem cursor COMPLETE

_FIELD_CODE_RE = re.compile(r"\b(TITLE-ABS-KEY|TITLE|ABS|KEY|AUTH|DOI|PUBYEAR|SRCTITLE|ALL|AFFIL|LANGUAGE|DOCTYPE)\s*\(", re.I)


def to_scopus_query(query: str) -> str:
    """Mantém queries já escritas em sintaxe Scopus; caso contrário, busca em título/resumo/palavras-chave."""
    if _FIELD_CODE_RE.search(query):
        return query.strip()
    return f"TITLE-ABS-KEY({to_plain_boolean(query)})"


def fetch_scopus_dois(query: str) -> tuple[int, list[str], list[str]]:
    api_key = os.getenv("SCOPUS_API_KEY") or os.getenv("ELSEVIER_API_KEY")
    if not api_key:
        print("[Scopus] Pulando: configure SCOPUS_API_KEY no .env (https://dev.elsevier.com/).", flush=True)
        return 0, [], []
    headers = {"X-ELS-APIKey": api_key}
    inst_token = os.getenv("ELSEVIER_INST_TOKEN")
    if inst_token:
        headers["X-ELS-Insttoken"] = inst_token

    q = to_scopus_query(query)
    limit = min(max_results(), SCOPUS_START_CAP)
    print("📚 [Scopus] Consultando a base curada da Elsevier...", flush=True)

    dois, no_doi = [], []
    total, start = 0, 0
    while start < limit:
        data = get_json(SEARCH_URL, params={"query": q, "start": start, "count": min(PAGE_SIZE, limit - start),
                                            "field": "prism:doi,dc:title"}, headers=headers)
        results = data.get("search-results") or {}
        total = int(results.get("opensearch:totalResults") or 0)
        entries = [e for e in (results.get("entry") or []) if "error" not in e]
        if not entries:
            break
        for entry in entries:
            doi = clean_doi(entry.get("prism:doi"))
            if doi:
                dois.append(doi)
            else:
                no_doi.append(entry.get("dc:title") or "Sem título")
        start += len(entries)
        if start >= total:
            break

    if total > start:
        print(f"[Scopus] Aviso: {total} resultados; baixados {start} (limite {limit}).", flush=True)
    print(f"🎯 [Scopus] {total} registros encontrados.", flush=True)
    return total, dois, no_doi
