import os
import re
import sys
from dotenv import load_dotenv

SEARCH_DIR = os.path.dirname(os.path.abspath(__file__))
if SEARCH_DIR not in sys.path:
    sys.path.insert(0, SEARCH_DIR)
OUTPUT_DIR = os.path.join(SEARCH_DIR, "output")

from doi_utils import deduplicate_dois
from bases import BASES, resolve_base
from fallback_search import search_web_for_missing_articles

def ler_queries_do_arquivo(filepath="quary.txt") -> dict[str, str]:
    """
    Lê o arquivo de texto e retorna um dicionário de queries.
    Se o arquivo tiver seções como [PUBMED], separa por base.
    Se não, usa a mesma query para todas (chave 'DEFAULT').
    """
    if not os.path.exists(filepath) and not os.path.isabs(filepath):
        # O backend grava quary.txt em src/search; não depender do cwd.
        candidate = os.path.join(SEARCH_DIR, filepath)
        if os.path.exists(candidate):
            filepath = candidate
    if not os.path.exists(filepath):
        print(f"Erro: Arquivo '{filepath}' não encontrado.")
        sys.exit(1)
        
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read().strip()
        
    queries = {}
    
    # Verifica se há seções (ex: [PUBMED], [EMBASE], [SCOPUS]...)
    nomes = "|".join(list(BASES) + ["DEFAULT"])
    secoes = re.split(rf"\[({nomes})\]", content, flags=re.IGNORECASE)
    
    if len(secoes) > 1:
        # Pula o primeiro elemento se for vazio (antes da primeira seção)
        i = 1 if not secoes[0].strip() else 0
        while i < len(secoes) - 1:
            if secoes[i].upper() in BASES or secoes[i].upper() == "DEFAULT":
                base_name = secoes[i].upper()
                query = secoes[i+1].strip()
                if query:
                    queries[base_name] = query
                i += 2
            else:
                i += 1
    else:
        queries["DEFAULT"] = content
        
    return queries

def query_for(queries: dict[str, str], base: str) -> str | None:
    """Query da base: a da seção própria, senão DEFAULT, senão a primeira seção
    presente (os conectores removem a sintaxe específica do PubMed)."""
    key = resolve_base(base) or base.upper()
    return queries.get(key) or queries.get("DEFAULT") or next(iter(queries.values()), None)


# O fluxo interativo (menu de bases, resumo no terminal) vivia aqui e foi
# removido junto com a interface própria do motor. A execução não interativa
# está em `headless_runner.py`, que é o que o ORBIS chama.
