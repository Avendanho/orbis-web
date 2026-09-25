import requests
import os

def fetch_embase_dois(query: str) -> tuple[int, list[str], list[str]]:
    """
    Executa a busca na API do Embase (Elsevier).
    Retorna (total_count, list_of_dois, list_of_records_without_doi).
    """
    api_key = os.getenv("EMBASE_API_KEY")
    inst_token = os.getenv("EMBASE_INST_TOKEN")
    
    if not api_key or not inst_token:
        print("[Embase] ERRO: Credenciais não encontradas no arquivo .env.")
        print("[Embase] Certifique-se de configurar EMBASE_API_KEY e EMBASE_INST_TOKEN.")
        return 0, [], []
        
    # Headers obrigatórios
    headers = {
        "X-ELS-APIKey": api_key,
        "X-ELS-Insttoken": inst_token,
        "Accept": "application/json"
    }
    
    # Endpoint de busca do Embase (tentando os dois formatos mais comuns)
    # A URL base padrão para busca no Embase via Elsevier API:
    url = "https://api.elsevier.com/content/search/embase"
    
    print("🔬 [Embase] Vasculhando a nata da pesquisa biomédica europeia...")
    
    # A paginação da Elsevier API normalmente usa `start` e `count` (max 200)
    count = 0
    dois = []
    no_doi_records = []
    
    start = 0
    batch_size = 100
    total_results = None
    
    while True:
        params = {
            "query": query,
            "start": start,
            "count": batch_size,
        }
        
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            
            search_results = data.get("search-results", {})
            if total_results is None:
                total_results = int(search_results.get("opensearch:totalResults", 0))
                print(f"🎯 [Embase] Radar apitou! {total_results} resultados encontrados.")
                
            entries = search_results.get("entry", [])
            if not entries:
                break
                
            for entry in entries:
                # Resultado vazio vem como [{"error": "Result set was empty"}]
                if "error" in entry:
                    continue
                doi = entry.get("prism:doi") or entry.get("doi")
                if doi:
                    dois.append(doi)
                else:
                    title = entry.get("dc:title", "Sem título")
                    no_doi_records.append(title)
                    
            start += batch_size
            if start >= total_results:
                break
                
        except (requests.exceptions.RequestException, ValueError) as e:
            print(f"[Embase] Erro na requisição: {e}")
            if getattr(e, "response", None) is not None:
                print(f"[Embase] Detalhes: {e.response.text[:500]}")
            break
            
    return total_results or 0, dois, no_doi_records

