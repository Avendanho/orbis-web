"""BASE (Bielefeld Academic Search Engine): o maior índice de repositórios de
acesso aberto do mundo (~400 milhões de documentos de milhares de repositórios).

A interface de busca da BASE só responde a endereços IP cadastrados — o
cadastro é gratuito e se pede em https://www.base-search.net/about/en/faq_data.php
(informe o IP de saída da sua rede). Enquanto o IP não for liberado, a API
devolve HTTP 200 com ``{"error": "Access denied for IP address ..."}``, e este
conector avisa e segue sem derrubar a busca nas demais bases.
"""
from query_utils import clean_doi, get_json, max_results, to_plain_boolean

SEARCH_URL = "https://api.base-search.net/cgi-bin/BaseHttpSearchInterface.fcgi"
PAGE_SIZE = 100


def fetch_base_dois(query: str) -> tuple[int, list[str], list[str]]:
    q = to_plain_boolean(query)
    limit = max_results()
    print("🗂️ [BASE] Consultando repositórios de acesso aberto do mundo todo...", flush=True)

    dois, no_doi = [], []
    total, offset = 0, 0
    while offset < limit:
        data = get_json(SEARCH_URL, params={
            "func": "PerformSearch", "query": q, "format": "json",
            "hits": min(PAGE_SIZE, limit - offset), "offset": offset, "boost": "oa",
        })
        denied = str((data or {}).get("error") or "")
        if denied:
            print(f"[BASE] Acesso negado: {denied}", flush=True)
            print("[BASE] Cadastre o IP em https://www.base-search.net/about/en/faq_data.php "
                  "(gratuito) para habilitar esta base.", flush=True)
            return 0, [], []

        response = (data or {}).get("response") or {}
        total = int(response.get("numFound") or 0)
        docs = response.get("docs") or []
        if not docs:
            break
        for doc in docs:
            doi = clean_doi(doc.get("dcdoi") or doc.get("dcidentifier"))
            if doi:
                dois.append(doi)
            else:
                title = doc.get("dctitle")
                if isinstance(title, list):
                    title = title[0] if title else None
                if title:
                    no_doi.append(title)
        offset += len(docs)
        if offset >= total:
            break

    if total > offset:
        print(f"[BASE] Aviso: {total} resultados; baixados {offset} (ajuste SEARCH_MAX_RESULTS).", flush=True)
    print(f"🎯 [BASE] {total} registros encontrados.", flush=True)
    return total, dois, no_doi
