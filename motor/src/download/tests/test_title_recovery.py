"""Recuperação por título: só vale quando acha O MESMO artigo.

Caso real (2026-09-25): o DOI 10.1111/jir.12400 ("Oral Abstracts", J Intellect
Disabil Res) falhou pelas fontes de DOI; a recuperação buscou o título e
entregou, como sucesso, um artigo do J Cereb Blood Flow Metab
(10.1177/0271678x231176478) — PDF errado validado contra o DOI errado.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fetch


def _falha(title):
    return {"doi": "10.1/pedido", "success": False, "meta": {"title": title}, "error": {"code": "x"}}


def _recuperado(doi, title):
    return {"doi": doi, "success": True, "file": "/caminho/inexistente/x.pdf", "meta": {"title": title}}


def _rodar(monkeypatch, titulo_pedido, recuperado):
    monkeypatch.setattr(fetch, "_fetch_traced", lambda *a, **k: _falha(titulo_pedido))
    monkeypatch.setattr(fetch, "fetch_title_direct", lambda *a, **k: recuperado)
    return fetch.fetch("10.1/pedido", Path("/tmp"), dry_run=False, overwrite=False, timeout=5)


def test_titulo_generico_nunca_troca_de_artigo(monkeypatch):
    r = _rodar(monkeypatch, "Oral Abstracts",
               _recuperado("10.1177/0271678x231176478", "Oral Abstracts"))
    assert r["success"] is False
    assert r["doi"] == "10.1/pedido"


def test_titulo_diferente_nao_e_recuperacao(monkeypatch):
    r = _rodar(monkeypatch, "Gut microbiota composition in children with autism spectrum disorder",
               _recuperado("10.9/outro", "Cerebral blood flow after ischemic stroke in adults"))
    assert r["success"] is False


def test_mesmo_titulo_especifico_aceita_outra_versao(monkeypatch):
    """Preprint/versão aceita com o mesmo título continua valendo."""
    titulo = "Gut microbiota composition in children with autism spectrum disorder"
    r = _rodar(monkeypatch, titulo, _recuperado("10.1101/2024.01.01.123456", titulo))
    assert r["success"] is True
    assert r["recovery"]["from_doi"] == "10.1/pedido"
