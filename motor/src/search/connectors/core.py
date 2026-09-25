"""CORE: agregador de repositórios institucionais de acesso aberto (~300 M registros). Requer CORE_API_KEY."""
import os

from query_utils import clean_doi, get_json, max_results, to_plain_boolean

SEARCH_URL = "https://api.core.ac.uk/v3/search/works/"
PAGE_SIZE = 100
CORE_OFFSET_CAP = 10000  # além disso a API exige scroll


def fetch_core_dois(query: str) -> tuple[int, list[str], list[str]]:
    api_key = os.getenv("CORE_API_KEY")
    if not api_key:
        print("[CORE] Pulando: configure CORE_API_KEY no .env (https://core.ac.uk/services/api).", flush=True)
        return 0, [], []
    headers = {"Authorization": f"Bearer {api_key}"}
    q = to_plain_boolean(query)
    limit = min(max_results(), CORE_OFFSET_CAP)
    print("🏛️ [CORE] Varrendo repositórios institucionais de acesso aberto...", flush=True)

    dois, no_doi = [], []
    total, offset = 0, 0
    while offset < limit:
        data = get_json(SEARCH_URL, params={"q": q, "limit": min(PAGE_SIZE, limit - offset), "offset": offset},
                        headers=headers, attempts=4)
        total = int(data.get("totalHits") or 0)
        works = data.get("results") or []
        if not works:
            break
        for work in works:
            doi = clean_doi(work.get("doi"))
            if doi:
                dois.append(doi)
            elif work.get("title"):
                no_doi.append(work["title"])
        offset += len(works)
        if offset >= total:
            break

    print(f"🎯 [CORE] {total} registros encontrados.", flush=True)
    return total, dois, no_doi
