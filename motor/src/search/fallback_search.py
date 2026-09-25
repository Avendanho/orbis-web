import requests
from requests.adapters import HTTPAdapter
import json
import os
import sys
import time
from urllib.parse import quote_plus
import concurrent.futures

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from connectors.ufmg import search_ufmg_by_title

# OpenAlex limita a ~10 req/s; mais threads que isso só geram HTTP 429.
MAX_WORKERS = 8

def process_single_fallback(item, use_ufmg, session):
    locations = []
    
    # 1. Fallback UFMG
    if use_ufmg:
        ufmg_res = search_ufmg_by_title(item)
        if ufmg_res["found"]:
            locations.append(f"Repositório UFMG: {ufmg_res['url']}")
            
    # 2. Fallback OpenAlex
    query = quote_plus(item)
    url = f"https://api.openalex.org/works?search={query}&per-page=3"
    mailto = os.getenv("OPENALEX_MAILTO")
    if mailto:
        url += f"&mailto={quote_plus(mailto)}"
    api_key = os.getenv("OPENALEX_API_KEY")
    if api_key:
        url += f"&api_key={quote_plus(api_key)}"
    
    try:
        resp = session.get(url, timeout=10)
        # Backoff simples para o limite de taxa do OpenAlex
        for attempt in range(3):
            if resp.status_code != 429:
                break
            time.sleep(1.5 * (attempt + 1))
            resp = session.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            works = data.get('results', [])
            
            for w in works:
                primary_loc = w.get('primary_location') or {}
                if primary_loc:
                    landing_page = primary_loc.get('landing_page_url')
                    pdf_url = primary_loc.get('pdf_url')
                    if landing_page: locations.append(f"Página: {landing_page}")
                    if pdf_url: locations.append(f"PDF direto: {pdf_url}")
                    
                # Verificar também em open access locations
                for oa_loc in w.get('locations') or []:
                    oa_url = (oa_loc or {}).get('landing_page_url')
                    if oa_url and oa_url not in "".join(locations):
                        locations.append(f"Alternativo: {oa_url}")
            
            # Deduplicar
            locations = list(set(locations))
            return {"original_query": item, "locations": locations}
        else:
            return {"original_query": item, "locations": ["Erro na API OpenAlex"]}
    except Exception as e:
        return {"original_query": item, "locations": [f"Falha na conexão: {str(e)}"]}

def search_web_for_missing_articles(missing_items: list[str], output_file: str = "output/manual_review_links.txt", use_ufmg: bool = True):
    if not missing_items:
        return
        
    print(f"\n[Fallback Web] Iniciando busca paralela para {len(missing_items)} artigos sem DOI...", flush=True)
    
    results = []
    session = requests.Session()
    # Pool de conexões do tamanho do número de threads (padrão do requests é 10)
    adapter = HTTPAdapter(pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_single_fallback, item, use_ufmg, session): item for item in missing_items}
        for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
            if i % 50 == 0:
                print(f"[Fallback Web] Processados {i}/{len(missing_items)}...", flush=True)
            try:
                res = future.result()
                results.append(res)
            except Exception as exc:
                item = futures[future]
                results.append({"original_query": item, "locations": [f"Erro interno: {exc}"]})
            
    # Salvar resultados
    out_parent = os.path.dirname(output_file)
    if out_parent:
        os.makedirs(out_parent, exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("=== RELATÓRIO DE REVISÃO MANUAL (ARTIGOS SEM DOI) ===\n\n")
        f.write("Estes artigos não possuíam DOI nas bases principais.\n")
        f.write("Abaixo estão os locais na Web onde eles estão registrados:\n\n")
        
        for r in results:
            f.write(f"Artigo/Query: {r['original_query']}\n")
            if r['locations']:
                for loc in r['locations']:
                    f.write(f"  -> {loc}\n")
            else:
                f.write("  -> Nenhum local encontrado na Web (Verifique o título).\n")
            f.write("-" * 60 + "\n")
            
    print(f"[Fallback Web] Concluído! Relatório salvo em: {output_file}", flush=True)

