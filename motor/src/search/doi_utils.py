import re

def normalize_doi(doi: str) -> str | None:
    """
    Normaliza uma string de DOI, removendo prefixos comuns, espaços e pontuações nas bordas.
    Retorna o DOI normalizado em letras minúsculas ou None se a string for inválida.
    """
    if not doi:
        return None
        
    # Remove whitespace
    doi = doi.strip().lower()
    
    # Remove URL prefixes
    prefixes_to_remove = [
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
        "dx.doi.org/",
        "doi.org/",
        "doi:",
        "doi "
    ]
    
    for prefix in prefixes_to_remove:
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
            
    # Clean edges
    doi = doi.strip(' \t\n\r"\'.,;:/')
    
    # Validação básica de DOI (começa com 10.)
    if not doi.startswith("10."):
        return None
        
    return doi

def deduplicate_dois(dois: list[str]) -> list[str]:
    """
    Recebe uma lista de DOIs sujos/repetidos, normaliza todos e retorna
    uma lista única, em ordem alfabética. DOIs inválidos ou nulos são ignorados.
    """
    unique_dois = set()
    for doi in dois:
        norm = normalize_doi(doi)
        if norm:
            unique_dois.add(norm)
            
    return sorted(list(unique_dois))

