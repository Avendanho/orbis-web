"""Oasisbr (IBICT): agregador brasileiro de acesso aberto — SciELO Brasil, repositórios de
universidades (USP, UFMG, Unicamp...), BDTD (teses e dissertações) e revistas nacionais."""
from query_utils import clean_doi, get_json, max_results, to_plain_boolean

SEARCH_URL = "https://oasisbr.ibict.br/vufind/api/v1/search"
PAGE_SIZE = 100


def fetch_oasisbr_dois(query: str) -> tuple[int, list[str], list[str]]:
    q = to_plain_boolean(query)
    limit = max_results()
    print("🇧🇷 [Oasisbr] Consultando a produção científica brasileira em acesso aberto...", flush=True)

    dois, no_doi = [], []
    total, seen, page = 0, 0, 1
    while seen < limit:
        data = get_json(SEARCH_URL, params=[
            ("lookfor", q), ("type", "AllFields"), ("limit", min(PAGE_SIZE, limit - seen)), ("page", page),
            ("field[]", "title"), ("field[]", "urls"), ("field[]", "dois"),
        ])
        total = int(data.get("resultCount") or 0)
        records = data.get("records") or []
        if not records:
            break
        for rec in records:
            candidates = list(rec.get("dois") or []) + [u.get("url") for u in (rec.get("urls") or []) if isinstance(u, dict)]
            doi = next((d for d in (clean_doi(c) for c in candidates) if d), None)
            if doi:
                dois.append(doi)
            elif rec.get("title"):
                no_doi.append(rec["title"])
        seen += len(records)
        page += 1
        if seen >= total:
            break

    print(f"🎯 [Oasisbr] {total} registros encontrados.", flush=True)
    return total, dois, no_doi
