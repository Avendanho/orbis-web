"""Sessão institucional no navegador (CAPES/CAFe ou EZproxy), sem navegador real.

O Playwright é trocado por objetos falsos com a mesma forma: página, contexto
e o cliente de requisições do contexto. O que se testa é a decisão — onde
entrar, que PDF aceitar, quando dar a sessão por expirada — e a fila da
thread dona do navegador.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import runtime  # noqa: E402
import sessao_navegador as sn  # noqa: E402

PDF = b"%PDF-1.7\n" + b"0" * 2048 + b"\n%%EOF\n"


class _Resposta:
    def __init__(self, url, corpo=b"", tipo="text/html", status=200):
        self.url, self._corpo, self.status = url, corpo, status
        self.headers = {"content-type": tipo}

    def body(self):
        return self._corpo


class _Localizador:
    def __init__(self, n):
        self._n = n

    def count(self):
        return self._n


class _Pagina:
    """Página falsa: ``rotas`` diz o que cada URL devolve ao ser aberta."""

    def __init__(self, rotas, visitas):
        self.rotas, self.visitas, self.url = rotas, visitas, "about:blank"
        self._html, self._senhas, self._senha_visivel, self._ouvintes = "", 0, 0, {}

    def on(self, evento, funcao):
        self._ouvintes.setdefault(evento, []).append(funcao)

    def goto(self, url, **_):
        self.visitas.append(url)
        rota = self.rotas.get(url, {"html": "<html></html>"})
        self.url = rota.get("final", url)
        self._html = rota.get("html", "")
        self._senha_visivel = rota.get("senha_visivel", 0)
        self._senhas = rota.get("senhas", self._senha_visivel)
        if "pdf" in rota:
            for f in self._ouvintes.get("response", []):
                f(_Resposta(self.url, rota["pdf"], "application/pdf"))

    def wait_for_load_state(self, *_, **__):
        pass

    def content(self):
        return self._html

    def locator(self, seletor):
        return _Localizador(self._senha_visivel if ":visible" in seletor else self._senhas)

    def close(self):
        pass


class _Requisicoes:
    def __init__(self, arquivos):
        self.arquivos, self.pedidos = arquivos, []

    def get(self, url, **_):
        self.pedidos.append(url)
        corpo = self.arquivos.get(url)
        return _Resposta(url, corpo or b"<html>", "application/pdf" if corpo else "text/html", 200)


class _Contexto:
    def __init__(self, rotas=None, arquivos=None):
        self.visitas: list[str] = []
        self.rotas = rotas or {}
        self.request = _Requisicoes(arquivos or {})

    def new_page(self):
        return _Pagina(self.rotas, self.visitas)

    def close(self):
        pass


def _limite(segundos=30):
    return time.monotonic() + segundos


# ---------------------------------------------------------------------------
# Configuração e endereço de entrada
# ---------------------------------------------------------------------------

def test_sem_ezproxy_a_entrada_e_o_proprio_alvo():
    assert sn.url_entrada("https://link.springer.com/article/10.1007/x") == "https://link.springer.com/article/10.1007/x"


def test_com_ezproxy_da_capes_a_entrada_passa_pelo_login_do_proxy(monkeypatch):
    monkeypatch.setenv("ORBIS_SESSAO_EZPROXY", "https://ez27.periodicos.capes.gov.br/")
    assert sn.url_entrada("https://doi.org/10.1/x") == (
        "https://ez27.periodicos.capes.gov.br/login?url=https%3A%2F%2Fdoi.org%2F10.1%2Fx")


def test_ezproxy_da_biblioteca_vale_quando_nao_ha_um_proprio_da_sessao(monkeypatch):
    monkeypatch.setenv("EZPROXY_BASE_URL", "https://ezproxy.pucminas.br")
    assert sn.ezproxy_base() == "https://ezproxy.pucminas.br"


def test_disponivel_exige_opt_in_e_login_feito(monkeypatch, tmp_path):
    monkeypatch.setenv("ORBIS_PERFIL_NAVEGADOR", str(tmp_path / "perfil"))
    assert not sn.disponivel()
    monkeypatch.setenv("ORBIS_SESSAO_NAVEGADOR", "1")
    assert not sn.disponivel(), "sem login feito o perfil não tem a pasta Default"
    (tmp_path / "perfil" / "Default").mkdir(parents=True)
    assert sn.disponivel()


def test_sessao_expirada_ou_teto_atingido_desligam_a_rota(monkeypatch, tmp_path):
    monkeypatch.setenv("ORBIS_SESSAO_NAVEGADOR", "1")
    monkeypatch.setenv("ORBIS_PERFIL_NAVEGADOR", str(tmp_path))
    (tmp_path / "Default").mkdir()
    sn._estado["expirada"] = True
    assert not sn.disponivel()
    sn.reiniciar_estado()
    monkeypatch.setenv("ORBIS_SESSAO_MAX_ARTIGOS", "2")
    sn._estado["artigos"] = 2
    assert not sn.disponivel()


def test_resolver_doi_usa_a_api_de_handles(monkeypatch):
    pedidos = []

    def falso(url, *, timeout):
        pedidos.append(url)
        return {"values": [{"type": "HS_ADMIN", "data": {}},
                           {"type": "URL", "data": {"value": "https://link.springer.com/article/10.1007/x"}}]}

    monkeypatch.setattr(runtime, "_get_json", falso, raising=False)
    import fetch

    monkeypatch.setattr(fetch, "_get_json", falso)
    assert sn.resolver_doi("10.1007/x") == "https://link.springer.com/article/10.1007/x"
    assert pedidos[0].startswith("https://doi.org/api/handles/10.1007/x")


def test_resolver_doi_devolve_none_quando_a_api_falha(monkeypatch):
    import fetch

    def quebra(url, *, timeout):
        raise TimeoutError("x")

    monkeypatch.setattr(fetch, "_get_json", quebra)
    assert sn.resolver_doi("10.1007/x") is None


# ---------------------------------------------------------------------------
# O trabalho dentro do navegador
# ---------------------------------------------------------------------------

def test_segue_o_citation_pdf_url_com_os_cookies_da_sessao():
    pagina = "https://link.springer.com/article/10.1007/x"
    pdf = "https://link.springer.com/content/pdf/10.1007/x.pdf"
    ctx = _Contexto(
        rotas={pagina: {"html": f'<html><head><meta name="citation_pdf_url" content="{pdf}"></head></html>'}},
        arquivos={pdf: PDF},
    )
    dados, erro = sn._baixar_no_navegador(ctx, pagina, "10.1007/x", _limite())
    assert (dados, erro) == (PDF, None)
    assert ctx.request.pedidos == [pdf]


def test_pdf_servido_na_propria_navegacao_e_aceito():
    alvo = "https://www.nature.com/articles/x.pdf"
    ctx = _Contexto(rotas={alvo: {"pdf": PDF}})
    assert sn._baixar_no_navegador(ctx, alvo, None, _limite()) == (PDF, None)


def test_tela_de_login_do_cafe_e_sessao_expirada(monkeypatch):
    monkeypatch.setenv("ORBIS_SESSAO_EZPROXY", "https://ez27.periodicos.capes.gov.br")
    alvo = "https://www.sciencedirect.com/science/article/pii/S1"
    entrada = sn.url_entrada(alvo)
    ctx = _Contexto(rotas={entrada: {"final": "https://idp.ufmg.br/idp/profile/SAML2/Redirect/SSO",
                                     "html": "<form><input type=password></form>", "senha_visivel": 1}})
    assert sn._baixar_no_navegador(ctx, alvo, None, _limite()) == (None, "sessao_expirada")
    assert ctx.visitas == [entrada]


def test_formulario_de_login_escondido_na_pagina_da_editora_nao_expira_a_sessao():
    # Wiley e outras trazem um login escondido no cabeçalho de toda página.
    alvo = "https://onlinelibrary.wiley.com/doi/10.1111/x"
    html = ('<html><body><div style="display:none"><input type="password"></div>'
            "<a>Access through your institution</a></body></html>")
    ctx = _Contexto(rotas={alvo: {"html": html, "senhas": 1, "senha_visivel": 0}})
    assert sn._baixar_no_navegador(ctx, alvo, "10.1111/x", _limite()) == (None, "sem_acesso_institucional")


def test_link_que_devolve_html_nao_e_aceito_como_pdf():
    pagina = "https://example.org/artigo"
    pdf = "https://example.org/artigo.pdf"
    ctx = _Contexto(rotas={pagina: {"html": f'<meta name="citation_pdf_url" content="{pdf}">'}})
    assert sn._baixar_no_navegador(ctx, pagina, None, _limite()) == (None, "pdf_nao_entregue")


# ---------------------------------------------------------------------------
# A thread dona do navegador e o que o chamador vê
# ---------------------------------------------------------------------------

@pytest.fixture
def sessao_falsa(monkeypatch, tmp_path):
    """Sessão habilitada, com login feito e um navegador falso."""
    monkeypatch.setenv("ORBIS_SESSAO_NAVEGADOR", "1")
    monkeypatch.setenv("ORBIS_PERFIL_NAVEGADOR", str(tmp_path))
    monkeypatch.setenv("ORBIS_SESSAO_INTERVALO", "0")
    (tmp_path / "Default").mkdir()
    ctx = _Contexto()
    monkeypatch.setattr(sn, "_iniciar_playwright", lambda: None)
    monkeypatch.setattr(sn, "_abrir_contexto", lambda pw: ctx)
    monkeypatch.setattr(sn, "resolver_doi", lambda doi, timeout=10: f"https://editora.example/{doi}")
    yield ctx
    sn.encerrar()


def test_baixar_pdf_passa_pela_thread_do_navegador(sessao_falsa):
    sessao_falsa.rotas["https://editora.example/10.1/x"] = {"pdf": PDF}
    assert sn.baixar_pdf("10.1/x", prazo=20) == (PDF, None)
    assert sn._estado["artigos"] == 1


def test_sessao_expirada_desliga_a_rota_para_o_resto_da_execucao(sessao_falsa):
    sessao_falsa.rotas["https://editora.example/10.1/x"] = {
        "final": "https://ds.cafe.rnp.br/WAYF", "html": "<html></html>"}
    assert sn.baixar_pdf("10.1/x", prazo=20) == (None, "sessao_expirada")
    assert sn._estado["expirada"] is True
    assert not sn.disponivel()
    assert sn.baixar_pdf("10.1/y", prazo=20) == (None, "sessao_indisponivel")


def test_teto_por_execucao_e_respeitado(sessao_falsa, monkeypatch):
    monkeypatch.setenv("ORBIS_SESSAO_MAX_ARTIGOS", "1")
    sessao_falsa.rotas["https://editora.example/10.1/x"] = {"pdf": PDF}
    assert sn.baixar_pdf("10.1/x", prazo=20)[0] == PDF
    assert sn.baixar_pdf("10.1/y", prazo=20) == (None, "sessao_indisponivel")
    assert len(sessao_falsa.visitas) == 1


def test_pedido_cujo_prazo_acaba_na_fila_nao_abre_pagina(sessao_falsa):
    assert sn.baixar_pdf("10.1/x", prazo=1) == (None, "prazo_esgotado_na_fila")
    assert sessao_falsa.visitas == []


def test_navegador_que_nao_abre_vira_rota_indisponivel(monkeypatch, tmp_path):
    monkeypatch.setenv("ORBIS_SESSAO_NAVEGADOR", "1")
    monkeypatch.setenv("ORBIS_PERFIL_NAVEGADOR", str(tmp_path))
    (tmp_path / "Default").mkdir()
    monkeypatch.setattr(sn, "_iniciar_playwright", lambda: None)

    def perfil_ocupado(pw):
        raise RuntimeError("The browser is already in use by another process (ProcessSingleton)")

    monkeypatch.setattr(sn, "_abrir_contexto", perfil_ocupado)
    monkeypatch.setattr(sn, "resolver_doi", lambda doi, timeout=10: None)
    try:
        inicio = time.monotonic()
        assert sn.baixar_pdf("10.1/x", prazo=10) == (None, "navegador_indisponivel")
        assert time.monotonic() - inicio < 5, "o pedido não pode ficar esperando o prazo inteiro"
        assert "feche a janela do login" in sn._estado["indisponivel"]
        assert not sn.disponivel()
    finally:
        sn.encerrar()


def _gravar_cookies(perfil: Path) -> None:
    arquivo = perfil / "Default" / "Network" / "Cookies"
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_bytes(b"sqlite")
    futuro = time.time() + 5
    import os

    os.utime(arquivo, (futuro, futuro))


def test_login_renovado_religa_a_rota_sem_reiniciar(sessao_falsa, tmp_path):
    sessao_falsa.rotas["https://editora.example/10.1/x"] = {"final": "https://ds.cafe.rnp.br/WAYF"}
    assert sn.baixar_pdf("10.1/x", prazo=20) == (None, "sessao_expirada")
    assert not sn.disponivel()

    _gravar_cookies(tmp_path)  # o usuário rodou `login` de novo
    sessao_falsa.rotas["https://editora.example/10.1/y"] = {"pdf": PDF}
    assert sn.disponivel()
    assert sn.baixar_pdf("10.1/y", prazo=20) == (PDF, None)


def test_perfil_ocupado_volta_a_funcionar_depois_do_login(monkeypatch, tmp_path):
    monkeypatch.setenv("ORBIS_SESSAO_NAVEGADOR", "1")
    monkeypatch.setenv("ORBIS_PERFIL_NAVEGADOR", str(tmp_path))
    monkeypatch.setenv("ORBIS_SESSAO_INTERVALO", "0")
    (tmp_path / "Default").mkdir()
    ctx = _Contexto(rotas={"https://editora.example/10.1/x": {"pdf": PDF}})
    tentativas = []

    def abrir(pw):
        tentativas.append(1)
        if len(tentativas) == 1:
            raise RuntimeError("ProcessSingleton: profile already in use")
        return ctx

    monkeypatch.setattr(sn, "_iniciar_playwright", lambda: None)
    monkeypatch.setattr(sn, "_abrir_contexto", abrir)
    monkeypatch.setattr(sn, "resolver_doi", lambda doi, timeout=10: f"https://editora.example/{doi}")
    try:
        assert sn.baixar_pdf("10.1/x", prazo=10) == (None, "navegador_indisponivel")
        _gravar_cookies(tmp_path)
        assert sn.baixar_pdf("10.1/x", prazo=10) == (PDF, None)
        assert len(tentativas) == 2
    finally:
        sn.encerrar()


def test_pedido_que_expira_na_fila_nao_gasta_o_teto(sessao_falsa):
    assert sn.baixar_pdf("10.1/x", prazo=1) == (None, "prazo_esgotado_na_fila")
    assert sn._estado["artigos"] == 0
