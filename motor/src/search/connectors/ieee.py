"""IEEE Xplore: engenharia, computação e engenharia biomédica. Requer IEEE_API_KEY."""
import os
import time

from query_utils import clean_doi, get_json, max_results, to_plain_boolean

SEARCH_URL = "https://ieeexploreapi.ieee.org/api/v1/search/articles"
PAGE_SIZE = 200


def fetch_ieee_dois(query: str) -> tuple[int, list[str], list[str]]:
    api_key = os.getenv("IEEE_API_KEY")
    if not api_key:
        print("[IEEE Xplore] Pulando: configure IEEE_API_KEY no .env (https://developer.ieee.org/).", flush=True)
        return 0, [], []
    q = to_plain_boolean(query)
    limit = max_results()
    print("⚡ [IEEE Xplore] Consultando engenharia e computação...", flush=True)

    dois, no_doi = [], []
    total, seen = 0, 0
    while seen < limit:
        data = get_json(SEARCH_URL, params={
            "querytext": q, "apikey": api_key, "format": "json",
            "max_records": min(PAGE_SIZE, limit - seen), "start_record": seen + 1,
        })
        total = int(data.get("total_records") or 0)
        articles = data.get("articles") or []
        if not articles:
            break
        for art in articles:
            doi = clean_doi(art.get("doi"))
            if doi:
                dois.append(doi)
            elif art.get("title"):
                no_doi.append(art["title"])
        seen += len(articles)
        if seen >= total:
            break
        time.sleep(0.2)  # limite da API: 10 chamadas/s

    print(f"🎯 [IEEE Xplore] {total} registros encontrados.", flush=True)
    return total, dois, no_doi
