import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main  # noqa: E402


@pytest.fixture(autouse=True)
def ambiente_restaurado():
    with patch.dict(os.environ):
        yield


def _motor(tmp_path, exemplo: str, env: str) -> Path:
    (tmp_path / ".env.example").write_text(exemplo, encoding="utf-8")
    (tmp_path / ".env").write_text(env, encoding="utf-8")
    return tmp_path


def test_carrega_chaves_preenchidas_e_ignora_as_do_modelo(tmp_path, monkeypatch):
    for k in ("UNPAYWALL_EMAIL", "ELSEVIER_API_KEY", "CORE_API_KEY", "VAZIA"):
        monkeypatch.delenv(k, raising=False)
    raiz = _motor(
        tmp_path,
        "UNPAYWALL_EMAIL=seuemail@exemplo.com\nELSEVIER_API_KEY=sua_chave_elsevier\nCORE_API_KEY=sua_chave_core\n",
        "# comentário\nUNPAYWALL_EMAIL=pesquisa@funed.mg.gov.br\nELSEVIER_API_KEY=sua_chave_elsevier\n"
        "export CORE_API_KEY=\"abc123\"\nVAZIA=\n",
    )
    main.carregar_env_do_motor(raiz)
    assert os.environ["UNPAYWALL_EMAIL"] == "pesquisa@funed.mg.gov.br"
    assert os.environ["CORE_API_KEY"] == "abc123"
    assert "ELSEVIER_API_KEY" not in os.environ, "valor do modelo não é chave de verdade"
    assert "VAZIA" not in os.environ


def test_ambiente_do_terminal_prevalece(tmp_path, monkeypatch):
    monkeypatch.setenv("CORE_API_KEY", "do-terminal")
    main.carregar_env_do_motor(_motor(tmp_path, "", "CORE_API_KEY=do-arquivo\n"))
    assert os.environ["CORE_API_KEY"] == "do-terminal"


def test_sem_arquivo_nao_faz_nada(tmp_path):
    main.carregar_env_do_motor(tmp_path)
