"""Comparação bibliográfica de títulos, autores e anos.

Quando um artigo chega sem DOI — ou com um DOI que nenhuma fonte reconhece —
a recuperação passa a depender do título. Isso é busca aproximada: a mesma
obra aparece com subtítulo ou sem, com pontuação diferente, em maiúsculas, com
o nome do periódico grudado. As funções aqui decidem se dois registros
bibliográficos são a mesma coisa, e com que confiança.

Tudo é função pura, sem rede e sem estado. É de propósito: é a camada que
decide se um PDF será aceito como sendo o artigo pedido, e esse julgamento
precisa ser verificável sozinho.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import os
import re
import unicodedata

import identity as _identity
from identity import normalize_doi

_MIN_TITLE_LEN = 6
TITLE_SCORE_MIN = 40.0   # piso absoluto; abaixo disso o primeiro colocado é suspeito
TITLE_GAP_MIN = 3.0      # distância para o segundo; abaixo disso o topo é ambíguo
# Quantas grafias alternativas do mesmo título vale a pena consultar.
TITLE_VARIANT_COUNT = max(2, int(os.environ.get("PAPER_FETCH_TITLE_VARIANTS", "6")))

# Minimum title length we'll send to a resolver. Anything shorter is almost
# certainly a typo or one-word query that will return noise.
_MIN_TITLE_LEN = 6


# Heuristic confidence thresholds for Crossref's relevance score. The score
# is unitless and scales with title length, so these are calibrated to be
# permissive — anything obviously sloppy still produces a low_confidence
# flag rather than silently picking the wrong paper.
TITLE_SCORE_MIN = 40.0   # absolute floor; below this the top is suspect


TITLE_GAP_MIN = 3.0      # gap from top to runner-up; below this the top is ambiguous


def _classify_low_confidence(score: float | None, gap: float | None) -> str | None:
    """Identify why a Crossref top match should be treated as low-confidence.

    Returns a single short reason string, or None if both heuristics pass.
    Order matters: ``score_below_threshold`` is the more diagnostic signal,
    so report that first when both fire.
    """
    if score is not None and score < TITLE_SCORE_MIN:
        return "score_below_threshold"
    if gap is not None and gap < TITLE_GAP_MIN:
        return "ambiguous_runner_up"
    return None


def _norm_match_text(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = re.sub(r"&", " and ", text)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _token_jaccard(a: str, b: str) -> float:
    sa = set(_norm_match_text(a).split())
    sb = set(_norm_match_text(b).split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _title_similarity(a: str, b: str) -> float:
    """Robust bibliographic-title similarity.

    The resolver must tolerate common index differences such as:
      - omitted subtitles;
      - punctuation/Unicode differences;
      - hyphen vs space;
      - singular/plural or minor token noise;
      - title metadata with small formatting additions.

    At the same time, it must not treat merely related-topic titles as the
    same paper.  The requested-title token coverage therefore contributes a
    strong signal, while character similarity handles formatting changes.
    """
    na = _norm_match_text(a)
    nb = _norm_match_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    sa = set(na.split())
    sb = set(nb.split())
    if not sa or not sb:
        return 0.0

    seq = SequenceMatcher(None, na, nb).ratio()
    jac = len(sa & sb) / len(sa | sb)
    coverage = len(sa & sb) / len(sa)

    # Also compare token sequences after sorting unique tokens. This helps
    # with indexes that reorder subtitle fragments or publication metadata.
    sorted_a = " ".join(sorted(sa))
    sorted_b = " ".join(sorted(sb))
    sorted_seq = SequenceMatcher(None, sorted_a, sorted_b).ratio()

    score = (
        0.34 * coverage
        + 0.26 * jac
        + 0.25 * seq
        + 0.15 * sorted_seq
    )
    return round(min(1.0, score), 6)


def _deep_find_pdf_urls(obj, *, limit: int = 12) -> list[str]:
    """Best-effort recursive extraction of direct-looking PDF URLs from JSON."""
    found: list[str] = []
    seen: set[str] = set()

    def walk(node):
        if len(found) >= limit:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str):
                    raw = value.strip()
                    if raw.startswith(("http://", "https://")):
                        low = raw.casefold()
                        if (
                            ".pdf" in low
                            or "/pdf/" in low
                            or "download" in low and ("file" in key.casefold() or "content" in key.casefold())
                            or key.casefold() in {"download", "download_url", "pdf_url", "file_url", "fulltext_url", "content_url"}
                        ):
                            clean = raw.replace("&amp;", "&")
                            if clean not in seen:
                                seen.add(clean)
                                found.append(clean)
                elif isinstance(value, list) and key.casefold() in {"pdf_candidates", "download_url", "pdf_url", "content_url"}:
                    for url in value:
                        if isinstance(url, str) and url.startswith(("http://", "https://")) and url not in seen:
                            seen.add(url)
                            found.append(url)
                elif isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
                if len(found) >= limit:
                    break

    walk(obj)
    return found


def _extract_first_year(meta: dict) -> int | None:
    for key in ("year", "publication_year", "published", "issued"):
        value = meta.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            m = re.search(r"\b(19|20)\d{2}\b", value)
            if m:
                return int(m.group(0))
    return None


def _merge_candidate_meta(*metas: dict | None) -> dict:
    out: dict = {}
    for meta in metas:
        if not isinstance(meta, dict):
            continue
        for key, value in meta.items():
            if value and not out.get(key):
                out[key] = value
    return out


def _match_records(records: list[dict], doi: str | None, title: str | None, *, doi_getter, title_getter) -> list[dict]:
    """Records that provably belong to the requested article, never a loose hit."""
    if doi:
        return _identity.records_matching_doi(records, doi, doi_getter=doi_getter)
    if title:
        return _identity.filter_hits_by_title(records, title, title_getter=title_getter)
    return []


def _rank_title_candidates(requested_title: str, candidates: list[dict]) -> tuple[dict | None, list[dict]]:
    """Strictly rank title candidates and reject ambiguous/weak matches."""
    ranked: list[dict] = []
    for cand in candidates:
        ct = cand.get("title") or ""
        if not ct:
            continue
        title_score = _title_similarity(requested_title, ct)
        # Author agreement is a useful secondary signal when available.
        author_bonus = 0.0
        if cand.get("author"):
            # For title-only searches we only use a small bonus; never enough
            # to rescue a clearly wrong title.
            author_bonus = 0.03
        total = min(1.0, title_score + author_bonus)
        c = dict(cand)
        c["title_similarity"] = title_score
        c["rank_score"] = round(total, 6)
        ranked.append(c)
    ranked.sort(key=lambda x: x.get("rank_score", 0.0), reverse=True)
    return (ranked[0] if ranked else None), ranked[:10]


def _title_variants(title: str) -> list[str]:
    """Create deterministic search variants without changing the user's source title."""
    raw = re.sub(r"\s+", " ", (title or "").strip())
    raw = re.sub(r"^[\[\(\{\"'‘“]+|[\]\)\}\"'’”\.]+$", "", raw).strip()
    if not raw:
        return []

    variants: list[str] = []
    def add(v: str):
        v = re.sub(r"\s+", " ", v).strip(" \t\r\n.,;:")
        if v and len(v) >= _MIN_TITLE_LEN and v not in variants:
            variants.append(v)

    add(raw)

    # Remove subtitles after colon / em dash only as a fallback, never as the primary query.
    for sep in (":", " — ", " – ", " - "):
        if sep in raw:
            add(raw.split(sep, 1)[0])

    # Parenthetical stripping often fixes older titles containing issue / model notes.
    no_paren = re.sub(r"\([^()]{1,100}\)", " ", raw)
    add(no_paren)

    # Normalize common scholarly punctuation variants.
    add(raw.replace("&", "and"))
    add(raw.replace(" and ", " & "))

    # De-duplicate whitespace/punctuation noise.
    add(re.sub(r"[\[\]{}<>]", " ", raw))

    return variants[:TITLE_VARIANT_COUNT]


def _candidate_key(rec: dict) -> str:
    doi = normalize_doi(str(rec.get("doi") or ""))
    if doi:
        return f"doi:{doi}"
    return f"title:{_norm_match_text(str(rec.get('title') or ''))}"


def _year_similarity(requested_year, candidate_year) -> float:
    try:
        ry = int(requested_year) if requested_year else None
        cy = int(candidate_year) if candidate_year else None
    except Exception:
        return 0.0
    if ry is None or cy is None:
        return 0.0
    if ry == cy:
        return 1.0
    if abs(ry - cy) == 1:
        return 0.5
    return 0.0


def _author_similarity(requested_author: str | None, candidate_author: str | None) -> float:
    if not requested_author or not candidate_author:
        return 0.0
    ra = _norm_match_text(requested_author).split()
    ca = _norm_match_text(candidate_author).split()
    if not ra or not ca:
        return 0.0
    return 1.0 if ra[-1] == ca[-1] else 0.0


def _rank_title_recovery_candidates(requested_title: str, records: list[dict]) -> list[dict]:
    """Rank title candidates using title coverage + source corroboration.

    A single weak API hit is not enough.  Independent sources confirming the
    same DOI/title receive a corroboration bonus.  This is what lets legacy,
    punctuation-heavy and subtitle-shifted titles resolve without reopening
    the old "first related result" false-positive problem.
    """
    grouped: dict[str, dict] = {}
    req_tokens = set(_norm_match_text(requested_title).split())

    for raw in records:
        ct = str(raw.get("title") or "").strip()
        if not ct:
            continue

        score = _title_similarity(requested_title, ct)
        cand_tokens = set(_norm_match_text(ct).split())
        coverage = (len(req_tokens & cand_tokens) / len(req_tokens)) if req_tokens else 0.0
        year_bonus = _year_similarity(raw.get("requested_year"), raw.get("year"))
        author_bonus = _author_similarity(raw.get("requested_author"), raw.get("author"))

        c = dict(raw)
        c["pdf_candidates"] = list(dict.fromkeys([*raw.get("pdf_candidates", []), *_deep_find_pdf_urls(raw)]))
        c["title_similarity"] = round(score, 6)
        c["title_coverage"] = round(coverage, 6)
        c["year_similarity"] = round(year_bonus, 6)
        c["author_similarity"] = round(author_bonus, 6)

        key = _candidate_key(c)
        existing = grouped.get(key)
        if existing is None:
            c["_resolvers"] = {str(c.get("resolver") or "").lower()} if c.get("resolver") else set()
            c["_source_count"] = 1
            grouped[key] = c
        else:
            links = list(dict.fromkeys([*existing.get("pdf_candidates", []), *c.get("pdf_candidates", [])]))
            existing["pdf_candidates"] = links
            c["pdf_candidates"] = links
            resolver = str(c.get("resolver") or "").lower()
            if resolver:
                existing.setdefault("_resolvers", set()).add(resolver)
            existing["_source_count"] = len(existing.get("_resolvers", set()))
            # Keep the best metadata record for the candidate.
            current_base = (
                0.50 * float(existing.get("title_similarity") or 0.0)
                + 0.30 * float(existing.get("title_coverage") or 0.0)
                + 0.10 * float(existing.get("year_similarity") or 0.0)
                + 0.10 * float(existing.get("author_similarity") or 0.0)
            )
            candidate_base = (
                0.50 * score
                + 0.30 * coverage
                + 0.10 * year_bonus
                + 0.10 * author_bonus
            )
            if candidate_base > current_base:
                preserved_resolvers = set(existing.get("_resolvers", set()))
                preserved_resolvers.update(existing.get("resolver") and [str(existing["resolver"]).lower()] or [])
                preserved_resolvers.update(c.get("resolver") and [str(c["resolver"]).lower()] or [])
                c["_resolvers"] = preserved_resolvers
                c["_source_count"] = len(preserved_resolvers)
                grouped[key] = c
        # refresh source count after each insertion
        grouped[key]["_source_count"] = len(grouped[key].get("_resolvers", set()))

    ranked: list[dict] = []
    for c in grouped.values():
        source_count = int(c.get("_source_count") or 1)

        # Base score emphasizes identity of the requested title.
        total = (
            0.78 * float(c.get("title_similarity") or 0.0)
            + 0.12 * float(c.get("title_coverage") or 0.0)
            + 0.06 * float(c.get("year_similarity") or 0.0)
            + 0.04 * float(c.get("author_similarity") or 0.0)
        )

        # Independent-source corroboration is valuable, but modest so it
        # cannot rescue a clearly wrong title.
        corroboration_bonus = min(0.08, 0.02 * max(0, source_count - 1))
        total = min(1.0, total + corroboration_bonus)

        c["source_count"] = source_count
        c["source_corrob_bonus"] = round(corroboration_bonus, 6)
        c["rank_score"] = round(total, 6)
        c["resolvers"] = sorted(
            r for r in c.get("_resolvers", set())
            if r
        )
        c.pop("_resolvers", None)
        c.pop("_source_count", None)
        ranked.append(c)

    ranked.sort(key=lambda x: x.get("rank_score", 0.0), reverse=True)
    return ranked[:30]
