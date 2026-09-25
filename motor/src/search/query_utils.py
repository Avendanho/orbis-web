"""Utilitários compartilhados pelos conectores de busca.

- ``to_plain_boolean``: converte uma query no estilo PubMed (com etiquetas de
  campo como ``[MeSH Terms]`` e ``[tiab]``) em uma expressão booleana simples
  que as demais bases entendem (AND / OR / NOT, aspas e parênteses).
- ``get_json``: GET com timeout e novas tentativas para erros transitórios.
- ``max_results``: teto de registros por base (``SEARCH_MAX_RESULTS``).
"""
import os
import re
import time

import requests

REQUEST_TIMEOUT = 60
USER_AGENT = "revisao-sistematica/1.0 (mailto:{})".format(
    os.getenv("CROSSREF_MAILTO") or os.getenv("UNPAYWALL_EMAIL") or "anonymous"
)

# Etiquetas de campo do PubMed: [MeSH Terms], [tiab], [Title/Abstract], [pt]...
_PUBMED_TAG_RE = re.compile(r"\[[A-Za-z /:_-]+\]")
# Truncamento/curinga do PubMed (termo*) é aceito pela maioria das bases, então é mantido.


def to_plain_boolean(query: str) -> str:
    """Remove etiquetas de campo do PubMed e normaliza espaços e operadores."""
    q = _PUBMED_TAG_RE.sub("", query or "")
    q = re.sub(r"\b(and|or|not)\b", lambda m: m.group(1).upper(), q)
    q = re.sub(r"\s+", " ", q)
    q = re.sub(r"\(\s+", "(", q)
    q = re.sub(r"\s+\)", ")", q)
    return q.strip()


def max_results(default: int = 2000) -> int:
    """Quantidade máxima de registros baixados por base (SEARCH_MAX_RESULTS)."""
    try:
        return max(1, int(os.getenv("SEARCH_MAX_RESULTS", default)))
    except ValueError:
        return default


def get_json(url: str, *, params=None, headers=None, attempts: int = 3, timeout: int = REQUEST_TIMEOUT):
    """GET que devolve JSON, com novas tentativas para rede, 429 e 5xx."""
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    hdrs.update(headers or {})
    last_exc = None
    for attempt in range(attempts):
        try:
            resp = requests.get(url, params=params, headers=hdrs, timeout=timeout)
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("Retry-After", "")
                delay = int(retry_after) if retry_after.isdigit() else 2 * (attempt + 1)
                last_exc = requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
                if delay > 60 or attempt == attempts - 1:
                    # Cota esgotada (Retry-After em horas): esperar não adianta.
                    break
                time.sleep(delay)
                continue
            resp.raise_for_status()
            return resp.json()
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e
            time.sleep(2 * (attempt + 1))
    raise last_exc


def clean_doi(value) -> str | None:
    """Extrai um DOI de um valor que pode ser URL (https://doi.org/...) ou texto."""
    if not value:
        return None
    m = re.search(r"10\.\d{4,9}/\S+", str(value))
    return m.group(0).rstrip(" .,;") if m else None
