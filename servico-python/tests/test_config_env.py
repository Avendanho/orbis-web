import importlib.util
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config_env  # noqa: E402
import extracao  # noqa: E402

MODELO = """# Comentário de topo
# E-mail do Unpaywall
UNPAYWALL_EMAIL=antigo@exemplo.org

# Chave CORE
# CORE_API_KEY=sua_chave

# Sci-Hub (descomente para desligar)
# PAPER_FETCH_NO_SCIHUB=1

OUTRA_COISA=fica
"""


@pytest.fixture
def arquivo(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text(MODELO, encoding="utf-8")
    # setenv + delenv: o monkeypatch passa a restaurar o valor original no fim,
    # mesmo que `gravar` escreva em os.environ durante o teste.
    for k in ("UNPAYWALL_EMAIL", "CORE_API_KEY", "PAPER_FETCH_NO_SCIHUB", "WILEY_TDM_TOKEN", "OUTRA_COISA",
              "ORBIS_SALVAR_IMAGENS", "ORBIS_EXTRACAO_PRAZO"):
        monkeypatch.setenv(k, "x")
        monkeypatch.delenv(k)
    monkeypatch.setattr(config_env, "ARQUIVO", f)
    return f


def test_ler_ignora_comentarios(arquivo):
    assert config_env.ler() == {"UNPAYWALL_EMAIL": "antigo@exemplo.org", "OUTRA_COISA": "fica"}


def test_gravar_substitui_descomenta_e_preserva(arquivo):
    reiniciar = config_env.gravar({"UNPAYWALL_EMAIL": "novo@exemplo.org", "CORE_API_KEY": "core-123"})
    texto = arquivo.read_text(encoding="utf-8")
    assert "UNPAYWALL_EMAIL=novo@exemplo.org" in texto
    assert "antigo@exemplo.org" not in texto
    assert "CORE_API_KEY=core-123" in texto
    assert "# CORE_API_KEY=sua_chave" not in texto, "a linha do modelo vira a linha ativa"
    assert "# Comentário de topo" in texto and "OUTRA_COISA=fica" in texto
    assert texto.index("CORE_API_KEY=") < texto.index("OUTRA_COISA="), "mantém a posição do modelo"
    assert os.environ["UNPAYWALL_EMAIL"] == "novo@exemplo.org", "vale na hora"
    assert sorted(reiniciar) == ["CORE_API_KEY", "UNPAYWALL_EMAIL"]


def test_gravar_liga_e_desliga_fonte(arquivo):
    config_env.gravar({"PAPER_FETCH_NO_SCIHUB": "1"})
    texto = arquivo.read_text(encoding="utf-8")
    assert "PAPER_FETCH_NO_SCIHUB=1" in texto and "# PAPER_FETCH_NO_SCIHUB" not in texto
    assert config_env.gravar({"PAPER_FETCH_NO_SCIHUB": None}) == []
    assert "PAPER_FETCH_NO_SCIHUB" not in arquivo.read_text(encoding="utf-8")
    assert "PAPER_FETCH_NO_SCIHUB" not in os.environ


def test_desligar_opcao_ligada_por_padrao_grava_zero(arquivo):
    config_env.gravar({"ORBIS_SALVAR_IMAGENS": "0", "ORBIS_EXTRACAO_PRAZO": "30"})
    assert arquivo.read_text(encoding="utf-8").rstrip().endswith("ORBIS_SALVAR_IMAGENS=0\nORBIS_EXTRACAO_PRAZO=30")
    o = extracao.opcoes()
    assert o["imagens"] is False and o["prazo"] == 30, "a extração já usa o valor novo, sem reiniciar"


def test_valor_com_espaco_ou_cerquilha_vai_entre_aspas(arquivo):
    config_env.gravar({"WILEY_TDM_TOKEN": 'se#nha com "aspas"'})
    assert config_env.ler()["WILEY_TDM_TOKEN"] == 'se#nha com "aspas"'


def test_linha_com_export_e_substituida(arquivo):
    arquivo.write_text("export CORE_API_KEY=velha\n", encoding="utf-8")
    config_env.gravar({"CORE_API_KEY": "nova-chave"})
    assert arquivo.read_text(encoding="utf-8") == "CORE_API_KEY=nova-chave\n"


def test_recusa_quebra_de_linha_e_chave_desconhecida(arquivo):
    antes = arquivo.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="quebra de linha"):
        config_env.gravar({"UNPAYWALL_EMAIL": "a@b.co\nHTTP_PROXY=http://mal"})
    with pytest.raises(ValueError, match="não configurável"):
        config_env.gravar({"ORBIS_DATA_DIR": "/tmp"})
    with pytest.raises(ValueError, match="não configurável"):
        config_env.gravar({"PAPER_FETCH_PROXY": "http://x"}), "acesso institucional fica só no .env"
    assert arquivo.read_text(encoding="utf-8") == antes, "nada gravado"


def test_visao_mascara_segredo(arquivo, monkeypatch):
    monkeypatch.setenv("CORE_API_KEY", "abcdefghijklmnop")
    monkeypatch.setenv("UNPAYWALL_EMAIL", "a@b.co")
    v = config_env.visao()
    assert v["CORE_API_KEY"] == {"preenchido": True, "valor": "••••mnop"}
    assert v["UNPAYWALL_EMAIL"] == {"preenchido": True, "valor": "a@b.co"}
    assert v["WILEY_TDM_TOKEN"] == {"preenchido": False, "valor": ""}


# --- token --------------------------------------------------------------------
def _start():
    spec = importlib.util.spec_from_file_location("start_orbis", Path(__file__).resolve().parents[2] / "start.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_start_cria_o_token_uma_vez(tmp_path):
    start = _start()
    t1 = start.token_do_motor(tmp_path)
    t2 = start.token_do_motor(tmp_path)
    assert t1 == t2 and len(t1) >= 32, "a segunda execução reaproveita o mesmo token"
    arq = tmp_path / "data" / "orbis-token"
    assert arq.read_text(encoding="utf-8").strip() == t1
    if os.name != "nt":
        assert stat.S_IMODE(arq.stat().st_mode) == 0o600


def test_motor_le_o_token_do_ambiente_ou_do_arquivo(tmp_path, monkeypatch):
    import main
    monkeypatch.delenv("ORBIS_ENGINE_TOKEN", raising=False)
    assert main.ler_token(tmp_path) == "", "sem arquivo e sem variável: sem token"
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "orbis-token").write_text("do-arquivo\n", encoding="utf-8")
    assert main.ler_token(tmp_path) == "do-arquivo", "motor iniciado à mão com o arquivo presente"
    monkeypatch.setenv("ORBIS_ENGINE_TOKEN", "da-variavel")
    assert main.ler_token(tmp_path) == "da-variavel"


# --- rotas --------------------------------------------------------------------
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402


@pytest.fixture
def cliente(arquivo, monkeypatch):
    monkeypatch.setattr(main, "TOKEN", "tok")
    return TestClient(main.app)


def test_rota_exige_token(cliente, monkeypatch):
    assert cliente.get("/config").status_code == 403
    assert cliente.get("/config", headers={"X-Orbis-Token": "errado"}).status_code == 403
    monkeypatch.setattr(main, "TOKEN", "")
    r = cliente.get("/config", headers={"X-Orbis-Token": ""})
    assert r.status_code == 403 and "start.py" in r.json()["detail"], "motor iniciado sem token"


def test_rota_grava(cliente, arquivo):
    h = {"X-Orbis-Token": "tok"}
    r = cliente.put("/config", json={"mudancas": {"CORE_API_KEY": "core-abcdefghijk"}}, headers=h)
    assert r.status_code == 200
    assert r.json()["reiniciar"] == ["CORE_API_KEY"]
    assert r.json()["itens"]["CORE_API_KEY"]["valor"] == "••••hijk"
    assert "core-abcdefghijk" not in r.text
    assert cliente.put("/config", json={"mudancas": {"X": "1"}}, headers=h).status_code == 400
    assert cliente.get("/config", headers=h).json()["itens"]["CORE_API_KEY"]["preenchido"] is True
