import requests
import re
import time

def fetch_lilacs_dois(query: str) -> tuple[int, list[str], list[str]]:
    """
    Busca na base LILACS via Portal Regional da BVS.
    Como não há API oficial, fazemos requests simulando o browser para exportar em formato RIS.
    """
    url = "https://pesquisa.bvsalud.org/portal/"
    
    params = {
        "q": query,
        "filter[db][]": "LILACS",
        "output": "ris",
        "format": "summary",
        "count": 100,  # Max records per page for export
        "page": 1
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    
    print("🌎 [LILACS] Cruzando fronteiras! Garimpando publicações na América Latina e Caribe...")
    
    dois = []
    no_doi_records = []
    total_count = 0
    
    try:
        # Fazer primeira requisição para descobrir total
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        
        if resp.status_code == 403:
            print("[LILACS] Acesso bloqueado (Erro 403 - Desafio Antibot).")
            print("[LILACS] Não foi possível automatizar a busca pois o Portal BVS ativou proteção.")
            print("[LILACS] Por favor, siga as instruções de Fallback Manual no README.md")
            return 0, [], []
            
        resp.raise_for_status()
        
        # Como o formato é RIS, precisaremos inferir o total pelo total de registros ER - ou se a resposta contiver metadados.
        # Geralmente a resposta é um texto puro.
        text = resp.text
        
        # Parse simples do RIS
        # O RIS divide registros com "ER  - \n"
        records = text.split("ER  -")
        
        if len(records) <= 1: # Só 1 item geralmente significa vazio
            print("[LILACS] Nenhum artigo encontrado ou resposta vazia.")
            return 0, [], []
            
        # Para saber o total real, seria necessário olhar no HTML. 
        # Como estamos pedindo output=ris, vamos apenas paginar até não vir mais registros.
        print("[LILACS] Baixando registros em RIS...")
        
        max_pages = 200  # trava de segurança contra paginação infinita
        previous_text = None
        while True:
            # Se o portal ignorar o parâmetro de página e devolver sempre o mesmo
            # conteúdo, o laço nunca terminaria (e duplicaria registros).
            if resp.text == previous_text:
                break
            previous_text = resp.text
            records = resp.text.split("ER  -")
            records = [r for r in records if r.strip()]
            
            if not records:
                break
                
            total_count += len(records)
            
            for i, record in enumerate(records):
                # Procura a linha com "DO  -"
                match = re.search(r"^DO\s*-\s*(.+)$", record, re.MULTILINE)
                if match:
                    dois.append(match.group(1).strip())
                else:
                    # Tenta pegar o título
                    ti_match = re.search(r"^TI\s*-\s*(.+)$", record, re.MULTILINE)
                    title = ti_match.group(1).strip() if ti_match else f"Registro_{total_count - len(records) + i + 1}"
                    no_doi_records.append(title)
            
            # Paginando
            if len(records) < params["count"] or params["page"] >= max_pages:
                # Última página
                break
                
            params["page"] += 1
            time.sleep(1)  # Intervalo respeitoso
            
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            if resp.status_code != 200:
                break
                
        print(f"🎯 [LILACS] Sucesso! Capturamos {total_count} publicações regionais.")
        
    except Exception as e:
        print(f"[LILACS] Falha ao conectar: {e}")
        
    return total_count, dois, no_doi_records

