"""Execução não interativa da busca (usada pelo backend via SSE).

Uso:
    python headless_runner.py --bases PubMed,Embase,LILACS,EuropePMC,OpenAlex,Scopus --ufmg
    python headless_runner.py --bases "" --no-ufmg

Os parâmetros chegam por argumentos de linha de comando (nunca por código
gerado), e todos os caminhos são resolvidos a partir deste arquivo, de modo
que o resultado não depende do diretório de trabalho atual.
"""
import argparse
import os
import sys
from urllib.parse import quote_plus

from dotenv import load_dotenv

SEARCH_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(SEARCH_DIR))
ANALYSIS_DIR = os.path.join(ROOT_DIR, "src", "analysis")
OUTPUT_DIR = os.path.join(SEARCH_DIR, "output")

if SEARCH_DIR not in sys.path:
    sys.path.insert(0, SEARCH_DIR)
load_dotenv(os.path.join(ROOT_DIR, ".env"))

from main import ler_queries_do_arquivo, query_for
from doi_utils import deduplicate_dois
from bases import BASES, resolve_base
from fallback_search import search_web_for_missing_articles



def display_results_summary(results: dict[str, int], total_unique: int,
                            total_duplicates: int, total_no_doi: int) -> None:
    """Resumo da busca no stdout, lido pelo ORBIS linha a linha.

    Morava em `cli_menu.py`, junto do menu interativo. Com a interface própria
    do motor removida, o que sobrou é isto: texto simples, sem prompt.
    """
    for base, count in results.items():
        print(f"{base}: {count} artigos")
    print("-" * 30)
    print(f"Total bruto: {sum(results.values())}")
    print(f"DOIs únicos: {total_unique} -> output/dois_extraidos.txt")
    print(f"Duplicatas removidas: {total_duplicates}")
    print(f"Sem DOI (logados): {total_no_doi} -> output/sem_doi.txt")

def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Busca headless de DOIs")
    parser.add_argument(
        "--bases",
        default=os.environ.get("SEARCH_BASES", "PubMed,Embase,LILACS"),
        help="Lista separada por vírgulas: " + ",".join(label for label, _fn in BASES.values()) + " (vazio = nenhuma)",
    )
    parser.add_argument("--ufmg", dest="ufmg", action="store_true", default=True,
                        help="Usa o repositório UFMG no fallback web (padrão)")
    parser.add_argument("--no-ufmg", dest="ufmg", action="store_false",
                        help="Não consulta o repositório UFMG no fallback web")
    parser.add_argument("--query-file", default=os.path.join(SEARCH_DIR, "quary.txt"))
    args = parser.parse_args(argv)

    bases = []
    for raw in args.bases.split(","):
        if not raw.strip():
            continue
        key = resolve_base(raw)
        if key is None:
            parser.error(f"base desconhecida: {raw.strip()!r}")
        label = BASES[key][0]
        if label not in bases:
            bases.append(label)
    args.bases = bases
    return args


def run(bases, use_ufmg=True, query_file=None):
    queries = ler_queries_do_arquivo(query_file or os.path.join(SEARCH_DIR, "quary.txt"))

    resultados_contagem = {}
    todos_dois_brutos, todos_sem_doi = [], []

    if not bases:
        print("⚠️ Nenhuma base primária foi acionada. Pulando direto para varredura secundária...", flush=True)

    for base in bases:
        print(f"\n--- Processando {base} ---", flush=True)
        query = query_for(queries, base)
        if not query:
            print(f"Aviso: Nenhuma query encontrada para {base}. Pulando.", flush=True)
            resultados_contagem[base] = 0
            continue

        count, dois, no_doi = 0, [], []
        try:
            count, dois, no_doi = BASES[resolve_base(base)][1](query)

            resultados_contagem[base] = count
            todos_dois_brutos.extend(dois)
            todos_sem_doi.extend(no_doi)
        except Exception as e:
            print(f"[{base}] Erro ao processar: {str(e)}", flush=True)
            resultados_contagem[base] = 0

    print("\n--- Processamento concluído. Extraindo DOIs únicos... ---", flush=True)

    dois_unicos = deduplicate_dois(todos_dois_brutos)
    duplicatas = len(todos_dois_brutos) - len(dois_unicos)

    try:
        if ANALYSIS_DIR not in sys.path:
            sys.path.append(ANALYSIS_DIR)
        from prisma_manager import PrismaManager
        # Mesmo diretório usado por src/analysis/main.py (settings.output_dir),
        # para que ambos leiam/escrevam <raiz>/data/prisma_state.json.
        prisma = PrismaManager(os.path.join(ROOT_DIR, "relatorio"))
        prisma.update_identification(list(bases), len(todos_dois_brutos), len(todos_sem_doi), duplicatas)
    except Exception as e:
        print(f"Aviso: Falha ao atualizar PRISMA: {e}", flush=True)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    def out(name):
        return os.path.join(OUTPUT_DIR, name)

    with open(out("dois_extraidos.txt"), "w", encoding="utf-8") as f:
        for d in dois_unicos: f.write(f"{d}\n")
    with open(out("sem_doi.txt"), "w", encoding="utf-8") as f:
        for r in todos_sem_doi: f.write(f"{r}\n")
    with open(out("DOI's.txt"), "w", encoding="utf-8") as f:
        for d in dois_unicos: f.write(f"{d}\n")
    with open(out("Artigos.txt"), "w", encoding="utf-8") as f:
        for d in dois_unicos: f.write(f"{d}\n")
        for r in todos_sem_doi: f.write(f"{r}\n")

    with open(out("artigos_com_links.txt"), "w", encoding="utf-8") as f:
        for d in dois_unicos:
            f.write(f"DOI: {d}\n")
            f.write(f"Link: https://doi.org/{d}\n")
        if todos_sem_doi:
            f.write("--- ARTIGOS SEM DOI ---\n")
            for r in todos_sem_doi:
                f.write(f"Título: {r}\n")
                f.write(f"Link: https://scholar.google.com/scholar?q={quote_plus(chr(34) + r + chr(34))}\n")

    if todos_sem_doi:
        search_web_for_missing_articles(todos_sem_doi, out("manual_review_links.txt"), use_ufmg=use_ufmg)

    display_results_summary(
        results=resultados_contagem,
        total_unique=len(dois_unicos),
        total_duplicates=duplicatas,
        total_no_doi=len(todos_sem_doi)
    )


if __name__ == "__main__":
    _args = _parse_args()
    run(_args.bases, use_ufmg=_args.ufmg, query_file=_args.query_file)
