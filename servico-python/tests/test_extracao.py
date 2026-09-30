import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import extracao  # noqa: E402

pymupdf = pytest.importorskip("pymupdf")
pytest.importorskip("pymupdf4llm")

TITULO = "Efeito do exercicio na dor lombar"


def pdf(tmp_path, com_imagem=True) -> Path:
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((72, 72), TITULO, fontsize=20)
    p.insert_text((72, 130), "Texto do corpo do artigo com varias palavras.", fontsize=11)
    if com_imagem:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 120, 80), 0)
        pix.clear_with(200)
        p.insert_image(pymupdf.Rect(72, 160, 272, 300), pixmap=pix)
    caminho = tmp_path / "artigo.pdf"
    doc.save(caminho)
    return caminho


def test_markdown_com_imagens_e_links_relativos(tmp_path):
    caminho = pdf(tmp_path)
    r = extracao.extrair(caminho, tmp_path / "artigo_imagens")
    assert r["formato"] == "markdown"
    assert TITULO in r["texto"] and r["paginas"] == 1
    assert r["imagens"] == 1
    imagens = list((tmp_path / "artigo_imagens").iterdir())
    assert len(imagens) == 1
    assert f"](artigo_imagens/{imagens[0].name})" in r["texto"], "link relativo à pasta do .md"
    assert "aviso" not in r


def test_sem_pasta_de_imagens_nada_fica_no_disco(tmp_path):
    caminho = pdf(tmp_path)
    antes = set(tmp_path.iterdir())
    r = extracao.extrair(caminho, None)
    assert r["formato"] == "markdown" and TITULO in r["texto"]
    assert r["imagens"] == 0
    assert set(tmp_path.iterdir()) == antes


def test_pdf_sem_imagem_nao_deixa_pasta_vazia(tmp_path):
    r = extracao.extrair(pdf(tmp_path, com_imagem=False), tmp_path / "artigo_imagens")
    assert r["imagens"] == 0
    assert not (tmp_path / "artigo_imagens").exists()


def test_falha_do_markdown_cai_para_texto_simples(tmp_path):
    def quebra(*_a, **_k):
        raise RuntimeError("pymupdf4llm quebrou")
    r = extracao.extrair(pdf(tmp_path), tmp_path / "artigo_imagens", rodar=quebra)
    assert r["formato"] == "texto"
    assert TITULO in r["texto"] and r["paginas"] == 1
    assert r["aviso"] == "Falha na extração em Markdown; foi usado o texto simples."
    assert not (tmp_path / "artigo_imagens").exists()


def test_prazo_estourado_mata_o_filho_e_cai_para_texto_simples(tmp_path):
    imagens = tmp_path / "artigo_imagens"
    dorme = [sys.executable, "-c", "import os,time;os.makedirs('artigo_imagens');time.sleep(30)"]

    def lento(caminho, pasta, prazo):
        return extracao.markdown_no_filho(caminho, pasta, prazo, comando=dorme)
    r = extracao.extrair(pdf(tmp_path), imagens, prazo=1, rodar=lento)
    assert r["formato"] == "texto" and TITULO in r["texto"]
    assert r["aviso"] == "A extração em Markdown passou de 1 s; foi usado o texto simples."
    assert not imagens.exists(), "pasta de imagens pela metade é apagada"


def test_markdown_desligado_nem_chama_o_filho(tmp_path):
    def nao_chame(*_a, **_k):
        raise AssertionError("não devia rodar")
    r = extracao.extrair(pdf(tmp_path), tmp_path / "artigo_imagens", markdown=False, rodar=nao_chame)
    assert r["formato"] == "texto" and TITULO in r["texto"] and "aviso" not in r
    assert not (tmp_path / "artigo_imagens").exists()


def test_opcoes_do_ambiente(monkeypatch):
    for k in ("ORBIS_EXTRAIR_MARKDOWN", "ORBIS_SALVAR_IMAGENS", "ORBIS_EXTRACAO_PRAZO"):
        monkeypatch.delenv(k, raising=False)
    assert extracao.opcoes() == {"markdown": True, "imagens": True, "prazo": 90}
    monkeypatch.setenv("ORBIS_EXTRAIR_MARKDOWN", "0")
    monkeypatch.setenv("ORBIS_SALVAR_IMAGENS", "")
    monkeypatch.setenv("ORBIS_EXTRACAO_PRAZO", "9999")
    assert extracao.opcoes() == {"markdown": False, "imagens": False, "prazo": 600}
    monkeypatch.setenv("ORBIS_EXTRACAO_PRAZO", "abc")
    assert extracao.opcoes()["prazo"] == 90


def test_conforme_opcoes_sem_imagens(tmp_path, monkeypatch):
    monkeypatch.setenv("ORBIS_SALVAR_IMAGENS", "0")
    monkeypatch.delenv("ORBIS_EXTRAIR_MARKDOWN", raising=False)
    r = extracao.extrair_conforme_opcoes(pdf(tmp_path), tmp_path / "artigo_imagens")
    assert r["formato"] == "markdown" and r["imagens"] == 0
    assert not (tmp_path / "artigo_imagens").exists()
