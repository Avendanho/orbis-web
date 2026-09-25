"""Extract public document links from article landing pages without executing scripts.

Covers what repository and publisher pages expose to any visitor: the
citation metadata tags, <link rel="alternate">, JSON-LD contentUrl, Dublin
Core identifiers, embedded viewers (iframe/embed/object) and the URL shapes
used by DSpace, EPrints, Pure, DigitalCommons and OJS. Nothing here defeats
a paywall or a challenge — it only reads what the page already carries.
"""
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urldefrag

# Link priority: the lower the rank, the more explicit the page was about
# this URL being the article's full text.
_RANK_CITATION_META = 0     # citation_pdf_url and friends: the publisher says so
_RANK_LINK_ALTERNATE = 1    # <link rel="alternate" type="application/pdf">
_RANK_JSONLD = 2            # schema.org contentUrl / encoding
_RANK_EMBEDDED = 3          # iframe/embed/object pointing at a PDF
_RANK_ANCHOR = 4            # a/href that looks like a PDF
_RANK_PATH_HINT = 5         # repository download paths without a .pdf suffix
_RANK_DUBLIN_CORE = 6       # dc.identifier.uri: often the landing page itself

_PDF_META_NAMES = {
    "citation_pdf_url",
    "wkhealth_pdf_url",
    "eprints.document_url",
    "bepress_citation_pdf_url",
    "dc.identifier.pdf",
}
_DUBLIN_CORE_NAMES = {"dc.identifier.uri", "dcterms.identifier", "dc.identifier"}

# Path shapes used by repository software to serve the file itself.
_DOWNLOAD_PATH_RE = re.compile(
    r"/(bitstream|bitstreams|download|downloads|fulltext|full-text|article/file|"
    r"content/pdf|viewcontent|servlets/purl|objects?/[^/]+/datastreams?)/",
    re.IGNORECASE,
)
_PDF_PATH_RE = re.compile(r"\.pdf(\?|$)|/pdf(/|\?|$)|/pdfft(\?|$)|/epdf(/|\?|$)", re.IGNORECASE)
_PDF_TYPES = {"application/pdf", "application/octet-stream", "application/x-pdf"}
_HANDLE_RE = re.compile(r"^https?://(hdl\.handle\.net|handle\.net)/", re.IGNORECASE)


def _looks_like_pdf_url(url: str) -> int | None:
    """Rank for a URL judged only by its shape, or None when it looks unrelated."""
    path_and_query = urlsplit(url).path + "?" + (urlsplit(url).query or "")
    if _PDF_PATH_RE.search(path_and_query):
        return _RANK_ANCHOR
    if _DOWNLOAD_PATH_RE.search(path_and_query):
        return _RANK_PATH_HINT
    return None


def _jsonld_urls(payload) -> list[str]:
    """contentUrl / encoding.contentUrl values anywhere in a JSON-LD block."""
    found: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key.lower() in {"contenturl", "url", "downloadurl"} and isinstance(value, str):
                    encoding_format = str(node.get("encodingFormat") or node.get("fileFormat") or "").lower()
                    if "pdf" in encoding_format or _PDF_PATH_RE.search(value):
                        found.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return found


def extract_pdf_links(html_text: str, page_url: str) -> list[str]:
    """Candidate full-text URLs on a landing page, most explicit first."""

    class Parser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.base = page_url
            self.links: list[tuple[int, str]] = []
            self._in_jsonld = False

        def _add(self, rank: int, value: str | None):
            if value and value.strip():
                self.links.append((rank, value.strip()))

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            name = (attrs.get("name") or attrs.get("property") or "").lower()
            attr_type = (attrs.get("type") or "").lower()

            if tag == "base" and attrs.get("href"):
                self.base = urljoin(page_url, attrs["href"])
                return
            if tag == "script" and attr_type == "application/ld+json":
                self._in_jsonld = True
                return
            if tag == "meta":
                if name in _PDF_META_NAMES:
                    self._add(_RANK_CITATION_META, attrs.get("content"))
                elif name in _DUBLIN_CORE_NAMES:
                    content = attrs.get("content") or ""
                    # Only worth following when it points at a file or a handle
                    # that redirects to one, not at the landing page itself.
                    if _looks_like_pdf_url(content) is not None or _HANDLE_RE.match(content):
                        self._add(_RANK_DUBLIN_CORE, content)
                return
            if tag == "link":
                rel = (attrs.get("rel") or "").lower()
                if attr_type in _PDF_TYPES and ("alternate" in rel or "item" in rel):
                    self._add(_RANK_LINK_ALTERNATE, attrs.get("href"))
                return
            if tag in {"iframe", "embed", "object"}:
                value = attrs.get("src") or attrs.get("data")
                if value and (attr_type in _PDF_TYPES or _looks_like_pdf_url(value) is not None):
                    self._add(_RANK_EMBEDDED, value)
                return

            value = attrs.get("href") or attrs.get("src") or attrs.get("data") or attrs.get("data-url")
            if not value:
                return
            if attr_type in _PDF_TYPES:
                self._add(_RANK_ANCHOR, value)
                return
            rank = _looks_like_pdf_url(value)
            if rank is not None:
                self._add(rank, value)

        def handle_data(self, data):
            if not self._in_jsonld:
                return
            try:
                payload = json.loads(data)
            except ValueError:
                return
            for url in _jsonld_urls(payload):
                self._add(_RANK_JSONLD, url)

        def handle_endtag(self, tag):
            if tag == "script":
                self._in_jsonld = False

    parser = Parser()
    parser.feed(html_text)

    result: list[str] = []
    for _rank, value in sorted(parser.links, key=lambda item: item[0]):
        url = urldefrag(urljoin(parser.base, value))[0]
        if urlsplit(url).scheme in {"http", "https"} and url not in result:
            result.append(url)
    return result[:12]
