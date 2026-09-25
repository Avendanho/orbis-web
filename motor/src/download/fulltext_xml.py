"""Full-text XML from publisher APIs, rendered as a readable PDF.

Elsevier and Springer Nature serve full text as XML to API keys that are not
entitled to (or not provisioned for) the PDF. The XML carries the whole
article text — no figures, no page layout — which is exactly what the triage
step needs. It is rendered into a plain PDF so the rest of the pipeline
(identity validation, text extraction, LLM screening) works unchanged.
"""
from __future__ import annotations

import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import institutional

ELSEVIER_ARTICLE_URL = "https://api.elsevier.com/content/article/doi/{doi}"
SPRINGER_JATS_URL = "https://api.springernature.com/openaccess/jats"
USER_AGENT = "revisao-sistematica/1.0 (mailto:{})".format(os.environ.get("UNPAYWALL_EMAIL", "") or "anonymous")

# Namespaces used by the two formats.
_CE = "http://www.elsevier.com/xml/common/dtd"
_DC = "http://purl.org/dc/elements/1.1/"

_MIN_BODY_CHARS = 500  # below this it is an abstract-only record, not full text


def _http_get(url: str, *, headers: dict, timeout: int) -> tuple[int, bytes, dict]:
    session = institutional.get_session(timeout) if institutional.is_configured() else None
    if session is not None:
        resp = session.get(url, headers=headers, timeout=timeout)
        return resp.status_code, resp.content or b"", dict(resp.headers or {})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read() if e.fp else b"", dict(e.headers or {})


def try_elsevier_xml(doi: str, *, timeout: int = 25) -> tuple[bytes | None, str | None]:
    """Elsevier full-text XML for a DOI. Returns (xml, error)."""
    api_key = os.environ.get("ELSEVIER_API_KEY", "").strip()
    if not api_key:
        return None, "no_api_key"
    headers = {"X-ELS-APIKey": api_key, "Accept": "text/xml"}
    inst_token = os.environ.get("ELSEVIER_INST_TOKEN", "").strip()
    if inst_token:
        headers["X-ELS-Insttoken"] = inst_token
    url = ELSEVIER_ARTICLE_URL.format(doi=urllib.parse.quote(doi))
    try:
        status, body, resp_headers = _http_get(url, headers=headers, timeout=timeout)
    except Exception as exc:
        return None, f"transport_error:{exc}"
    if status != 200:
        # X-ELS-Status explains 403s: APIKEY_INVALID (bad key),
        # AUTHORIZATION_ERROR (no subscription for this article),
        # AUTHENTICATION_ERROR (key not provisioned for Article Retrieval).
        detail = (resp_headers.get("X-ELS-Status") or "").strip() or f"http_{status}"
        return None, detail
    if b"<coredata>" in body and b"<ce:para" not in body and b"<xocs:rawtext" not in body:
        return None, "abstract_only"
    return body, None


def try_springer_jats(doi: str, *, timeout: int = 25) -> tuple[bytes | None, str | None]:
    """Springer Nature open-access JATS full text for a DOI."""
    api_key = os.environ.get("SPRINGER_OA_API_KEY", "").strip() or os.environ.get("SPRINGER_API_KEY", "").strip()
    if not api_key:
        return None, "no_api_key"
    url = SPRINGER_JATS_URL + "?" + urllib.parse.urlencode({"q": f"doi:{doi}", "api_key": api_key})
    try:
        status, body, _headers = _http_get(url, headers={"Accept": "application/xml"}, timeout=timeout)
    except Exception as exc:
        return None, f"transport_error:{exc}"
    if status != 200:
        return None, f"http_{status}"
    if b"<article" not in body:
        return None, "no_article_in_response"
    return body, None


def _text_of(element) -> str:
    return re.sub(r"\s+", " ", "".join(element.itertext())).strip()


def parse_fulltext(xml_bytes: bytes) -> dict | None:
    """{title, authors, abstract, paragraphs} from Elsevier or JATS XML."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    paragraphs = [_text_of(p) for p in root.iter(f"{{{_CE}}}para")]
    title = next((t.text for t in root.iter(f"{{{_DC}}}title") if t.text), None)
    authors = [a.text for a in root.iter(f"{{{_DC}}}creator") if a.text]
    abstract = next((d.text for d in root.iter(f"{{{_DC}}}description") if d.text), "")

    if not paragraphs:  # JATS (Springer, PMC)
        body = next((el for el in root.iter() if el.tag.endswith("body")), None)
        if body is not None:
            paragraphs = [_text_of(p) for p in body.iter() if p.tag.endswith("}p") or p.tag == "p"]
        title_el = next((el for el in root.iter() if el.tag.endswith("article-title")), None)
        title = title or (_text_of(title_el) if title_el is not None else None)
        authors = authors or [
            _text_of(name) for name in root.iter() if name.tag.endswith("string-name")
        ][:10]
        abstract_el = next((el for el in root.iter() if el.tag.endswith("abstract")), None)
        abstract = abstract or (_text_of(abstract_el) if abstract_el is not None else "")

    paragraphs = [p for p in paragraphs if p]
    if sum(len(p) for p in paragraphs) < _MIN_BODY_CHARS:
        return None
    return {
        "title": (title or "").strip() or None,
        "authors": [a for a in authors if a],
        "abstract": (abstract or "").strip(),
        "paragraphs": paragraphs,
    }


def render_pdf(article: dict, doi: str, dest: Path) -> bool:
    """Write the parsed full text as a plain PDF. True on success.

    The DOI goes on the first page so the identity gate can confirm the file
    the same way it confirms a publisher PDF, and a note records that this is
    an XML rendering rather than the version of record.
    """
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore
        except ImportError:
            return False

    blocks = [
        (article.get("title") or doi, 15),
        (", ".join(article.get("authors") or []), 10),
        (f"DOI: {doi}", 10),
        ("Texto completo obtido em XML pela API da editora (sem figuras nem diagramação).", 8),
    ]
    if article.get("abstract"):
        blocks += [("Abstract", 12), (article["abstract"], 10)]
    blocks.append(("Full text", 12))
    blocks += [(p, 10) for p in article["paragraphs"]]

    try:
        font = pymupdf.Font("helv")
        doc = pymupdf.open()
        page = doc.new_page()
        margin = 56
        width, height = page.rect.width, page.rect.height
        max_width = width - 2 * margin
        y = margin
        for text, size in blocks:
            if not text:
                continue
            line_height = size * 1.35
            for line in _wrap(text, font, size, max_width):
                if y + line_height > height - margin:
                    page = doc.new_page()
                    y = margin
                page.insert_text((margin, y + size), line, fontsize=size, fontname="helv")
                y += line_height
            y += size * 0.5
        doc.set_metadata({
            "title": article.get("title") or doi,
            "subject": f"doi:{doi}",
            "author": ", ".join(article.get("authors") or [])[:200],
            "keywords": f"doi:{doi} full-text-xml",
        })
        dest.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(dest))
        doc.close()
    except Exception:
        return False
    return dest.exists() and dest.stat().st_size > 0


def _wrap(text: str, font, size: float, max_width: float) -> list[str]:
    """Break a paragraph into lines that fit the page width."""
    lines: list[str] = []
    line = ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if line and font.text_length(candidate, size) > max_width:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines or [""]


def fetch_as_pdf(doi: str, dest: Path, *, timeout: int = 25) -> tuple[bool, str | None]:
    """Full text from the publisher APIs, rendered to ``dest``. (ok, error)."""
    errors = []
    for name, getter in (("elsevier_xml", try_elsevier_xml), ("springer_jats", try_springer_jats)):
        if name == "elsevier_xml" and not doi.startswith("10.1016/"):
            continue
        if name == "springer_jats" and not doi.startswith(("10.1007/", "10.1038/", "10.1186/", "10.1140/", "10.1057/")):
            continue
        xml_bytes, error = getter(doi, timeout=timeout)
        if not xml_bytes:
            errors.append(f"{name}:{error}")
            continue
        article = parse_fulltext(xml_bytes)
        if not article:
            errors.append(f"{name}:no_full_text_body")
            continue
        if render_pdf(article, doi, dest):
            return True, None
        errors.append(f"{name}:render_failed")
    return False, "; ".join(errors) or "no_applicable_api"
