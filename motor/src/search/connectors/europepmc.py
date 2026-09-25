"""Europe PMC: PubMed/MEDLINE + PMC + preprints (bioRxiv, medRxiv...) + Agricola + patentes."""
import time

from query_utils import clean_doi, get_json, max_results, to_plain_boolean

SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
PAGE_SIZE = 1000


def _search_page(q: str, page_size: int, cursor: str, attempts: int = 5) -> dict:
    """Uma página da busca. A API às vezes responde 200 só com {"version": ...}
    (sem hitCount nem resultados); nesse caso a requisição é repetida."""
    data = {}
    for attempt in range(attempts):
        data = get_json(SEARCH_URL, params={
            "query": q, "format": "json", "resultType": "lite",
            "pageSize": page_size, "cursorMark": cursor,
        })
        if "hitCount" in data:
            return data
        time.sleep(1 + attempt)
    print("[Europe PMC] Aviso: a API devolveu respostas vazias repetidamente.", flush=True)
    return data


def fetch_europepmc_dois(query: str) -> tuple[int, list[str], list[str]]:
    """Busca na Europe PMC (sintaxe booleana própria; aceita AND/OR/NOT e aspas)."""
    q = to_plain_boolean(query)
    limit = max_results()
    print("🇪🇺 [Europe PMC] Consultando MEDLINE, PMC e preprints...", flush=True)

    dois, no_doi = [], []
    total, seen, cursor = 0, 0, "*"
    while seen < limit:
        data = _search_page(q, min(PAGE_SIZE, limit - seen), cursor)
        total = int(data.get("hitCount") or 0)
        results = (data.get("resultList") or {}).get("result") or []
        if not results:
            break
        for rec in results:
            doi = clean_doi(rec.get("doi"))
            if doi:
                dois.append(doi)
            else:
                no_doi.append(rec.get("title") or f"{rec.get('source', 'EPMC')} {rec.get('id', '')}".strip())
        seen += len(results)
        next_cursor = data.get("nextCursorMark")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor

    if total > seen:
        print(f"[Europe PMC] Aviso: {total} resultados; baixados {seen} (ajuste SEARCH_MAX_RESULTS para mais).", flush=True)
    print(f"🎯 [Europe PMC] {total} registros encontrados.", flush=True)
    return total, dois, no_doi
