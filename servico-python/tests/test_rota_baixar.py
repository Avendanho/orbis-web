import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from test_baixar import FetchFalso, IdentidadeFalsa  # noqa: E402


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    modulos = {"fetch": FetchFalso(), "identity": IdentidadeFalsa()}
    monkeypatch.setattr(main, "_importar", lambda nome: modulos[nome])
    monkeypatch.setattr(main, "PASTA_PDFS", tmp_path / "pdfs")
    return TestClient(main.app)


def test_baixar_ok(cliente, tmp_path, monkeypatch):
    monkeypatch.setattr(main.motor_baixar, "extrair_texto", lambda dados: ("texto", 1))
    r = cliente.post("/baixar", json={"doi": "10.1/a", "projeto": "p1", "modo": "baixar", "titulo": "T"})
    assert r.status_code == 200, r.text
    assert r.json()["arquivo"] == "Silva_2021_T.pdf"
    assert (tmp_path / "pdfs" / "p1" / "Silva_2021_T.pdf").exists()


def test_baixar_projeto_invalido(cliente):
    r = cliente.post("/baixar", json={"doi": "10.1/a", "projeto": "../x", "modo": "baixar"})
    assert r.status_code == 400


def test_baixar_disco(cliente, tmp_path, monkeypatch):
    (tmp_path / "ocupado").write_text("arquivo")
    monkeypatch.setattr(main, "PASTA_PDFS", tmp_path / "ocupado")
    r = cliente.post("/baixar", json={"doi": "10.1/a", "projeto": "p1", "modo": "baixar"})
    assert r.status_code == 507
    assert "Não foi possível usar a pasta" in r.json()["detail"]


def test_saude_informa_pasta(cliente, tmp_path):
    d = cliente.get("/saude").json()
    assert d["pasta_pdfs"] == str((tmp_path / "pdfs").resolve())
