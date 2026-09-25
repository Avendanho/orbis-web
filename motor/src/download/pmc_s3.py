"""PMC Open Access articles straight from the NIH bucket on AWS.

Why this route exists
---------------------
Two things changed on the PMC side and broke every per-article HTTP route
into it:

* ``pmc.ncbi.nlm.nih.gov/articles/PMC.../pdf/...`` now answers a reCAPTCHA
  interstitial ("Checking your browser") with HTTP 200 and ~20 KB of HTML,
  so a plain client gets a page, never a file;
* the ``oa.fcgi`` Open Access web service was retired in the August 2026
  migration to the PMC Cloud Service and answers 404.

The bucket NIH publishes on AWS has neither problem: no key, no session, no
rate limit and no challenge. It is the same corpus, served as static objects.

Layout
------
One prefix per article *version*::

    PMC10350077.1/PMC10350077.1.pdf   <- the article
    PMC10350077.1/PMC10350077.1.xml   <- JATS full text
    PMC10350077.1/PMC10350077.1.txt
    PMC10350077.1/media-1.docx        <- supplements, not the article

Two traps this module exists to avoid: an article can have several versions
(``.1``, ``.2``) and only the newest should be used, and supplementary files
are often PDFs too (``..._MOESM1_ESM.pdf``, ``...-s001.pdf``). Only the file
named exactly ``PMC<id>.<version>.<ext>`` is the article, so that is the only
name matched here.

Not every article is present: the bucket carries the Open Access Subset, so
author manuscripts deposited under the NIH policy (NIHMS) are usually absent
even though PMC hosts them. A miss here is normal and cheap — the caller
simply continues down its source chain.
"""
from __future__ import annotations

import re
import urllib.parse
import urllib.request

BUCKET_URL = "https://pmc-oa-opendata.s3.amazonaws.com/"
USER_AGENT = "revisao-sistematica/1.0 (+PMC open access bucket)"

# Objects are listed, at most, this many pages deep. One article never needs
# more than one page; the loop only exists so a pathological prefix cannot
# spin forever.
_MAX_PAGES = 5

_KEY_RE = re.compile(r"<Key>([^<]+)</Key>")
_TRUNCATED_RE = re.compile(r"<IsTruncated>\s*true\s*</IsTruncated>", re.IGNORECASE)
_TOKEN_RE = re.compile(r"<NextContinuationToken>([^<]+)</NextContinuationToken>")


def normalize_pmcid(pmcid: str) -> str:
    """``10350077``/``pmc10350077``/``PMC10350077`` -> ``PMC10350077``."""
    value = str(pmcid or "").strip().upper()
    if value.startswith("PMC"):
        value = value[3:]
    value = value.strip()
    return f"PMC{value}" if value else ""


def _get(url: str, *, timeout: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def _list_keys(pmcid: str, *, timeout: int) -> list[str]:
    """Every object key under this article's prefix.

    The prefix carries a trailing dot (``PMC1000.``) because without it S3
    would also return ``PMC10000338/...`` for the id ``PMC1000``.
    """
    keys: list[str] = []
    token: str | None = None
    for _ in range(_MAX_PAGES):
        params = {"list-type": "2", "prefix": f"{pmcid}."}
        if token:
            params["continuation-token"] = token
        body = _get(BUCKET_URL + "?" + urllib.parse.urlencode(params), timeout=timeout)
        text = body.decode("utf-8", "replace")
        keys.extend(_KEY_RE.findall(text))
        if not _TRUNCATED_RE.search(text):
            break
        match = _TOKEN_RE.search(text)
        if not match:
            break
        token = match.group(1)
    return keys


def list_assets(pmcid: str, *, timeout: int = 20) -> dict:
    """Locate the newest version's article files.

    Returns ``{"pmcid", "version", "pdf", "xml", "txt"}`` with URLs, or Nones
    when the article is not in the bucket. Never raises: a transport failure
    is reported as a miss so the caller's source chain simply moves on.
    """
    pmcid = normalize_pmcid(pmcid)
    empty = {"pmcid": pmcid, "version": None, "pdf": None, "xml": None, "txt": None}
    if not pmcid:
        return empty

    try:
        keys = _list_keys(pmcid, timeout=timeout)
    except Exception:
        return empty

    # Only `PMC<id>.<version>/PMC<id>.<version>.<ext>` is the article itself;
    # anything else under the prefix is a figure or a supplement.
    article = re.compile(
        rf"^{re.escape(pmcid)}\.(\d+)/{re.escape(pmcid)}\.\1\.(pdf|xml|txt)$"
    )
    by_version: dict[int, dict[str, str]] = {}
    for key in keys:
        match = article.match(key)
        if not match:
            continue
        version, extension = int(match.group(1)), match.group(2)
        by_version.setdefault(version, {})[extension] = BUCKET_URL + urllib.parse.quote(key)

    if not by_version:
        return empty

    version = max(by_version)
    files = by_version[version]
    return {
        "pmcid": pmcid,
        "version": version,
        "pdf": files.get("pdf"),
        "xml": files.get("xml"),
        "txt": files.get("txt"),
    }


def fetch_xml(url: str, *, timeout: int = 30) -> bytes | None:
    """The JATS XML behind a URL from :func:`list_assets`. None on failure."""
    if not url or not url.startswith(BUCKET_URL):
        return None
    try:
        return _get(url, timeout=timeout)
    except Exception:
        return None
