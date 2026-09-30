"""Ritmo por host das APIs de metadados e carga do motor/.env pela CLI."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ambiente  # noqa: E402
import fetch  # noqa: E402
import http_retry  # noqa: E402


# ---------------------------------------------------------------------------
# http_retry.pace
# ---------------------------------------------------------------------------

def _relogio_parado(monkeypatch):
    """Registra as esperas sem dormir; o relógio não anda entre chamadas."""
    esperas: list[float] = []
    monkeypatch.setattr(http_retry.time, "sleep", esperas.append)
    monkeypatch.setattr(http_retry.time, "monotonic", lambda: 1000.0)
    http_retry.reset()
    return esperas


def test_pedidos_ao_mesmo_host_de_api_sao_espacados(monkeypatch):
    esperas = _relogio_parado(monkeypatch)
    monkeypatch.setitem(http_retry.MIN_INTERVAL, "api.openalex.org", 0.5)
    for _ in range(3):
        http_retry.pace("https://api.openalex.org/works?filter=doi:x")
    assert esperas == [0.5, 1.0]  # o primeiro sai na hora


def test_hosts_do_ncbi_dividem_um_so_limite(monkeypatch):
    esperas = _relogio_parado(monkeypatch)
    monkeypatch.setenv("NCBI_API_KEY", "")
    http_retry.pace("https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/?ids=x")
    http_retry.pace("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?term=x")
    assert esperas == pytest.approx([0.4])  # 3 req/s por IP, somando os dois


def test_sem_chave_a_openalex_vai_mais_devagar(monkeypatch):
    esperas = _relogio_parado(monkeypatch)
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    http_retry.pace("https://api.openalex.org/a")
    http_retry.pace("https://api.openalex.org/b")
    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    http_retry.reset()
    http_retry.pace("https://api.openalex.org/a")
    http_retry.pace("https://api.openalex.org/b")
    assert esperas == pytest.approx([0.5, 0.12])


def test_hosts_diferentes_nao_esperam_um_pelo_outro(monkeypatch):
    esperas = _relogio_parado(monkeypatch)
    http_retry.pace("https://api.openalex.org/a")
    http_retry.pace("https://api.crossref.org/b")
    assert esperas == []


def test_host_sem_limite_conhecido_nao_espera(monkeypatch):
    esperas = _relogio_parado(monkeypatch)
    for _ in range(3):
        assert http_retry.pace("https://repositorio.exemplo.br/x.pdf") == 0.0
    assert esperas == []


def test_espera_nunca_passa_do_prazo_do_artigo(monkeypatch):
    esperas = _relogio_parado(monkeypatch)
    monkeypatch.setitem(http_retry.MIN_INTERVAL, "api.semanticscholar.org", 5.0)
    http_retry.pace("https://api.semanticscholar.org/a")
    http_retry.pace("https://api.semanticscholar.org/b", max_wait=0.3)
    assert esperas == [0.3]


def test_get_passa_pelo_ritmo_antes_de_cada_tentativa(monkeypatch):
    chamados = []
    monkeypatch.setattr(http_retry, "pace", lambda url, max_wait=None: chamados.append(url) or 0.0)

    class _R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(fetch.urllib.request, "urlopen", lambda req, timeout, context=None: _R(b"{}"))
    http_retry.reset()
    assert fetch._get("https://api.openalex.org/works/x", timeout=5) == b"{}"
    assert chamados == ["https://api.openalex.org/works/x"]


# ---------------------------------------------------------------------------
# ambiente.carregar
# ---------------------------------------------------------------------------

def test_env_aplica_chaves_reais_e_ignora_placeholders(monkeypatch, tmp_path):
    (tmp_path / ".env.example").write_text(
        "UNPAYWALL_EMAIL=seuemail@exemplo.com\nCORE_API_KEY=sua_chave_core\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "# comentário\nUNPAYWALL_EMAIL=seuemail@exemplo.com\nCORE_API_KEY=abc123\n"
        "export OPENALEX_API_KEY='xyz'\nVAZIA=\n", encoding="utf-8")
    for var in ("UNPAYWALL_EMAIL", "CORE_API_KEY", "OPENALEX_API_KEY", "VAZIA"):
        monkeypatch.delenv(var, raising=False)

    relato = ambiente.carregar(tmp_path)

    import os

    assert os.environ.get("CORE_API_KEY") == "abc123"
    assert os.environ.get("OPENALEX_API_KEY") == "xyz"
    assert "UNPAYWALL_EMAIL" not in os.environ
    assert relato == {"aplicadas": ["CORE_API_KEY", "OPENALEX_API_KEY"], "modelo": ["UNPAYWALL_EMAIL"]}


def test_ambiente_do_terminal_prevalece_sobre_o_arquivo(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("CORE_API_KEY=do_arquivo\n", encoding="utf-8")
    monkeypatch.setenv("CORE_API_KEY", "do_terminal")
    ambiente.carregar(tmp_path)
    import os

    assert os.environ["CORE_API_KEY"] == "do_terminal"


def test_sem_arquivos_nada_acontece(tmp_path):
    assert ambiente.carregar(tmp_path) == {"aplicadas": [], "modelo": []}


# ---------------------------------------------------------------------------
# Conversor de IDs do NCBI: uma consulta por DOI, e falha não vira resposta
# ---------------------------------------------------------------------------

def test_idconv_consulta_uma_vez_por_doi(monkeypatch):
    import sources_pubmed

    chamadas = []

    def falso(url, *, timeout):
        chamadas.append(url)
        return {"records": [{"doi": "10.1186/scrt399", "pmcid": "PMC3", "pmid": "9"}]}

    monkeypatch.setattr(fetch, "_get_json", falso)
    assert sources_pubmed.try_pmc_idconv("10.1186/scrt399", timeout=5) == {"pmcid": "PMC3", "pmid": "9"}
    assert sources_pubmed.try_pmc_idconv("10.1186/SCRT399", timeout=5) == {"pmcid": "PMC3", "pmid": "9"}
    assert len(chamadas) == 1


def test_idconv_que_falhou_pergunta_de_novo(monkeypatch):
    import sources_pubmed

    respostas = [TimeoutError("429"), {"records": [{"pmcid": "PMC3"}]}]

    def falso(url, *, timeout):
        r = respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(fetch, "_get_json", falso)
    assert sources_pubmed.try_pmc_idconv("10.1186/scrt399", timeout=5) == {}
    assert sources_pubmed.try_pmc_idconv("10.1186/scrt399", timeout=5) == {"pmcid": "PMC3"}
