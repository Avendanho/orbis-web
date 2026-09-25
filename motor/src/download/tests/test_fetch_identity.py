"""Integration tests for the identity-validation gate wired into fetch.py.

These tests never touch the network: every resolver / download call that
would normally hit a real API is monkeypatched. They exercise the actual
fetch() control flow (source loop, cache pre-check, expanded-discovery
layer) so the regression they guard against — mixing PDFs/metadata across
unrelated search results, and trusting a cached file without validating its
identity — is caught at the same layer where the original bug lived.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fetch
import identity as idn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pdf_bytes(text: str) -> bytes:
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
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _fake_download_writer(pdf_bytes: bytes):
    """Build a drop-in replacement for fetch._download that writes fixed bytes."""

    def _fake_download(url, dest, *, timeout):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(pdf_bytes)
        return None

    return _fake_download


# ---------------------------------------------------------------------------
# Test 1 / Test 4: DOI correct + PDF correct -> CONFIRMED
# ---------------------------------------------------------------------------

def test_fetch_success_when_pdf_doi_matches_requested_doi(tmp_path, monkeypatch):
    doi = "10.1002/ajmg.a.63953"
    pdf_bytes = _make_pdf_bytes(f"The Correct Article\nJane Doe\nhttps://doi.org/{doi}\n2021")

    monkeypatch.setattr(fetch, "EMAIL", "test@example.com")
    monkeypatch.setattr(fetch, "try_unpaywall", lambda d, *, timeout, errors=None: (
        "https://example.org/paper.pdf", {"title": "The Correct Article", "author": "Doe", "year": 2021},
    ))
    monkeypatch.setattr(fetch, "_download", _fake_download_writer(pdf_bytes))

    result = fetch.fetch(doi, tmp_path, dry_run=False, overwrite=False, timeout=5, sources=["unpaywall"])

    assert result["success"] is True
    assert result["identity_validated"] is True
    assert result["validation_method"] == "doi_in_pdf"
    assert Path(result["file"]).exists()
    assert Path(result["file"] + ".identity.json").exists()


# ---------------------------------------------------------------------------
# Test 2 / Test 21: DOI A requested, source serves PDF of DOI B -> REJECTED
# ---------------------------------------------------------------------------

def test_fetch_rejects_when_downloaded_pdf_belongs_to_a_different_doi(tmp_path, monkeypatch):
    requested_doi = "10.1002/ajmg.a.63953"
    wrong_doi = "10.1002/ajmg.b.33061"
    pdf_bytes = _make_pdf_bytes(f"Article B\nSomeone Else\nhttps://doi.org/{wrong_doi}\n2015")

    monkeypatch.setattr(fetch, "EMAIL", "test@example.com")
    monkeypatch.setattr(fetch, "try_unpaywall", lambda d, *, timeout, errors=None: (
        "https://example.org/wrong.pdf", {"title": "Article A (as indexed)"},
    ))
    monkeypatch.setattr(fetch, "_download", _fake_download_writer(pdf_bytes))

    result = fetch.fetch(requested_doi, tmp_path, dry_run=False, overwrite=False, timeout=5, sources=["unpaywall"])

    assert result["success"] is False
    assert result["error"]["code"] == "article_identity_not_confirmed"
    assert result["identity_rejections"][0]["reason"] == "doi_mismatch"
    # The wrongly-identified file must not be left on disk as if it were a match.
    assert not any(tmp_path.glob("*.pdf"))


# ---------------------------------------------------------------------------
# Test 7: structurally corrupted PDF -> REJECTED (never reaches identity gate)
# ---------------------------------------------------------------------------

def test_fetch_rejects_corrupted_pdf_before_identity_check(tmp_path, monkeypatch):
    doi = "10.1002/ajmg.a.63953"

    def _fake_download(url, dest, *, timeout):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"%PDF-1.4 not actually a valid pdf body")
        return "corrupted_pdf_structure"

    monkeypatch.setattr(fetch, "EMAIL", "test@example.com")
    monkeypatch.setattr(fetch, "try_unpaywall", lambda d, *, timeout, errors=None: (
        "https://example.org/broken.pdf", {},
    ))
    monkeypatch.setattr(fetch, "_download", _fake_download)

    result = fetch.fetch(doi, tmp_path, dry_run=False, overwrite=False, timeout=5, sources=["unpaywall"])

    assert result["success"] is False
    # A download-layer rejection, not an identity-layer one.
    assert "download" in result["error"]["code"]


# ---------------------------------------------------------------------------
# Test 3: Zenodo (and friends) must never mix hits from different records
# ---------------------------------------------------------------------------

def test_try_zenodo_does_not_leak_urls_across_unrelated_hits(monkeypatch):
    requested_doi = "10.1002/ajmg.a.63953"
    other_doi = "10.1002/ajmg.b.33061"

    zenodo_payload = {
        "hits": {
            "hits": [
                {
                    "id": 111,
                    "metadata": {
                        "doi": other_doi,
                        "title": "An unrelated article B",
                    },
                    "files": [{"key": "wrong.pdf", "links": {"download": "https://zenodo.org/record/111/files/wrong.pdf"}}],
                },
                {
                    "id": 222,
                    "metadata": {
                        "doi": requested_doi,
                        "title": "The correct article A",
                    },
                    "files": [{"key": "right.pdf", "links": {"download": "https://zenodo.org/record/222/files/right.pdf"}}],
                },
            ]
        }
    }
    monkeypatch.setattr(fetch, "_get_json", lambda url, *, timeout: zenodo_payload)

    urls, meta, candidates = fetch.try_zenodo(doi=requested_doi, timeout=5)

    assert all("222" in u for u in urls), f"leaked URL(s) from the wrong record: {urls}"
    assert meta["doi"] == requested_doi
    assert len(candidates) == 1


def test_try_zenodo_returns_nothing_when_no_hit_matches_the_doi(monkeypatch):
    """When Zenodo has no record actually carrying the requested DOI, no
    unrelated hit's files may be substituted in — better to report nothing
    than to hand back the wrong article."""
    requested_doi = "10.1002/ajmg.a.63953"
    zenodo_payload = {
        "hits": {
            "hits": [
                {"id": 1, "metadata": {"doi": "10.5281/zenodo.1", "title": "Unrelated deposit 1"}},
                {"id": 2, "metadata": {"doi": "10.5281/zenodo.2", "title": "Unrelated deposit 2"}},
            ]
        }
    }
    monkeypatch.setattr(fetch, "_get_json", lambda url, *, timeout: zenodo_payload)

    urls, meta, candidates = fetch.try_zenodo(doi=requested_doi, timeout=5)

    assert urls == []
    assert candidates == []


def test_try_zenodo_title_mode_only_uses_best_matching_hit(monkeypatch):
    target_title = "Genetic basis of X-linked syndrome: a case report"
    zenodo_payload = {
        "hits": {
            "hits": [
                {
                    "id": 1,
                    "metadata": {"title": "Completely unrelated dataset about volcanoes"},
                    "files": [{"links": {"download": "https://zenodo.org/record/1/files/unrelated.pdf"}}],
                },
                {
                    "id": 2,
                    "metadata": {"title": target_title},
                    "files": [{"links": {"download": "https://zenodo.org/record/2/files/correct.pdf"}}],
                },
            ]
        }
    }
    monkeypatch.setattr(fetch, "_get_json", lambda url, *, timeout: zenodo_payload)

    urls, meta, candidates = fetch.try_zenodo(title=target_title, timeout=5)

    assert all("record/2" in u for u in urls)
    assert meta["title"] == target_title


# ---------------------------------------------------------------------------
# Test 8 / Test 9: cache trust must depend on prior validation
# ---------------------------------------------------------------------------

def test_cache_hit_reused_when_sidecar_already_validated(tmp_path, monkeypatch):
    doi = "10.1002/ajmg.a.63953"
    dest = tmp_path / "doe_2021_pnas_article.pdf"
    dest.write_bytes(_make_pdf_bytes(f"Title\nDoe\nhttps://doi.org/{doi}"))
    sidecar = dest.with_name(dest.name + ".identity.json")
    sidecar.write_text(json.dumps({
        "identity_validated": True,
        "validation_method": "doi_in_pdf",
        "validation_score": 1.0,
        "expected": {"doi": doi, "title": None, "author": None, "journal": None, "year": None},
        "detected_doi": doi,
    }))

    # No resolver should even be consulted: the cache pre-check must short
    # circuit before any source is tried.
    monkeypatch.setattr(fetch, "try_unpaywall", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network hit")))

    result = fetch.fetch(doi, tmp_path, dry_run=False, overwrite=False, timeout=5, sources=["unpaywall"])

    assert result["success"] is True
    assert result["source"] == "cache"
    assert result["file"] == str(dest)


def test_cache_hit_not_trusted_when_never_validated_and_content_does_not_confirm(tmp_path, monkeypatch):
    """A previously downloaded PDF with no identity sidecar (legacy state, or
    the exact bug this pipeline used to have) must be re-verified, and
    rejected, rather than replayed as an automatic success."""
    doi = "10.1002/ajmg.a.63953"
    doi_slug = fetch._slug(doi, n=20).lower()
    # Legacy-style filename containing the doi slug (the old, sole trust
    # signal) but with content that proves it is NOT the requested article.
    dest = tmp_path / f"{doi_slug}_some_legacy_name.pdf"
    dest.write_bytes(_make_pdf_bytes("An unrelated article with a different topic entirely."))

    monkeypatch.setattr(fetch, "EMAIL", "")  # disable unpaywall cleanly -> falls straight through
    monkeypatch.setattr(fetch, "try_unpaywall", lambda *a, **k: (None, {}))

    result = fetch.fetch(doi, tmp_path, dry_run=False, overwrite=False, timeout=5, sources=["unpaywall"])

    assert result.get("success") is not True
    # The stale file must be quarantined, not silently reused.
    assert not dest.exists()
    quarantined = list(tmp_path.glob("*.INVALID_IDENTITY.pdf"))
    assert len(quarantined) == 1


# ---------------------------------------------------------------------------
# Test 10: DOIs sharing a long common prefix must never share a cache identity
# ---------------------------------------------------------------------------

def test_similar_prefix_dois_do_not_share_cached_file(tmp_path):
    doi_a = "10.1002/ajmg.a.63953"
    doi_b = "10.1002/ajmg.b.33061"

    dest_a = tmp_path / "article_a.pdf"
    dest_a.write_bytes(_make_pdf_bytes(f"Article A\nhttps://doi.org/{doi_a}"))
    sidecar_a = dest_a.with_name(dest_a.name + ".identity.json")
    sidecar_a.write_text(json.dumps({
        "identity_validated": True, "expected": {"doi": doi_a}, "detected_doi": doi_a,
    }))

    found_for_b = fetch._find_cached_pdf_for_doi(tmp_path, doi_b)
    assert found_for_b is None

    found_for_a = fetch._find_cached_pdf_for_doi(tmp_path, doi_a)
    assert found_for_a == dest_a
