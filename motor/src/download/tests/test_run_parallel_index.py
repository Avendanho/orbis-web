"""Tests for the sqlite download-index / cache-trust layer in run_parallel.py.

Covers report requirements #11-#13: the index key is the fully normalized
DOI (never a truncated slug), and a row is only ever treated as an instant
cache hit when it was recorded with identity_validated=True.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_parallel as rp


def _make_pdf(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    import io
    import pypdf
    content = b"Padding " * 200
    raw = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
        b"4 0 obj << /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n"
        + content +
        b"\nendstream\nendobj\n"
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
        b"xref\n0 6\n0000000000 65535 f \n"
        b"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n0\n%%EOF\n"
    )
    reader = pypdf.PdfReader(io.BytesIO(raw))
    writer = pypdf.PdfWriter()
    writer.append(reader)
    out = io.BytesIO()
    writer.write(out)
    data = out.getvalue()
    if len(data) < 1024:
        data += b"%" + b"P" * (1024 - len(data)) + b"\n%%EOF\n"
    p.write_bytes(data)
    return p


def test_index_key_is_normalized_doi_not_truncated_slug(tmp_path):
    pdf = _make_pdf(tmp_path, "article.pdf")
    rp._record_download_index(
        tmp_path, "https://doi.org/10.1002/AJMG.A.63953", str(pdf.name),
        identity_validated=True, validation_method="doi_in_pdf",
    )
    idx = rp._load_download_index(tmp_path)
    assert "10.1002/ajmg.a.63953" in idx
    assert idx["10.1002/ajmg.a.63953"]["identity_validated"] is True


def test_similar_prefix_dois_get_distinct_index_entries(tmp_path):
    pdf_a = _make_pdf(tmp_path, "article_a.pdf")
    pdf_b = _make_pdf(tmp_path, "article_b.pdf")
    rp._record_download_index(tmp_path, "10.1002/ajmg.a.63953", str(pdf_a.name), identity_validated=True)
    rp._record_download_index(tmp_path, "10.1002/ajmg.b.33061", str(pdf_b.name), identity_validated=True)

    idx = rp._load_download_index(tmp_path)
    assert idx["10.1002/ajmg.a.63953"]["filepath"] == str(pdf_a.name)
    assert idx["10.1002/ajmg.b.33061"]["filepath"] == str(pdf_b.name)
    assert idx["10.1002/ajmg.a.63953"]["filepath"] != idx["10.1002/ajmg.b.33061"]["filepath"]


def test_check_already_downloaded_trusts_validated_entry(tmp_path):
    pdf = _make_pdf(tmp_path, "article.pdf")
    rp._record_download_index(tmp_path, "10.1002/ajmg.a.63953", str(pdf.name), identity_validated=True)

    hit = rp._check_already_downloaded("10.1002/ajmg.a.63953", tmp_path)
    assert hit == pdf


def test_check_already_downloaded_ignores_unvalidated_entry():
    """A row recorded before this migration (or one whose identity check
    failed) must not be treated as an instant cache hit — the caller is
    expected to fall through to fetch(), which re-validates authoritatively."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp_path = Path(d)
        pdf = _make_pdf(tmp_path, "article.pdf")
        rp._record_download_index(tmp_path, "10.1002/ajmg.a.63953", str(pdf.name), identity_validated=False)

        hit = rp._check_already_downloaded("10.1002/ajmg.a.63953", tmp_path)
        assert hit is None


def test_check_already_downloaded_no_entry_returns_none(tmp_path):
    assert rp._check_already_downloaded("10.1002/does.not.exist/1", tmp_path) is None
