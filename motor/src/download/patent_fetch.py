import os
import urllib.request
import urllib.parse
import re
from pathlib import Path
from bs4 import BeautifulSoup
from curl_cffi import requests


def get_proxies():
    proxy = os.environ.get("PROXY_URL")
    return {"http": proxy, "https": proxy} if proxy else None

def search_duckduckgo_for_patent(title: str, timeout: int = 15) -> str | None:
    try:
        data = {"q": f'site:patents.google.com "{title}"'}
        url = "https://lite.duckduckgo.com/lite/"
        
        r = requests.post(url, data=data, impersonate="chrome110", timeout=timeout, proxies=get_proxies())
        
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a"):
            href = a.get("href")
            if href and "patents.google.com/patent/" in href:
                return href
    except Exception:
        pass
    return None

def fetch_google_patent_pdf_url(patent_url: str, timeout: int = 15) -> tuple[str | None, str | None]:
    try:
        r = requests.get(patent_url, impersonate="chrome110", timeout=timeout, proxies=get_proxies())
        match = re.search(r'<meta name="citation_pdf_url" content="([^"]+)">', r.text)
        if match:
            pdf_url = match.group(1)
            # Extract patent ID from URL for the filename
            id_match = re.search(r'patent/([A-Z0-9]+)', patent_url)
            patent_id = id_match.group(1) if id_match else "Patente_Desconhecida"
            return pdf_url, patent_id
    except Exception:
        pass
    return None, None

def download_patent_pdf(pdf_url: str, dest_path: Path, timeout: int = 20) -> bool:
    try:
        r = requests.get(pdf_url, impersonate="chrome110", timeout=timeout, proxies=get_proxies())
        if r.status_code == 200 and r.content.startswith(b"%PDF"):
            dest_path.write_bytes(r.content)
            return True
    except Exception:
        pass
    return False

def download_patent_by_title(title: str, base_out_dir: Path, timeout: int = 20) -> dict:
    patent_url = search_duckduckgo_for_patent(title, timeout=timeout)
    if not patent_url:
        return {"success": False, "error": "not_found_on_ddg"}
        
    pdf_url, patent_id = fetch_google_patent_pdf_url(patent_url, timeout=timeout)
    if not pdf_url:
        return {"success": False, "error": "no_pdf_url_in_google_patents"}
        
    patentes_dir = base_out_dir / "Patentes"
    patentes_dir.mkdir(exist_ok=True, parents=True)
    
    dest_path = patentes_dir / f"{patent_id}.pdf"
    
    if dest_path.exists():
        return {"success": True, "filepath": str(dest_path), "patent_id": patent_id, "cached": True}
        
    success = download_patent_pdf(pdf_url, dest_path, timeout=timeout)
    
    if success:
        return {"success": True, "filepath": str(dest_path), "patent_id": patent_id, "cached": False}
    else:
        return {"success": False, "error": "pdf_download_failed"}

