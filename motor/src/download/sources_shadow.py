"""Bibliotecas-sombra: Sci-Hub, LibGen e Anna's Archive.

Ficam num módulo à parte pelo motivo prático de serem as fontes mais caras e
menos produtivas da cadeia, e pelo motivo de organização de serem as de
estatuto legal distinto das demais — separadas, dá para desligar as três de
uma vez (``PAPER_FETCH_NO_SCIHUB``, ``PAPER_FETCH_NO_LIBGEN``) sem tocar no
resto, que é o que uma instituição costuma exigir.

São consultadas por último e com prazo curto: os espelhos caem, trocam de
domínio e respondem devagar, e cada tentativa longa aqui é tempo que não se
gasta nas fontes que funcionam.

O acesso ao cliente HTTP e ao emissor de eventos passa por ``runtime`` — veja
lá por que não é um import direto de ``fetch``.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request

import runtime
from bypass403 import bypass_get

def try_scihub(doi: str, *, timeout: int) -> tuple[str, str] | None:
    """Resolve a DOI to a PDF URL via concurrent Sci-Hub mirror probing."""
    if runtime._deadline_exceeded():
        return None
    import concurrent.futures
    mirrors = runtime._scihub_mirrors()
    req_timeout = min(timeout, 6)

    def _try_one(host: str) -> tuple[str | None, str]:
        # Normalize encoding: unquote first (in case doi arrived pre-encoded),
        # then re-encode exactly once, preserving the '/' separator.
        doi_encoded = urllib.parse.quote(urllib.parse.unquote(doi), safe="/")
        url = f"https://{host}/{doi_encoded}"
        if not runtime._is_allowed_host(url):
            return None, "error"
        try:
            html = runtime._get(
                url,
                accept="text/html,application/xhtml+xml",
                timeout=req_timeout,
                user_agent=runtime.SCIHUB_UA,
                ssl_context=runtime._ssl_unverified_context,
            ).decode("utf-8", "replace")
        except Exception:
            return None, "error"
        pdf = runtime._scihub_extract_iframe(html, mirror_host=host)
        if pdf:
            return pdf, "pdf"
        if runtime._scihub_is_not_in_corpus(html):
            return None, "not_in_corpus"
        return None, "no_pdf"

    # Concurrent probing of top mirrors
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(mirrors), 4)) as executor:
        future_to_host = {executor.submit(runtime._inherit_budget(_try_one), host): host for host in mirrors[:6]}
        for future in concurrent.futures.as_completed(future_to_host):
            host = future_to_host[future]
            try:
                pdf, status = future.result()
                if pdf:
                    return pdf, host
                if status == "not_in_corpus":
                    runtime._progress("source_miss", source="scihub", reason="not_in_corpus", mirror=host)
                    continue
            except Exception:
                continue

    return None


def try_annas_archive(doi: str, *, timeout: int, errors: list | None = None) -> str | None:
    """Try to fetch a paper from Anna's Archive using bypass_get to defeat DDoS-Guard.

    Uses a robust MD5 pipeline:
    1. Resolve DOI to MD5 via API (without content filter) or HTML search.
    2. Fetch the /md5/ page for the first found MD5.
    3. Extract IPFS or direct Sci-Hub partner links.
    """
    if runtime._deadline_exceeded():
        return None
    mirrors = runtime._annas_archive_mirrors()
    req_timeout = min(runtime._bounded_timeout(timeout), 12)
    md5_list = []

    # --- Strategy 1: Direct /scidb/ Route (Fast Path) -------------------
    # This route often redirects directly to the /md5/{hash} detail page,
    # saving us an API/Search request.
    for mirror in mirrors:
        try:
            fast_url = f"{mirror}/scidb/{urllib.parse.quote(doi)}"
            res = bypass_get(fast_url, timeout=req_timeout, headers={"Accept": "text/html"})
            
            html_bytes = None
            final_url = fast_url
            if res.success and res.data:
                html_bytes = res.data
                final_url = res.url

            if html_bytes:
                html_text = html_bytes.decode("utf-8", "ignore")
                
                # If it redirected to an MD5 detail page, we can extract links directly!
                if "/md5/" in final_url or "/detail/" in final_url or "Fast Partner Server" in html_text or "?&check=1" in final_url:
                    # 1. IPFS Gateways (Direct PDFs)
                    ipfs_links = re.findall(
                        r'href=["\'](https?://[^"\']*(?:cloudflare-ipfs\.com|dweb\.link|gateway\.pinata\.cloud|ipfs\.io)[^"\']*)["\']', 
                        html_text, re.IGNORECASE
                    )
                    if ipfs_links:
                        return ipfs_links[0]
                        
                    # 2. Sci-Hub mirrors linked by Anna's Archive
                    sh_links = re.findall(
                        r'href=["\'](https?://[^"\']*(?:sci-hub\.se|sci-hub\.ru|sci-hub\.st)[^"\']*)["\']', 
                        html_text, re.IGNORECASE
                    )
                    if sh_links:
                        return sh_links[0]
                        
                    # 3. Libgen / library.lol scimag
                    scimag = re.findall(
                        r'href=["\'](https?://library\.lol/scimag/[^"\']*)["\']', 
                        html_text, re.IGNORECASE
                    )
                    if scimag:
                        try:
                            lol_res = bypass_get(scimag[0], timeout=req_timeout, headers={"Accept": "text/html"})
                            if lol_res.success and lol_res.data:
                                get_links = re.findall(r'href=["\'](https?://[^"\']*get\.php\?[^"\']*)["\']', lol_res.data.decode("utf-8", "ignore"), re.IGNORECASE)
                                if get_links:
                                    return get_links[0]
                        except Exception:
                            pass
                            
                # If we are here, it didn't find the links on the redirected page.
                # Let's extract the MD5 anyway if it exists on the page.
                matches = re.findall(r'(?:/md5/|/detail/)([a-f0-9]{32})', html_text, re.IGNORECASE)
                for match in matches:
                    m = match.lower()
                    if m not in md5_list:
                        md5_list.append(m)
        except Exception:
            continue
            
        if md5_list:
            break

    # --- Strategy 2: Resolve MD5 via JSON API ---------------------------
    if not md5_list:
        for mirror in mirrors:
            try:
                api_url = f"{mirror}/api/search?q={urllib.parse.quote(doi)}&ext=pdf"
                res = bypass_get(api_url, timeout=req_timeout, headers={"Accept": "application/json"})
                if res.success and res.data:
                    data = json.loads(res.data.decode("utf-8", "ignore"))
                    results = data if isinstance(data, list) else data.get("results", [])
                    for item in results[:5]:
                        md5 = (item.get("md5") or "").lower().strip()
                        if re.fullmatch(r"[a-f0-9]{32}", md5) and md5 not in md5_list:
                            md5_list.append(md5)
                if md5_list:
                    break
            except Exception as e:
                continue

    # --- Strategy 3: Resolve MD5 via HTML search ------------------------
    if not md5_list:
        for mirror in mirrors:
            try:
                url = f"{mirror}/search?q={urllib.parse.quote(doi)}&ext=pdf"
                res = bypass_get(url, timeout=req_timeout, headers={"Accept": "text/html,application/xhtml+xml"})
                if res.success and res.data:
                    html_text = res.data.decode("utf-8", "ignore")
                    matches = re.findall(r'(?:/md5/|/detail/)([a-f0-9]{32})', html_text, re.IGNORECASE)
                    for match in matches:
                        m = match.lower()
                        if m not in md5_list:
                            md5_list.append(m)
                if md5_list:
                    break
            except Exception as e:
                continue

    # --- Strategy 4: Extract direct download from MD5 page -------------
    for md5 in md5_list:
        for mirror in mirrors:
            try:
                detail_url = f"{mirror}/md5/{md5}"
                res = bypass_get(detail_url, timeout=req_timeout, headers={"Accept": "text/html"})
                if not res.success or not res.data:
                    continue
                    
                detail_html = res.data.decode("utf-8", "ignore")
                
                # 1. Look for direct IPFS Gateways (Direct PDFs)
                ipfs_links = re.findall(
                    r'href=["\'](https?://[^"\']*(?:cloudflare-ipfs\.com|dweb\.link|gateway\.pinata\.cloud|ipfs\.io)[^"\']*)["\']', 
                    detail_html, 
                    re.IGNORECASE
                )
                if ipfs_links:
                    return ipfs_links[0]
                    
                # 2. Look for direct Sci-Hub mirrors linked by Anna's Archive
                sh_links = re.findall(
                    r'href=["\'](https?://[^"\']*(?:sci-hub\.se|sci-hub\.ru|sci-hub\.st)[^"\']*)["\']', 
                    detail_html, 
                    re.IGNORECASE
                )
                if sh_links:
                    return sh_links[0]
                    
                # 3. Look for Libgen / library.lol scimag link
                scimag = re.findall(
                    r'href=["\'](https?://library\.lol/scimag/[^"\']*)["\']', 
                    detail_html, 
                    re.IGNORECASE
                )
                if scimag:
                    # library.lol scimag requires a second fetch to get the GET link
                    try:
                        lol_res = bypass_get(scimag[0], timeout=req_timeout, headers={"Accept": "text/html"})
                        if lol_res.success and lol_res.data:
                            lol_html = lol_res.data.decode("utf-8", "ignore")
                            get_links = re.findall(r'href=["\'](https?://[^"\']*get\.php\?[^"\']*)["\']', lol_html, re.IGNORECASE)
                            if get_links:
                                return get_links[0]
                    except Exception:
                        pass
                        
            except Exception:
                continue

    return None


def try_libgen(
    doi: str,
    title: str | None = None,
    *,
    timeout: int,
    errors: list | None = None,
) -> tuple[list[str], dict]:
    """Resolve a DOI or title to direct PDF download URLs and metadata via Libgen."""
    if not runtime._is_libgen_enabled() or runtime._deadline_exceeded():
        return [], {}

    pdf_urls: list[str] = []
    meta: dict = {}

    headers = {
        "User-Agent": runtime.LIBGEN_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    req_timeout = min(timeout, 5)

    # 1. Fast direct HTTP mirror resolution (instant and resilient)
    search_urls = []
    mirrors = runtime._libgen_mirrors()
    for mirror in mirrors:
        if doi:
            search_urls.append(f"{mirror}/index.php?req={urllib.parse.quote(doi)}&columns%5B%5D=d&res=25")
            search_urls.append(f"{mirror}/index.php?req={urllib.parse.quote(doi)}&res=25")
        if title and len(title) >= 6:
            search_urls.append(f"{mirror}/index.php?req={urllib.parse.quote(title)}&columns%5B%5D=t&res=25")

    for search_url in search_urls:
        if pdf_urls or runtime._deadline_exceeded():
            break
        try:
            req = urllib.request.Request(search_url, headers=headers)
            with urllib.request.urlopen(req, timeout=req_timeout, context=runtime._ssl_unverified_context) as resp:
                html_text = resp.read().decode("utf-8", "replace")

            if "edition.php?id=" not in html_text and "/ads.php?md5=" not in html_text:
                continue

            md5_list: list[str] = []
            for m in re.findall(r'/ads\.php\?md5=([a-fA-F0-9]{32})', html_text):
                if m not in md5_list:
                    md5_list.append(m)

            edition_matches = re.findall(r'href=["\'](edition\.php\?id=\d+)["\']', html_text)
            for ed in edition_matches[:2]:
                ed_url = f"{mirror}/{ed}"
                try:
                    req_ed = urllib.request.Request(ed_url, headers=headers)
                    with urllib.request.urlopen(req_ed, timeout=req_timeout) as resp_ed:
                        ed_html = resp_ed.read().decode("utf-8", "replace")
                    for m in re.findall(r'/ads\.php\?md5=([a-fA-F0-9]{32})', ed_html):
                        if m not in md5_list:
                            md5_list.append(m)
                except Exception:
                    continue

            for md5 in md5_list[:2]:
                ads_url = f"{mirror}/ads.php?md5={md5}"
                try:
                    req_ads = urllib.request.Request(ads_url, headers=headers)
                    with urllib.request.urlopen(req_ads, timeout=req_timeout) as resp_ads:
                        ads_html = resp_ads.read().decode("utf-8", "replace")

                    get_match = re.search(r'href=["\'](get\.php\?md5=[a-fA-F0-9]+&key=[a-zA-Z0-9]+)["\']', ads_html)
                    if get_match:
                        dl_url = f"{mirror}/{get_match.group(1)}"
                        if dl_url not in pdf_urls:
                            pdf_urls.append(dl_url)
                except Exception:
                    continue

        except Exception as exc:
            if errors is not None and runtime._is_transport_exc(exc):
                errors.append({"source": "libgen", "detail": str(exc)})
            continue

    if pdf_urls:
        return pdf_urls, meta

    # 2. Fallback: Try using libgen-api-enhanced / libgen-api if installed
    if runtime._deadline_exceeded():
        return pdf_urls, meta
    try:
        from io import StringIO
        import contextlib
        import logging
        import socket
        logging.getLogger("libgen_api_enhanced").setLevel(logging.CRITICAL)
        logging.getLogger("libgen_api").setLevel(logging.CRITICAL)
        from libgen_api_enhanced import LibgenSearch
        old_sock_t = socket.getdefaulttimeout()
        try:
            _left = runtime._time_left()
            socket.setdefaulttimeout(min(timeout, 3) if _left is None else max(1, min(3, int(_left))))
            for mirror_code in ("li",):
                if runtime._deadline_exceeded():
                    break
                try:
                    s = LibgenSearch(mirror=mirror_code)
                    # Suppress third-party print noise
                    with contextlib.redirect_stdout(StringIO()), contextlib.redirect_stderr(StringIO()):
                        results = s.search_default(f"doi:{doi}")
                        if not results and title and len(title) >= 6:
                            results = s.search_title(title)
                    if results:
                        for book in results[:3]:
                            if runtime._deadline_exceeded():
                                break
                            if not meta.get("title") and getattr(book, "title", None):
                                meta["title"] = book.title
                            if not meta.get("author") and getattr(book, "author", None):
                                meta["author"] = book.author
                            if not meta.get("year") and getattr(book, "year", None):
                                try:
                                    meta["year"] = int(book.year)
                                except (ValueError, TypeError):
                                    pass
                            try:
                                with contextlib.redirect_stdout(StringIO()), contextlib.redirect_stderr(StringIO()):
                                    book.resolve_direct_download_link()
                                if getattr(book, "resolved_download_link", None):
                                    link = book.resolved_download_link
                                    if link not in pdf_urls:
                                        pdf_urls.append(link)
                            except Exception:
                                pass
                            for m in getattr(book, "mirrors", []) or []:
                                if m and m not in pdf_urls and ("get.php" in m or ".pdf" in m or "ads.php" in m):
                                    pdf_urls.append(m)
                        if pdf_urls:
                            return pdf_urls, meta
                except Exception:
                    continue
        finally:
            socket.setdefaulttimeout(old_sock_t)
    except ImportError:
        pass

    return pdf_urls, meta
