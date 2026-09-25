"""Unit tests for src/download/identity.py — the bibliographic identity gate.

Pure/offline: no network access, no real PDFs required for most cases (a
minimal in-memory PDF is generated with pypdf for the PDF-extraction tests).
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import identity as idn


# ---------------------------------------------------------------------------
# DOI normalization
# ---------------------------------------------------------------------------

def test_normalize_doi_strips_url_forms():
    assert idn.normalize_doi("https://doi.org/10.1002/ajmg.a.63953") == "10.1002/ajmg.a.63953"
    assert idn.normalize_doi("http://dx.doi.org/10.1002/ajmg.a.63953") == "10.1002/ajmg.a.63953"
    assert idn.normalize_doi("doi:10.1002/AJMG.A.63953") == "10.1002/ajmg.a.63953"
    assert idn.normalize_doi("  10.1002/ajmg.a.63953.  ") == "10.1002/ajmg.a.63953"


def test_normalize_doi_empty():
    assert idn.normalize_doi(None) == ""
    assert idn.normalize_doi("") == ""


def test_dois_equal():
    assert idn.dois_equal("https://doi.org/10.1002/ajmg.a.63953", "10.1002/AJMG.A.63953")
    assert not idn.dois_equal("10.1002/ajmg.a.63953", "10.1002/ajmg.b.33061")


def test_similar_prefix_dois_are_not_equal():
    """Regression: DOIs sharing a long common prefix must never collide."""
    a = "10.1002/ajmg.a.63953"
    b = "10.1002/ajmg.b.33061"
    assert not idn.dois_equal(a, b)
    # Also guard the truncated-slug bug directly: a 20-char slug of each
    # must not be usable as a shared identity key.
    import re
    slug_a = re.sub(r"[^A-Za-z0-9]+", "_", a).strip("_")[:20]
    slug_b = re.sub(r"[^A-Za-z0-9]+", "_", b).strip("_")[:20]
    assert slug_a != slug_b  # sanity: still distinguishable at 20 chars here
    assert idn.normalize_doi(a) != idn.normalize_doi(b)


# ---------------------------------------------------------------------------
# extract_record_doi / records_matching_doi
# ---------------------------------------------------------------------------

def test_extract_record_doi_zenodo_shape():
    record = {"metadata": {"doi": "10.5281/zenodo.1234", "title": "Some Deposit"}}
    assert idn.extract_record_doi(record) == "10.5281/zenodo.1234"


def test_extract_record_doi_identifiers_list_shape():
    record = {
        "attributes": {
            "identifiers": [{"identifierType": "DOI", "identifier": "10.1002/ajmg.a.63953"}]
        }
    }
    assert idn.extract_record_doi(record) == "10.1002/ajmg.a.63953"


def test_extract_record_doi_none_when_absent():
    record = {"title": "A record with no DOI at all", "abstract": "mentions 10.1002/other in prose but not as an id field... actually it does contain a doi-shaped string"}
    # abstract/text fields are intentionally not scanned — only structured
    # DOI-ish keys count as identity evidence.
    assert idn.extract_record_doi(record) is None


def test_records_matching_doi_filters_correctly():
    target = "10.1002/ajmg.a.63953"
    hits = [
        {"metadata": {"doi": "10.5281/zenodo.999", "title": "Unrelated deposit B"}},
        {"metadata": {"doi": "10.1002/ajmg.a.63953", "title": "The correct article A"}},
        {"metadata": {"title": "No doi at all"}},
    ]
    matched = idn.records_matching_doi(hits, target)
    assert len(matched) == 1
    assert matched[0]["metadata"]["title"] == "The correct article A"


def test_records_matching_doi_empty_when_none_match():
    hits = [{"metadata": {"doi": "10.5281/zenodo.999"}}, {"metadata": {"doi": "10.5281/zenodo.111"}}]
    assert idn.records_matching_doi(hits, "10.1002/ajmg.a.63953") == []


# ---------------------------------------------------------------------------
# Title similarity / filter_hits_by_title
# ---------------------------------------------------------------------------

def test_title_similarity_identical():
    assert idn.title_similarity("Genetic basis of X syndrome", "Genetic basis of X syndrome") == 1.0


def test_title_similarity_formatting_noise_still_high():
    a = "Genetic basis of X-linked syndrome: a case report"
    b = "Genetic basis of X linked syndrome — A Case Report"
    assert idn.title_similarity(a, b) > 0.85


def test_title_similarity_different_articles_low():
    a = "Genetic basis of X-linked syndrome in a Brazilian cohort"
    b = "Environmental toxicology of heavy metals in freshwater fish"
    assert idn.title_similarity(a, b) < 0.3


def test_filter_hits_by_title_keeps_only_best_match():
    hits = [
        {"title": "Completely unrelated deposit"},
        {"title": "Genetic basis of X-linked syndrome: a case report"},
        {"title": "Another unrelated dataset about volcanoes"},
    ]
    kept = idn.filter_hits_by_title(
        hits, "Genetic basis of X-linked syndrome: a case report",
        title_getter=lambda h: h["title"],
    )
    assert len(kept) == 1
    assert kept[0]["title"] == "Genetic basis of X-linked syndrome: a case report"


def test_filter_hits_by_title_empty_when_nothing_matches():
    hits = [{"title": "Completely unrelated deposit"}, {"title": "Another unrelated dataset"}]
    kept = idn.filter_hits_by_title(hits, "Genetic basis of X-linked syndrome", title_getter=lambda h: h["title"])
    assert kept == []


# ---------------------------------------------------------------------------
# PDF content extraction
# ---------------------------------------------------------------------------

def _make_pdf_bytes(text: str, *, title: str | None = None, author: str | None = None) -> bytes:
    import pypdf
    escaped_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    lines = escaped_text.splitlines() or [""]
    text_commands = []
    y = 700
    for l in lines:
        text_commands.append(f"1 0 0 1 50 {y} Tm ({l[:110]}) Tj")
        y -= 15
    stream = "BT /F1 12 Tf " + " ".join(text_commands) + " ET"
    stream_b = stream.encode("latin1")
    raw = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
        b"4 0 obj << /Length " + str(len(stream_b)).encode("ascii") + b" >>\nstream\n"
        + stream_b +
        b"\nendstream\nendobj\n"
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
        b"xref\n0 6\n0000000000 65535 f \n"
        b"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n0\n%%EOF\n"
    )
    reader = pypdf.PdfReader(io.BytesIO(raw))
    writer = pypdf.PdfWriter()
    writer.append(reader)
    meta = {}
    if title:
        meta["/Title"] = title
    if author:
        meta["/Author"] = author
    if meta:
        writer.add_metadata(meta)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_extract_pdf_identity_finds_doi_in_text():
    pdf_bytes = _make_pdf_bytes(
        "The Correct Article Title\nJane Doe et al.\nhttps://doi.org/10.1002/ajmg.a.63953\n2021"
    )
    result = idn.extract_pdf_identity(pdf_bytes)
    assert result["doi"] == "10.1002/ajmg.a.63953"


def test_extract_pdf_identity_no_doi_present():
    pdf_bytes = _make_pdf_bytes("A paper with no DOI printed anywhere on the title page.")
    result = idn.extract_pdf_identity(pdf_bytes)
    assert result["doi"] is None


def test_extract_pdf_identity_handles_garbage_gracefully():
    result = idn.extract_pdf_identity(b"not a pdf at all")
    assert result["doi"] is None
    assert result["title"] is None


# ---------------------------------------------------------------------------
# validate_article_identity — the gate itself
# ---------------------------------------------------------------------------

def test_validate_identity_confirmed_via_doi_in_pdf():
    expected = {"doi": "10.1002/ajmg.a.63953", "title": "The correct article"}
    pdf_identity = {"doi": "10.1002/ajmg.a.63953", "title": "The correct article"}
    result = idn.validate_article_identity(expected, pdf_identity=pdf_identity)
    assert result["identity_validated"] is True
    assert result["validation_method"] == "doi_in_pdf"
    assert result["validation_score"] == 1.0


def test_validate_identity_rejected_on_doi_mismatch():
    """Test 2 / Test 21: DOI A requested, PDF actually belongs to DOI B."""
    expected = {"doi": "10.1002/ajmg.a.63953", "title": "Article A"}
    pdf_identity = {"doi": "10.1002/ajmg.b.33061", "title": "Article B"}
    result = idn.validate_article_identity(expected, pdf_identity=pdf_identity)
    assert result["identity_validated"] is False
    assert result["reason"] == "doi_mismatch"


def test_validate_identity_doi_mismatch_overrides_high_title_similarity():
    """A conflicting DOI must reject even when titles look alike."""
    expected = {"doi": "10.1002/ajmg.a.63953", "title": "Genetic basis of X syndrome"}
    pdf_identity = {"doi": "10.1002/ajmg.b.33061", "title": "Genetic basis of X syndrome"}
    result = idn.validate_article_identity(expected, pdf_identity=pdf_identity, record_doi_matched=True)
    assert result["identity_validated"] is False
    assert result["reason"] == "doi_mismatch"


def test_validate_identity_confirmed_via_record_level_doi_when_pdf_has_none():
    """Test 4 variant: DOI known to match at the record level, PDF has no extractable DOI
    (e.g. scanned title page) -> still confirmed, since the record proved identity."""
    expected = {"doi": "10.1002/ajmg.a.63953", "title": "The correct article"}
    pdf_identity = {"doi": None, "title": None}
    result = idn.validate_article_identity(expected, pdf_identity=pdf_identity, record_doi_matched=True)
    assert result["identity_validated"] is True
    assert result["validation_method"] == "doi_in_record"


def test_validate_identity_confirmed_via_title_author_year_when_no_doi_available():
    """Test 5: DOI absent everywhere, but title+author+year strongly corroborate."""
    expected = {
        "doi": None,
        "title": "Genetic basis of X-linked syndrome: a case report",
        "author": "Doe",
        "year": 2021,
    }
    pdf_identity = {
        "doi": None,
        "title": "Genetic basis of X-linked syndrome — a case report",
        "author": "Jane Doe",
        "year": 2021,
    }
    result = idn.validate_article_identity(expected, pdf_identity=pdf_identity, record_doi_matched=False)
    assert result["identity_validated"] is True
    assert result["validation_method"] == "title_author_year"


def test_validate_identity_rejected_when_title_similar_but_different_article():
    """Test 6: similar title, different article -> no DOI, no author/year corroboration."""
    expected = {"doi": None, "title": "Genetic basis of X-linked syndrome in humans", "author": "Doe", "year": 2021}
    pdf_identity = {"doi": None, "title": "Genetic basis of Y-linked syndrome in mice", "author": "Smith", "year": 2015}
    result = idn.validate_article_identity(expected, pdf_identity=pdf_identity, record_doi_matched=False)
    assert result["identity_validated"] is False


def test_validate_identity_rejected_on_insufficient_evidence():
    """A bare, uncorroborated title match must never be accepted as identity."""
    expected = {"doi": None, "title": "Genetic basis of X-linked syndrome: a case report"}
    pdf_identity = {"doi": None, "title": "Genetic basis of X-linked syndrome — a brief report"}
    result = idn.validate_article_identity(expected, pdf_identity=pdf_identity, record_doi_matched=False)
    assert result["identity_validated"] is False
    assert result["reason"] == "article_identity_not_confirmed"


def test_validate_identity_rejected_when_expected_doi_present_but_no_evidence_at_all():
    expected = {"doi": "10.1002/ajmg.a.63953", "title": None}
    pdf_identity = {"doi": None, "title": None}
    result = idn.validate_article_identity(expected, pdf_identity=pdf_identity, record_doi_matched=False)
    assert result["identity_validated"] is False


def test_marca_dagua_do_biorxiv_nao_gruda_doi_no_identificador():
    """O bioRxiv e o medRxiv carimbam cada página com

        https://doi.org/10.1101/2023.07.08.548192doi: bioRxiv preprint

    sem espaço entre o identificador e o 'doi:' seguinte. O padrão guloso
    levava o 'doi' junto, e a identidade falhava para *todo* preprint desses
    servidores — que são justamente os mais recuperáveis em acesso aberto.
    """
    texto = "ly 8, 2023. ; https://doi.org/10.1101/2023.07.08.548192doi: bioRxiv preprint"
    achados = idn.DOI_LIKE_RE.findall(texto)
    assert "10.1101/2023.07.08.548192" in achados
    assert "10.1101/2023.07.08.548192doi" not in achados


def test_marca_dagua_do_medrxiv_tambem():
    texto = "https://doi.org/10.1101/2024.01.29.24301949doi: medRxiv preprint"
    assert "10.1101/2024.01.29.24301949" in idn.DOI_LIKE_RE.findall(texto)


def test_doi_que_termina_de_verdade_em_doi_e_preservado():
    # Sufixo legítimo: não pode ser recortado por engano.
    assert "10.1000/algumacoisadoi" in idn.DOI_LIKE_RE.findall("10.1000/algumacoisadoi ")


def test_dois_normais_continuam_intactos():
    # DOIs com parênteses (10.1290/1071-2690(2000)036<...>) ficam truncados por
    # uma limitação antiga do padrão, que exclui ')' para não engolir a
    # pontuação de frases. É outro problema, não este.
    for d in ("10.1002/aur.1227", "10.1016/j.psychres.2022.114586",
              "10.1007/s00253-005-0098-3"):
        assert d in idn.DOI_LIKE_RE.findall(d + " "), d
