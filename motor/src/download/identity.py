#!/usr/bin/env python3
"""Bibliographic identity resolution and validation.

This module is the single source of truth for answering one question that
the download pipeline in ``fetch.py`` previously never asked: *does this
candidate record / downloaded PDF actually correspond to the article we
requested?*

It is intentionally free of any dependency on ``fetch.py`` (fetch.py imports
this module, not the other way around) so it can be unit-tested in isolation
and reused by the cache/index layer in ``run_parallel.py``.

Nothing here performs network I/O. Callers pass in already-fetched JSON
records / already-downloaded PDF bytes.
"""
from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from difflib import SequenceMatcher

# ---------------------------------------------------------------------------
# DOI normalization
# ---------------------------------------------------------------------------

_DOI_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi.org/",
    "dx.doi.org/",
)

# A DOI "looks like" 10.<registrant>/<suffix>. Used both to validate free-form
# input and to recognize DOI-shaped strings embedded in arbitrary JSON/text.
# O `(?!doi:)` existe por causa da marca d'água do bioRxiv/medRxiv, que
# carimba cada página com "https://doi.org/10.1101/2023.07.08.548192doi:
# bioRxiv preprint" — sem espaço entre o identificador e o "doi:" seguinte.
# Sem a exceção, o padrão guloso levava o "doi" junto e a identidade falhava
# para todo preprint desses servidores.
DOI_LIKE_RE = re.compile(r"10\.\d{4,9}/(?:(?!doi:)[^\s\"'<>\)\]])+", re.IGNORECASE)


def normalize_doi(doi: str | None) -> str:
    """Normalize a DOI to its canonical bare form: ``10.xxxx/yyyy`` lowercase.

    Strips protocol/host prefixes, a leading ``doi:`` marker, surrounding
    whitespace, trailing punctuation that commonly leaks in from prose or
    reference lists (periods, commas, closing brackets), URL fragment
    identifiers (``#section``, ``#ch3``), and chapter suffixes of the form
    ``.ch<N>`` that some publishers (Wiley, Springer) append to book-chapter
    DOIs — e.g. ``10.1002/9780470xxx.ch3`` → ``10.1002/9780470xxx``.

    Stripping ``.ch<N>`` makes the identifier resolvable as the parent book,
    which is far more likely to be openly accessible than the chapter itself.
    The stripped form is recorded in the result so callers can note the
    transformation in logs.
    """
    if not doi:
        return ""
    s = str(doi).strip()
    for prefix in _DOI_PREFIXES:
        if s.lower().startswith(prefix):
            s = s[len(prefix):]
            break
    if s.lower().startswith("doi:"):
        s = s[4:].strip()
    # Strip URL fragment identifiers — e.g. "10.1002/xyz#ch3" or "...#sec2"
    if "#" in s:
        s = s.split("#")[0]
    s = s.strip().strip(".,;:)]}>\\\"'")
    # Strip trailing chapter suffixes: .ch1, .ch12, etc.
    s = re.sub(r"\.ch\d+$", "", s, flags=re.IGNORECASE)
    return s.lower()


def dois_equal(a: str | None, b: str | None) -> bool:
    na, nb = normalize_doi(a), normalize_doi(b)
    return bool(na) and bool(nb) and na == nb


# ---------------------------------------------------------------------------
# Extracting a DOI out of an arbitrary API record (Level 1 identity evidence)
# ---------------------------------------------------------------------------

# Keys that, when present with a string value, are treated as carrying a DOI.
# Covers the shapes actually returned by Zenodo, OpenAIRE, HAL, DataCite,
# DOAJ, CORE and Crossref.
_DOI_KEY_HINTS = ("doi",)


def extract_record_doi(record) -> str | None:
    """Best-effort recursive search for a DOI belonging to a single API record.

    Looks at any dict key containing "doi" (case-insensitive) whose value is
    a string or a list of strings, plus generic identifier lists such as
    ``identifiers: [{"identifierType": "DOI", "identifier": "10.x/y"}]`` or
    ``pids: {"doi": {"identifier": "10.x/y"}}``. Returns the first DOI-shaped
    value found, normalized, or ``None``.

    This function must never mistake *some other article's* DOI mentioned in
    free text for the record's own DOI, so it only looks at structured
    fields, never at arbitrary prose/abstract text.
    """
    found: list[str] = []

    def consider(value) -> None:
        if isinstance(value, str):
            v = value.strip()
            if v:
                found.append(v)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    consider(item)
                elif isinstance(item, dict):
                    # e.g. identifiers: [{"doi": "..."}] or
                    # [{"identifierType": "DOI", "identifier": "10.x/y"}]
                    for k, v in item.items():
                        if isinstance(v, str) and (
                            "doi" in k.lower()
                            or (
                                str(item.get("identifierType", "")).lower() == "doi"
                                and k.lower() in ("identifier", "value")
                            )
                        ):
                            consider(v)

    def walk(node, depth: int = 0) -> None:
        if depth > 6 or len(found) >= 5:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                lk = key.lower()
                if any(hint in lk for hint in _DOI_KEY_HINTS) and lk not in ("doi_resolver",):
                    consider(value)
                elif isinstance(value, list) and lk in ("identifiers", "pids", "alternate_identifiers", "alternateidentifiers"):
                    # Generic identifier-list shapes tag the DOI via an inner
                    # key/type rather than the outer key name itself, e.g.
                    # [{"identifierType": "DOI", "identifier": "10.x/y"}].
                    consider(value)
                if isinstance(value, (dict, list)):
                    walk(value, depth + 1)
        elif isinstance(node, list):
            for item in node:
                walk(item, depth + 1)

    walk(record)

    for candidate in found:
        m = DOI_LIKE_RE.search(candidate)
        if m:
            return normalize_doi(m.group(0))
    return None


def records_matching_doi(records: list, target_doi: str, *, doi_getter=None) -> list:
    """Filter ``records`` to only those whose own DOI equals ``target_doi``.

    This is the fix for the "mixed search results" bug: an API that returns
    several hits for a text query must never let hit #2's PDF be associated
    with hit #0's (or the query's) DOI. Only records that *prove* their own
    identity via an exact, normalized DOI match survive.

    ``doi_getter`` may be supplied when a record's DOI lives in a
    source-specific shape that :func:`extract_record_doi` would not reliably
    find; it is tried first and falls back to the generic extractor.
    """
    target = normalize_doi(target_doi)
    if not target:
        return []
    out = []
    for rec in records or []:
        rec_doi = None
        if doi_getter is not None:
            try:
                rec_doi = doi_getter(rec)
            except Exception:
                rec_doi = None
        if not rec_doi:
            rec_doi = extract_record_doi(rec)
        if rec_doi and normalize_doi(rec_doi) == target:
            out.append(rec)
    return out


# ---------------------------------------------------------------------------
# Title / author bibliographic comparison (Level 2 identity evidence)
# ---------------------------------------------------------------------------


def normalize_title_text(value: str | None) -> str:
    """Fold a title to a comparable canonical form.

    Handles Unicode ligatures/diacritics (NFKC + casefold), punctuation,
    hyphenation and whitespace noise so that formatting differences between
    indexes never masquerade as a real title mismatch.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = text.replace("&", " and ")
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def title_similarity(a: str | None, b: str | None) -> float:
    """Robust bibliographic-title similarity in [0, 1].

    Combines requested-title token coverage, Jaccard overlap and character
    sequence similarity so common index noise (subtitles, punctuation,
    reordered fragments) doesn't register as a mismatch, while unrelated
    titles that merely share a topic still score low.
    """
    na, nb = normalize_title_text(a), normalize_title_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    sa, sb = set(na.split()), set(nb.split())
    if not sa or not sb:
        return 0.0

    seq = SequenceMatcher(None, na, nb).ratio()
    jac = len(sa & sb) / len(sa | sb)
    coverage = len(sa & sb) / len(sa)
    sorted_seq = SequenceMatcher(None, " ".join(sorted(sa)), " ".join(sorted(sb))).ratio()

    score = 0.34 * coverage + 0.26 * jac + 0.25 * seq + 0.15 * sorted_seq
    return round(min(1.0, score), 6)


def _last_name(author: str | None) -> str:
    if not author:
        return ""
    text = normalize_title_text(author)
    if not text:
        return ""
    parts = text.split()
    return parts[-1] if parts else ""


def author_matches(expected: str | None, found: str | None) -> bool:
    """True when the (normalized) last name / surname token matches.

    Deliberately coarse: this is meant to corroborate a strong title match,
    not to carry identity on its own (see TITLE_SCORE_STRONG below).
    """
    ea, fa = _last_name(expected), _last_name(found)
    return bool(ea) and bool(fa) and ea == fa


def year_matches(expected, found, *, tolerance: int = 1) -> bool:
    try:
        ey = int(str(expected)[:4]) if expected else None
        fy = int(str(found)[:4]) if found else None
    except (TypeError, ValueError):
        return False
    if ey is None or fy is None:
        return False
    return abs(ey - fy) <= tolerance


def journal_matches(expected: str | None, found: str | None) -> bool:
    if not expected or not found:
        return False
    return title_similarity(expected, found) >= 0.6


def filter_hits_by_title(
    hits: list,
    target_title: str,
    *,
    title_getter,
    threshold: float = 0.72,
    top_n: int = 1,
) -> list:
    """Keep only the hit(s) whose own title genuinely matches ``target_title``.

    This is the title-mode counterpart of :func:`records_matching_doi`: a
    multi-hit search API (Zenodo, HAL, OpenAIRE, DataCite, DOAJ, ...) must
    not let an unrelated hit's files leak into the candidate pool just
    because it appeared in the same result page. Returns hits sorted by
    score (best first), truncated to ``top_n``; empty when nothing clears
    ``threshold``.
    """
    if not target_title:
        return []
    scored = []
    for hit in hits or []:
        try:
            hit_title = title_getter(hit)
        except Exception:
            hit_title = None
        score = title_similarity(target_title, hit_title)
        if score >= threshold:
            scored.append((score, hit))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [hit for _, hit in scored[:top_n]]


# ---------------------------------------------------------------------------
# PDF content extraction (cheap: metadata + first pages only)
# ---------------------------------------------------------------------------


def extract_pdf_identity(pdf_bytes: bytes, *, max_pages: int = 2) -> dict:
    """Best-effort extraction of {doi, title, author, year, text_sample}.

    Reads only document metadata plus the first ``max_pages`` pages of text
    (DOI/title/author virtually always appear on the title page), so this
    stays cheap even for large PDFs — no full-document parse is needed to
    confirm identity.

    Never raises: any parsing failure (encrypted, corrupted, scanned/no
    text layer) yields a dict with whatever could be recovered, possibly
    all-empty, so callers can fall back to a "no evidence" verdict rather
    than crashing the pipeline.
    """
    result = {"doi": None, "title": None, "author": None, "year": None, "text_sample": ""}
    if not pdf_bytes:
        return result
    try:
        import pypdf
    except ImportError:
        return result

    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    except Exception:
        return result

    try:
        info = reader.metadata or {}
        if info:
            result["title"] = (getattr(info, "title", None) or info.get("/Title") if hasattr(info, "get") else None) or result["title"]
            author = None
            if hasattr(info, "author"):
                author = info.author
            elif hasattr(info, "get"):
                author = info.get("/Author")
            result["author"] = author or result["author"]
    except Exception:
        pass

    text_parts: list[str] = []
    try:
        pages = reader.pages
        for page in pages[: max(1, max_pages)]:
            try:
                text_parts.append(page.extract_text() or "")
            except Exception:
                continue
    except Exception:
        pass
    text_sample = "\n".join(text_parts)
    result["text_sample"] = text_sample[:8000]

    # Explicit document DOI metadata outranks a DOI on a repository cover or
    # in an opening citation. Merely finding the requested DOI somewhere in
    # the body is deliberately NOT enough to accept an unrelated article.
    metadata_doi = None
    try:
        for key, value in (reader.metadata or {}).items():
            if str(key).lower().lstrip("/") in {"doi", "prism:doi", "dc:identifier", "identifier"} and isinstance(value, str):
                match = DOI_LIKE_RE.search(value)
                if match:
                    metadata_doi = normalize_doi(match.group(0))
                    break
    except Exception:
        pass
    doi_match = DOI_LIKE_RE.search(text_sample)
    if metadata_doi:
        result["doi"] = metadata_doi
    elif doi_match:
        result["doi"] = normalize_doi(doi_match.group(0))
    else:
        # DOIs embedded in XMP/document-info metadata (common for publisher
        # PDFs where the title page renders the DOI as an image/QR code).
        try:
            for value in (reader.metadata or {}).values():
                if isinstance(value, str):
                    m = DOI_LIKE_RE.search(value)
                    if m:
                        result["doi"] = normalize_doi(m.group(0))
                        break
        except Exception:
            pass

    m = re.search(r"\b(19|20)\d{2}\b", text_sample[:2000])
    if m:
        result["year"] = int(m.group(0))

    return result


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# The identity gate: everything above feeds into this one decision
# ---------------------------------------------------------------------------

# A title match at/above this level, corroborated by author+year OR
# author+journal, is treated as strong enough bibliographic identity in the
# absence of any DOI evidence (Level 2 in the design doc). A bare title
# match with no corroboration is never sufficient on its own.
# Bumped whenever the gate gets stricter, so identity records written by an
# older, more permissive validator are re-checked instead of trusted.
VALIDATOR_VERSION = 2

TITLE_SCORE_STRONG = 0.82

# Below this, a title extracted from the PDF is treated as contradicting the
# expected one (rather than merely differing in formatting or subtitle).
TITLE_SCORE_CONTRADICTS = 0.35
# Shorter strings are too often a filename, a running head or a journal name
# to be read as the document's title.
_MIN_DETECTED_TITLE_LEN = 12


# Titles PDF producers write into the metadata that say nothing about the
# document: a source filename, a template name, an export default.
_ARTIFACT_TITLE_RE = re.compile(
    r"^(microsoft (word|powerpoint)\b.*|powerpoint presentation|untitled.*|document\d*|print|layout|"
    r"\S+\.(docx?|mdi|qxd|indd|fm|tex|pdf|pptx?)|[^\s]+)$",
    re.IGNORECASE,
)

# A PDF whose opening text announces itself as the article's supplement is
# not the article, however well the record matches.
_SUPPLEMENT_OPENING_RE = re.compile(
    r"(electronic )?supplement(ary|al)?\s+(material|information|data|file)|supporting information",
    re.IGNORECASE,
)
_SUPPLEMENT_HEAD_CHARS = 300


def is_supplementary_material(pdf_identity: dict) -> bool:
    """True when the PDF opens by declaring itself supplementary material."""
    head = re.sub(r"\s+", " ", (pdf_identity.get("text_sample") or "")[:_SUPPLEMENT_HEAD_CHARS]).strip()
    title = re.sub(r"^microsoft word\s*-\s*", "", (pdf_identity.get("title") or "").strip(), flags=re.IGNORECASE)
    return bool(_SUPPLEMENT_OPENING_RE.search(head) or _SUPPLEMENT_OPENING_RE.match(title))


def _pdf_title_contradicts(expected: dict, pdf_identity: dict) -> bool:
    """True when the PDF is plainly a different work from the one expected.

    Aggregators and repositories do serve the wrong file under a correct DOI
    record — a whole book or proceedings volume in place of one chapter, a
    slide deck, an unrelated report. The check asks for positive evidence of
    a mismatch and stays silent when the file simply says little about
    itself, so record-level trust survives missing or garbled metadata:

    * an expected title to compare against, and no corroborating
      author / journal / year;
    * the expected title absent from the PDF's opening text (scanned
      articles often carry a junk metadata title but the real one on page 1);
    * and either a stated title that contradicts it, or enough opening text
      to show the document is about something else.
    """
    expected_title = (expected.get("title") or "").strip()
    if len(expected_title) < _MIN_DETECTED_TITLE_LEN:
        return False
    if (
        author_matches(expected.get("author"), pdf_identity.get("author"))
        or journal_matches(expected.get("journal"), pdf_identity.get("journal"))
        or year_matches(expected.get("year"), pdf_identity.get("year"))
    ):
        return False

    text_sample = pdf_identity.get("text_sample") or ""
    if _title_covered_by_text(expected_title, text_sample):
        return False

    detected_title = (pdf_identity.get("title") or "").strip()
    stated_title = (
        detected_title
        if len(detected_title) >= _MIN_DETECTED_TITLE_LEN and not _ARTIFACT_TITLE_RE.match(detected_title)
        else ""
    )
    if stated_title and title_similarity(expected_title, stated_title) >= TITLE_SCORE_CONTRADICTS:
        return False
    return bool(stated_title) or len(text_sample.strip()) >= _MIN_TEXT_FOR_MISMATCH


# Words of the expected title that must appear in the PDF's opening text for
# the file to be read as that article (tolerates wrapping and hyphenation).
_TITLE_COVERAGE_MIN = 0.75
# Opening text shorter than this says too little to conclude anything; the
# file may be a scan with no text layer.
_MIN_TEXT_FOR_MISMATCH = 200


def _title_covered_by_text(title: str, text_sample: str) -> bool:
    words = set(normalize_title_text(title).split())
    if not words:
        return False
    text_words = set(normalize_title_text(text_sample).split())
    return len(words & text_words) / len(words) >= _TITLE_COVERAGE_MIN


def validate_article_identity(
    expected: dict,
    *,
    pdf_identity: dict | None = None,
    record_doi_matched: bool = False,
) -> dict:
    """The single gate deciding whether a downloaded/candidate PDF is THE article.

    ``expected`` carries what we already know we're looking for:
    ``{"doi", "title", "author", "journal", "year"}`` (any may be ``None``).

    ``pdf_identity`` is the output of :func:`extract_pdf_identity` for the
    PDF actually downloaded, when available.

    ``record_doi_matched`` is True when the caller has already proven, via
    :func:`records_matching_doi` / :func:`dois_equal` / a DOI-exact source
    query, that the *metadata record* this candidate came from carries the
    requested DOI (Level 1 evidence at the record level, independent of
    whatever is or isn't extractable from the PDF bytes).

    Returns a dict with:
      identity_validated: bool
      validation_method: str
      validation_score: float
      reason: str | None            (set when rejected)
      detected_doi / detected_title / detected_author / detected_year
    """
    expected = expected or {}
    pdf_identity = pdf_identity or {}
    expected_doi = normalize_doi(expected.get("doi"))
    pdf_doi = normalize_doi(pdf_identity.get("doi"))

    base = {
        "detected_doi": pdf_identity.get("doi"),
        "detected_title": pdf_identity.get("title"),
        "detected_author": pdf_identity.get("author"),
        "detected_year": pdf_identity.get("year"),
    }

    # --- Case A: DOI found inside the PDF itself -> strongest possible signal.
    if pdf_doi:
        if expected_doi and pdf_doi == expected_doi:
            return {
                "identity_validated": True,
                "validation_method": "doi_in_pdf",
                "validation_score": 1.0,
                "reason": None,
                **base,
            }
        if expected_doi and pdf_doi != expected_doi:
            # A conflicting DOI inside the PDF is a hard, unconditional
            # rejection: this is a different article, no matter how strong
            # the source's own trust level or the title similarity is.
            return {
                "identity_validated": False,
                "validation_method": "doi_in_pdf",
                "validation_score": 0.0,
                "reason": "doi_mismatch",
                **base,
            }

    # --- Supplementary material is never the article, whatever the record says.
    if is_supplementary_material(pdf_identity):
        return {
            "identity_validated": False,
            "validation_method": "supplementary_material",
            "validation_score": 0.0,
            "reason": "supplementary_material",
            **base,
        }

    # --- Case B: no usable DOI signal from the PDF content.
    # Level 1: the record this candidate came from already proved its DOI
    # equals the requested one (either via an exact-DOI source query, or via
    # records_matching_doi()/dois_equal() filtering upstream).
    #
    # That record-level trust does NOT survive a PDF whose own title is a
    # different work: aggregators and repositories do hand out the wrong file
    # under a correct DOI record (a whole proceedings volume or book in place
    # of one chapter, for instance). When the PDF states a title, it
    # contradicts the expected one, and nothing else about the file
    # corroborates the record, the candidate is rejected.
    if expected_doi and record_doi_matched:
        if _pdf_title_contradicts(expected, pdf_identity):
            return {
                "identity_validated": False,
                "validation_method": "doi_in_record",
                "validation_score": 0.0,
                "reason": "pdf_title_contradicts_record",
                **base,
            }
        return {
            "identity_validated": True,
            "validation_method": "doi_in_record",
            "validation_score": 0.95,
            "reason": None,
            **base,
        }

    # Level 2: bibliographic comparison (title + corroborating author/journal/year).
    title_score = title_similarity(expected.get("title"), pdf_identity.get("title"))
    author_ok = author_matches(expected.get("author"), pdf_identity.get("author"))
    year_ok = year_matches(expected.get("year"), pdf_identity.get("year"))
    journal_ok = journal_matches(expected.get("journal"), pdf_identity.get("journal"))

    strong_title = title_score >= TITLE_SCORE_STRONG
    corroborated = author_ok or journal_ok or year_ok

    if strong_title and corroborated:
        return {
            "identity_validated": True,
            "validation_method": "title_author_year",
            "validation_score": round(0.7 + 0.3 * title_score, 6),
            "reason": None,
            **base,
        }

    # A bare, uncorroborated title match — even a fairly high one — is
    # explicitly insufficient per spec: title similarity alone must never
    # stand in for proof of identity when there is a real risk of conflict
    # (two different articles in the same issue/journal, near-duplicate
    # preprint titles, etc.).
    return {
        "identity_validated": False,
        "validation_method": "insufficient_evidence" if not expected_doi else "article_identity_not_confirmed",
        "validation_score": round(title_score, 6),
        "reason": "article_identity_not_confirmed",
        **base,
    }
