"""Rotas institucionais e filtros de fonte, no nível do fetch.

* a sessão institucional (CAPES/EZproxy no navegador) é uma fonte da cascata,
  e o PDF dela passa pela mesma checagem de identidade de qualquer outro;
* EZproxy/proxy configurado já é modo institucional para as URLs diretas das
  editoras — sem elas, não há o que reescrever para um artigo fechado;
* o EZproxy segue o ``citation_pdf_url`` da página autenticada;
* ``sources`` limita também a camada de descoberta expandida.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch  # noqa: E402
import institutional  # noqa: E402
import sessao_navegador  # noqa: E402
from test_fetch_identity import _make_pdf_bytes  # noqa: E402

DOI = "10.1007/s10719-009-9256-7"


def _pdf_com(texto: str) -> bytes:
    # validate_pdf_data recusa arquivos com menos de 1 KB.
    return _make_pdf_bytes(texto + "\n" + "\n".join(f"linha de corpo {i}" for i in range(60)))


def _cascata(tmp_path, monkeypatch, fontes, **patches):
    monkeypatch.setenv("PAPER_FETCH_TITLE_RECOVERY", "0")
    for nome, valor in patches.items():
        monkeypatch.setattr(fetch, nome, valor)
    return fetch.fetch(DOI, tmp_path, dry_run=False, overwrite=False, timeout=5, sources=fontes)


# ---------------------------------------------------------------------------
# Sessão institucional na cascata
# ---------------------------------------------------------------------------

def test_pdf_da_sessao_com_o_doi_certo_e_sucesso(tmp_path, monkeypatch):
    monkeypatch.setattr(sessao_navegador, "disponivel", lambda: True)
    baixar = MagicMock(return_value=(_pdf_com(f"Glycobiology article\nhttps://doi.org/{DOI}"), None))
    monkeypatch.setattr(sessao_navegador, "baixar_pdf", baixar)

    r = _cascata(tmp_path, monkeypatch, ["sessao_institucional"])

    assert r["success"] is True
    assert r["source"] == "sessao_institucional"
    assert r["validation_method"] == "doi_in_pdf"
    assert Path(r["file"]).exists()
    baixar.assert_called_once()
    assert baixar.call_args.args == (DOI,)


def test_pdf_da_sessao_de_outro_artigo_e_rejeitado_e_apagado(tmp_path, monkeypatch):
    monkeypatch.setattr(sessao_navegador, "disponivel", lambda: True)
    monkeypatch.setattr(sessao_navegador, "baixar_pdf",
                        lambda doi, prazo: (_pdf_com("Other paper\nhttps://doi.org/10.1007/outro.123"), None))

    r = _cascata(tmp_path, monkeypatch, ["sessao_institucional"])

    assert r["success"] is False
    assert r["error"]["code"] == "article_identity_not_confirmed"
    assert not list(tmp_path.glob("*.pdf"))


def test_sessao_expirada_aparece_nas_tentativas(tmp_path, monkeypatch):
    monkeypatch.setattr(sessao_navegador, "disponivel", lambda: True)
    monkeypatch.setattr(sessao_navegador, "baixar_pdf", lambda doi, prazo: (None, "sessao_expirada"))

    r = _cascata(tmp_path, monkeypatch, ["sessao_institucional"])

    assert r["success"] is False
    assert {"source": "sessao_institucional", "url": None, "reason": "sessao_expirada"} in r["download_attempts"]


def test_sessao_desligada_nao_e_chamada(tmp_path, monkeypatch):
    baixar = MagicMock()
    monkeypatch.setattr(sessao_navegador, "baixar_pdf", baixar)
    _cascata(tmp_path, monkeypatch, ["sessao_institucional"])
    baixar.assert_not_called()


def test_sessao_vem_antes_da_libgen_e_das_rotas_lentas(tmp_path, monkeypatch):
    ordem = []
    monkeypatch.setattr(sessao_navegador, "disponivel", lambda: True)
    monkeypatch.setattr(sessao_navegador, "baixar_pdf", lambda doi, prazo: (ordem.append("sessao"), (None, "x"))[1])
    r = _cascata(
        tmp_path, monkeypatch, ["sessao_institucional", "libgen", "crossref"],
        try_libgen=lambda *a, **k: (ordem.append("libgen"), ([], {}))[1],
        try_crossref_links=lambda *a, **k: (ordem.append("crossref"), ([], {}))[1],
    )
    assert r["success"] is False
    assert ordem == ["sessao", "libgen", "crossref"]


# ---------------------------------------------------------------------------
# EZproxy / proxy configurado = modo institucional para as URLs das editoras
# ---------------------------------------------------------------------------

def test_ezproxy_configurado_liga_as_urls_diretas_das_editoras(tmp_path, monkeypatch):
    direto = MagicMock(return_value=[])
    monkeypatch.setenv("EZPROXY_BASE_URL", "https://ezproxy.pucminas.br")
    _cascata(tmp_path, monkeypatch, ["publisher_direct"], _try_publisher_direct=direto)
    direto.assert_called_once()


def test_sem_acesso_institucional_springer_fechado_nao_gera_url_direta(tmp_path, monkeypatch):
    direto = MagicMock(return_value=[])
    monkeypatch.delenv("PAPER_FETCH_INSTITUTIONAL", raising=False)
    for var in institutional._PROXY_VARS:
        monkeypatch.delenv(var, raising=False)
    _cascata(tmp_path, monkeypatch, ["publisher_direct"], _try_publisher_direct=direto)
    direto.assert_not_called()


# ---------------------------------------------------------------------------
# EZproxy segue o citation_pdf_url da página autenticada
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, url, content, tipo, status=200):
        self.url, self.content, self.status_code = url, content, status
        self.headers = {"content-type": tipo}


class _Sessao:
    def __init__(self, respostas):
        self.respostas, self.pedidos = respostas, []

    def get(self, url, **kw):
        self.pedidos.append((url, kw.get("headers")))
        return self.respostas[url]


def test_ezproxy_segue_o_pdf_declarado_na_pagina(monkeypatch):
    monkeypatch.setenv("EZPROXY_BASE_URL", "https://ezproxy.exemplo.edu")
    alvo = "https://link.springer.com/content/pdf/10.1007/x.pdf"
    entrada = institutional.ezproxy_url(alvo)
    pagina = "https://link-springer-com.ezproxy.exemplo.edu/article/10.1007/x"
    pdf = "https://link-springer-com.ezproxy.exemplo.edu/content/pdf/10.1007/x.pdf"
    html = f'<html><head><meta name="citation_pdf_url" content="{pdf}"></head></html>'.encode()
    sessao = _Sessao({entrada: _Resp(pagina, html, "text/html"), pdf: _Resp(pdf, b"%PDF-1.4 ok", "application/pdf")})
    monkeypatch.setattr(institutional, "get_session", lambda timeout=30: sessao)

    dados, erro = institutional.fetch_pdf(alvo, timeout=5)

    assert (dados, erro) == (b"%PDF-1.4 ok", None)
    assert sessao.pedidos[1] == (pdf, {"Referer": pagina})


def test_ezproxy_sem_link_de_pdf_na_pagina_continua_sendo_falha(monkeypatch):
    monkeypatch.setenv("EZPROXY_BASE_URL", "https://ezproxy.exemplo.edu")
    alvo = "https://link.springer.com/content/pdf/10.1007/x.pdf"
    sessao = _Sessao({institutional.ezproxy_url(alvo): _Resp(alvo, b"<html>login</html>", "text/html")})
    monkeypatch.setattr(institutional, "get_session", lambda timeout=30: sessao)
    assert institutional.fetch_pdf(alvo, timeout=5) == (None, "ezproxy_not_a_pdf")


# ---------------------------------------------------------------------------
# sources vale também para a descoberta expandida
# ---------------------------------------------------------------------------

_EXPANDIDAS = ("try_openaire", "try_hal", "try_zenodo", "try_datacite", "try_doaj", "try_dryad", "try_figshare",
               "try_ntrs", "try_base_search", "try_fatcat", "try_doi_patterns", "try_arxiv_title",
               "try_google_scholar", "try_cyberleninka")


def _expandidas_falsas(monkeypatch):
    mocks = {nome: MagicMock(return_value=([], {}, [])) for nome in _EXPANDIDAS}
    for nome, mock in mocks.items():
        monkeypatch.setattr(fetch, nome, mock)
    return mocks


def test_descoberta_expandida_so_roda_as_fontes_permitidas(tmp_path, monkeypatch):
    mocks = _expandidas_falsas(monkeypatch)
    fetch._fetch_from_expanded_sources(
        DOI, tmp_path, dry_run=False, overwrite=False, timeout=5,
        original_result={"meta": {"title": "A sufficiently specific article title"}}, sources=["hal"],
    )
    assert mocks["try_hal"].call_count == 2  # por DOI e por título
    chamadas = {nome for nome, m in mocks.items() if m.called}
    assert chamadas == {"try_hal"}


def test_sem_filtro_a_descoberta_expandida_roda_todas(tmp_path, monkeypatch):
    mocks = _expandidas_falsas(monkeypatch)
    fetch._fetch_from_expanded_sources(
        DOI, tmp_path, dry_run=False, overwrite=False, timeout=5,
        original_result={"meta": {"title": "A sufficiently specific article title"}},
    )
    assert all(m.called for m in mocks.values())
