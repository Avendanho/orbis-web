import requests
import os
import time
import xml.etree.ElementTree as ET
import csv

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")
REQUEST_TIMEOUT = 60


def _post_with_retry(url: str, data: dict, attempts: int = 3) -> requests.Response:
    """POST com timeout e novas tentativas para erros transitórios (rede, 429, 5xx)."""
    last_exc = None
    for attempt in range(attempts):
        try:
            resp = requests.post(url, data=data, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as e:
            last_exc = e
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status is not None and status < 500 and status != 429:
                raise
            time.sleep(2 * (attempt + 1))
    raise last_exc


def _text(elem) -> str:
    """Texto completo do elemento, incluindo tags internas (<i>, <sup>...)."""
    if elem is None:
        return ""
    return "".join(elem.itertext()).strip()


def fetch_pubmed_dois(query: str) -> tuple[int, list[str], list[str]]:
    """
    Executa a busca no PubMed para a query dada.
    Retorna (total_count, list_of_dois, list_of_pmids_without_doi).
    Também salva um CSV com as informações de todos os artigos em output/pubmed_articles.csv.
    """
    api_key = os.getenv("NCBI_API_KEY")
    email = os.getenv("NCBI_EMAIL", "test@example.com")
    tool = os.getenv("NCBI_TOOL_NAME", "doi_extractor")
    
    # 1. Busca os PMIDs (esearch)
    search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    search_params = {
        "db": "pubmed",
        "term": query,
        "retmode": "json",
        "retmax": 10000,
        "tool": tool,
        "email": email,
        "usehistory": "y"
    }
    if api_key:
        search_params["api_key"] = api_key
        
    print("🧠 [PubMed] Traduzindo complexidade! Mapeando termos MESH e iniciando busca profunda...")
    search_resp = _post_with_retry(search_url, search_params)
    search_data = search_resp.json()
    
    count = int(search_data["esearchresult"]["count"])
    pmids = search_data["esearchresult"]["idlist"]
    
    print(f"🎯 [PubMed] Bingo! Localizamos {count} artigos promissores na base.")
    
    if count == 0:
        return 0, [], []
    if count > len(pmids):
        print(f"[PubMed] Aviso: a API retorna no máximo {len(pmids)} de {count} PMIDs; refine a query para cobrir todos.", flush=True)
        
    # 2. Busca os dados completos para extrair DOI e outros metadados (efetch) em lotes
    dois = []
    no_doi_pmids = []
    all_articles_info = []
    
    fetch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    
    batch_size = 200
    for i in range(0, len(pmids), batch_size):
        batch_pmids = pmids[i:i+batch_size]
        
        fetch_params = {
            "db": "pubmed",
            "id": ",".join(batch_pmids),
            "retmode": "xml",
            "rettype": "abstract",
            "tool": tool,
            "email": email
        }
        if api_key:
            fetch_params["api_key"] = api_key
            
        try:
            fetch_resp = _post_with_retry(fetch_url, fetch_params)
            root = ET.fromstring(fetch_resp.content)
        except (requests.exceptions.RequestException, ET.ParseError) as e:
            # Um lote com falha não deve descartar todos os resultados já obtidos
            print(f"[PubMed] Falha no lote {i // batch_size + 1} ({len(batch_pmids)} PMIDs): {e}", flush=True)
            no_doi_pmids.extend(f"PMID {p}" for p in batch_pmids)
            continue
        
        for article in root.findall(".//PubmedArticle"):
            pmid_elem = article.find(".//PMID")
            pmid = pmid_elem.text if pmid_elem is not None else "Unknown"
            
            title = _text(article.find(".//ArticleTitle"))
            
            journal_elem = article.find(".//Journal/Title")
            journal = journal_elem.text if journal_elem is not None else ""
            
            year_elem = article.find(".//PubDate/Year")
            if year_elem is None:
                year_elem = article.find(".//ArticleDate/Year")
            year = year_elem.text if year_elem is not None else ""
            
            doi = None
            article_id_list = article.find(".//PubmedData/ArticleIdList")
            if article_id_list is not None:
                for article_id in article_id_list.findall("ArticleId"):
                    if article_id.get("IdType") == "doi":
                        doi = article_id.text
                        break
            
            all_articles_info.append({
                "PMID": pmid,
                "Title": title,
                "Journal": journal,
                "Year": year,
                "DOI": doi if doi else ""
            })
            
            if doi:
                dois.append(doi)
            else:
                fallback_name = title if title else f"PMID {pmid}"
                no_doi_pmids.append(fallback_name)
                
        # Rate limit control
        time.sleep(0.34 if not api_key else 0.11)
        
    # Salvar CSV
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    csv_filepath = os.path.join(OUTPUT_DIR, "pubmed_articles.csv")
    with open(csv_filepath, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["PMID", "Title", "Journal", "Year", "DOI"])
        writer.writeheader()
        writer.writerows(all_articles_info)
        
    print(f"📁 [PubMed] Metadados organizados em CSV ({csv_filepath}).")
        
    return count, dois, no_doi_pmids

