"""OpenAlex: índice aberto multidisciplinar (~250 M trabalhos, sucessor do Microsoft Academic)."""
import os

import requests

from query_utils import clean_doi, get_json, max_results, to_plain_boolean

WORKS_URL = "https://api.openalex.org/works"
PAGE_SIZE = 200


def fetch_openalex_dois(query: str) -> tuple[int, list[str], list[str]]:
    """Busca em título, resumo e texto completo (a API aceita AND/OR/NOT e aspas)."""
    q = to_plain_boolean(query)
    limit = max_results()
    params = {"search": q, "per-page": PAGE_SIZE, "select": "doi,display_name", "cursor": "*"}
    if os.getenv("OPENALEX_MAILTO"):
        params["mailto"] = os.getenv("OPENALEX_MAILTO")
    if os.getenv("OPENALEX_API_KEY"):
        params["api_key"] = os.getenv("OPENALEX_API_KEY")
    else:
        print("[OpenAlex] Dica: sem OPENALEX_API_KEY a cota diária gratuita é pequena.", flush=True)
    print("🌐 [OpenAlex] Varrendo o índice aberto multidisciplinar...", flush=True)

    dois, no_doi = [], []
    total, seen = 0, 0
    while seen < limit:
        try:
            data = get_json(WORKS_URL, params=params)
        except requests.HTTPError as e:
            if getattr(e.response, "status_code", None) == 429:
                print("[OpenAlex] Cota diária esgotada (HTTP 429). Crie uma chave gratuita em "
                      "https://openalex.org/settings/api e defina OPENALEX_API_KEY no .env.", flush=True)
                if seen:
                    break
            raise
        total = int((data.get("meta") or {}).get("count") or 0)
        results = data.get("results") or []
        if not results:
            break
        for work in results[: limit - seen]:
            doi = clean_doi(work.get("doi"))
            if doi:
                dois.append(doi)
            elif work.get("display_name"):
                no_doi.append(work["display_name"])
        seen += len(results)
        cursor = (data.get("meta") or {}).get("next_cursor")
        if not cursor:
            break
        params["cursor"] = cursor

    if total > seen:
        print(f"[OpenAlex] Aviso: {total} resultados; baixados {min(seen, limit)} (ajuste SEARCH_MAX_RESULTS).", flush=True)
    print(f"🎯 [OpenAlex] {total} registros encontrados.", flush=True)
    return total, dois, no_doi
