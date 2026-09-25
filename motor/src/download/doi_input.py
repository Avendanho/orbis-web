"""Leitura da lista de entrada: DOIs, PMIDs, PMCIDs, arXiv e títulos soltos.

A lista de trabalho de uma revisão sistemática raramente chega limpa. Sai de
um gerenciador de referências, de uma planilha ou da exportação de uma base, e
vem misturada: DOI puro, DOI como URL, ``PMID: 12345678``, ``arXiv:2101.00001``
e linhas que são só o título do artigo.

Este módulo reconhece cada forma e separa em duas listas — o que tem
identificador e o que vai precisar ser resolvido pelo título. Resolver o
título é bem mais caro e mais sujeito a erro, então vale distinguir na
entrada em vez de descobrir no meio da execução.

O acesso ao cliente HTTP passa por ``runtime`` — veja lá por que não é um
import direto de ``fetch``.
"""
from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

import runtime
from identity import normalize_doi

# Forma canônica de um DOI. Vive aqui, e não em fetch.py, porque é a
# entrada que ela descreve; fetch.py a reexporta para a mensagem de erro
# da CLI e para o schema.
DOI_PATTERN = r"^10\..+/.+$"

_DOI_RE = re.compile(DOI_PATTERN)


def _read_text_any(path: Path) -> str:
    """Read an uploaded list as UTF-8 (with or without BOM), falling back to Latin-1."""
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


_ARXIV_ID_RE = re.compile(
    r"^(?:arxiv:\s*|https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/)?"
    r"(\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)(?:\.pdf)?$",
    re.IGNORECASE,
)


_PMCID_RE = re.compile(r"^(?:pmcid:\s*)?(PMC\d+)$", re.IGNORECASE)


_PMID_RE = re.compile(r"^(?:pmid:?\s*)(\d{1,9})$", re.IGNORECASE)


def _identifier_to_doi(line: str) -> str | None:
    """DOI for an arXiv id, PMCID or PMID line of an input list (else None).

    arXiv ids map to arXiv's DataCite DOI (10.48550/arXiv.<id>), which the
    cascade routes straight to arXiv; PMCIDs and PMIDs (e.g. the "PMID 123"
    lines the PubMed search writes for records without a DOI) go through
    NCBI's ID converter.
    """
    text = line.strip()
    m = _ARXIV_ID_RE.match(text)
    if m:
        return "10.48550/arXiv." + re.sub(r"v\d+$", "", m.group(1))
    m = _PMCID_RE.match(text) or _PMID_RE.match(text)
    if not m:
        return None
    try:
        data = runtime._get_json(
            "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/?"
            + urllib.parse.urlencode({"ids": m.group(1), "format": "json", "tool": "paper-fetch"}),
            timeout=15,
        )
    except Exception:
        return None
    for rec in data.get("records") or []:
        doi = normalize_doi(rec.get("doi") or "")
        if doi and _DOI_RE.match(doi):
            return doi
    return None


def _load_dois_and_titles_from_file(path: Path) -> tuple[list[str], list[str]]:
    """Read DOI's.txt and separate DOI lines from trailing article titles.

    Every non-empty line up to and including the last valid DOI is treated as
    part of the DOI section. Every non-empty line after the last valid DOI is
    treated as an article title.

    This allows DOI's.txt to have the following structure::

        10.1234/example.one
        10.5678/example.two

        Article title one
        Article title two

    Optional section headers such as ``ARTIGOS:`` are ignored.
    """
    lines = [
        line.strip()
        for line in _read_text_any(path).splitlines()
        if line.strip()
    ]

    dois: list[str] = []
    titles: list[str] = []

    ignored_headers = {
        "artigo", "artigos", "artigos:", "titulo", "título", "titulos",
        "títulos", "titulo:", "título:", "titulos:", "títulos:",
        "nomes dos artigos", "nomes dos artigos:", "dois", "doi", "dois:", "doi:"
    }

    for line in lines:
        if line.casefold() in ignored_headers or re.fullmatch(r"[-=_]{3,}", line):
            continue
        
        candidate = normalize_doi(line)
        if _DOI_RE.match(candidate):
            dois.append(candidate)
            continue
        resolved = _identifier_to_doi(line)
        if resolved:
            dois.append(resolved)
        else:
            titles.append(line)

    return dois, titles
