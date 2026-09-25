"""Semantic Scholar: ~220 M artigos, forte em computação, engenharia e biomedicina."""
import os
import re
import time

from query_utils import clean_doi, get_json, max_results, to_plain_boolean

BULK_URL = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"


def to_s2_syntax(query: str) -> str:
    """AND/OR/NOT → + | - (sintaxe da busca em lote do Semantic Scholar)."""
    q = to_plain_boolean(query)
    q = re.sub(r"\s+AND\s+", " + ", q)
    q = re.sub(r"\s+OR\s+", " | ", q)
    q = re.sub(r"\bNOT\s+", "-", q)
    return q


def fetch_semantic_scholar_dois(query: str) -> tuple[int, list[str], list[str]]:
    limit = max_results()
    headers = {}
    if os.getenv("SEMANTIC_SCHOLAR_API_KEY"):
        headers["x-api-key"] = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
    params = {"query": to_s2_syntax(query), "fields": "externalIds,title"}
    print("🧭 [Semantic Scholar] Buscando no grafo acadêmico...", flush=True)

    dois, no_doi = [], []
    total, seen = 0, 0
    while seen < limit:
        data = get_json(BULK_URL, params=params, headers=headers, attempts=5)
        total = int(data.get("total") or 0)
        papers = data.get("data") or []
        if not papers:
            break
        for paper in papers[: limit - seen]:
            doi = clean_doi((paper.get("externalIds") or {}).get("DOI"))
            if doi:
                dois.append(doi)
            elif paper.get("title"):
                no_doi.append(paper["title"])
        seen += len(papers)
        token = data.get("token")
        if not token:
            break
        params["token"] = token
        if not headers:
            time.sleep(1.1)  # pool anônimo: ~1 req/s

    print(f"🎯 [Semantic Scholar] {total} registros encontrados.", flush=True)
    return total, dois, no_doi
