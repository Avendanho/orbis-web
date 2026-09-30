"""Isola os testes do ambiente de quem os roda e uns dos outros.

Com ``ORBIS_SESSAO_NAVEGADOR=1`` exportado no terminal, qualquer teste que
passe pelo ``fetch`` abriria o navegador de verdade com a sessão institucional.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _sem_sessao_institucional(monkeypatch):
    for var in ("ORBIS_SESSAO_NAVEGADOR", "ORBIS_SESSAO_EZPROXY", "EZPROXY_BASE_URL", "ORBIS_PERFIL_NAVEGADOR"):
        monkeypatch.delenv(var, raising=False)
    import http_retry
    import sessao_navegador
    import sources_pubmed

    sessao_navegador.reiniciar_estado()
    # Estado de processo que um teste deixaria para o seguinte: respostas do
    # conversor de IDs em cache e o ritmo/cooldown por host.
    sources_pubmed._IDCONV_CACHE.clear()
    http_retry.reset()
    yield
    sessao_navegador.reiniciar_estado()
    sources_pubmed._IDCONV_CACHE.clear()
    http_retry.reset()
