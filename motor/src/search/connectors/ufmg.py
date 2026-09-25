import requests
import json
import urllib.parse
from bs4 import BeautifulSoup
import time

def fetch_ufmg_dois(query: str) -> tuple[int, list[str], list[str]]:
    """
    Realiza busca no Repositório Institucional da UFMG (DSpace) e no portal Periódicos CAPES/UFMG (simulado via web/metadata).
    Retorna (total_count, list_of_dois, list_of_titles_without_doi).
    """
    dois = []
    titles_no_doi = []
    
    # Busca primária no repositório DSpace da UFMG (Teses e Dissertações)
    # Exemplo: https://repositorio.ufmg.br/server/api/discover/search/objects?query=...
    base_url = "https://repositorio.ufmg.br/server/api/discover/search/objects"
    params = {
        "query": query,
        "page": 0,
        "size": 50
    }
    
    try:
        response = requests.get(base_url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            embedded = data.get('_embedded', {}).get('searchResult', {}).get('_embedded', {}).get('objects', [])
            
            for obj in embedded:
                metadata = obj.get('_embedded', {}).get('indexableObject', {}).get('metadata', {})
                
                # Buscar DOI nos metadados
                doi_fields = metadata.get('dc.identifier.doi', [])
                title_fields = metadata.get('dc.title', [])
                
                title = title_fields[0].get('value') if title_fields else "Sem título"
                
                if doi_fields and doi_fields[0].get('value'):
                    doi = doi_fields[0].get('value')
                    # Normaliza o DOI
                    doi = doi.replace('https://doi.org/', '').replace('http://dx.doi.org/', '')
                    dois.append(doi)
                else:
                    titles_no_doi.append(title)
                    
    except Exception as e:
        print(f"[UFMG] Erro ao buscar no Repositório Institucional: {e}")

    # Fallback/Proxy Simulado para Periódicos CAPES UFMG (Mapeando para busca local/OpenAlex)
    # Como o portal CAPES não tem API pública, complementaremos as informações 
    # dos 'não encontrados' no fallback web.
    
    total = len(dois) + len(titles_no_doi)
    return total, dois, titles_no_doi

def search_ufmg_by_title(title: str) -> dict:
    """Busca um título específico no Repositório da UFMG e retorna os metadados (DOI ou Handle URL)."""
    base_url = "https://repositorio.ufmg.br/server/api/discover/search/objects"
    params = {
        # Aspas no título quebrariam a frase exata da query Solr/DSpace
        "query": 'dc.title:"{}"'.format(title.replace('"', ' ').replace('\\', ' ')),
        "page": 0,
        "size": 5
    }
    
    try:
        response = requests.get(base_url, params=params, timeout=5)
        if response.status_code == 200:
            data = response.json()
            embedded = data.get('_embedded', {}).get('searchResult', {}).get('_embedded', {}).get('objects', [])
            
            for obj in embedded:
                metadata = obj.get('_embedded', {}).get('indexableObject', {}).get('metadata', {})
                doi_fields = metadata.get('dc.identifier.doi', [])
                uri_fields = metadata.get('dc.identifier.uri', [])
                
                if doi_fields and doi_fields[0].get('value'):
                    doi = doi_fields[0].get('value').replace('https://doi.org/', '')
                    return {"found": True, "doi": doi, "url": f"https://doi.org/{doi}"}
                elif uri_fields:
                    handle = uri_fields[0].get('value')
                    return {"found": True, "doi": None, "url": handle}
    except Exception:
        pass
    
    return {"found": False, "doi": None, "url": None}

