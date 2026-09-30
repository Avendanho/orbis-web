"""Material suplementar nunca é candidato a PDF do artigo.

Caso real (Springer, 10.1007/s00253-018-9525-0): o /content/pdf/ redireciona
clientes automáticos para a página do artigo, cujo citation_pdf_url aponta de
volta para a mesma URL. Pulada a já visitada, o próximo link era o
``MOESM1_ESM.pdf`` — baixado como se fosse o artigo, rejeitado depois pela
identidade, e o candidato gasto.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pdf_links import extract_pdf_links, is_supplementary_url  # noqa: E402

PAGINA = "https://link.springer.com/article/10.1007/s00253-018-9525-0"


def test_pagina_da_springer_nao_oferece_o_esm_como_candidato():
    html = f"""
    <meta name="citation_pdf_url" content="https://link.springer.com/content/pdf/10.1007/s00253-018-9525-0.pdf">
    <a href="https://media.springernature.com/original/springer-static/esm/art%3A10.1007%2Fs00253-018-9525-0/MediaObjects/253_2018_9525_MOESM1_ESM.pdf">ESM 1</a>
    """
    assert extract_pdf_links(html, PAGINA, doi="10.1007/s00253-018-9525-0") == [
        "https://link.springer.com/content/pdf/10.1007/s00253-018-9525-0.pdf"]


def test_padroes_de_suplemento_das_editoras():
    for url in (
        "https://ars.els-cdn.com/content/image/1-s2.0-S0142961214011223-mmc1.pdf",
        "https://onlinelibrary.wiley.com/action/downloadSupplement?doi=10.1111%2Fx&file=bjd14578-sup-0001-FigS1.pdf",
        "https://www.tandfonline.com/doi/suppl/10.1080/x/suppl_file/tbio_a_1_sm1234.pdf",
        "https://pubs.acs.org/doi/suppl/10.1021/acsbiomaterials.1c01101/suppl_file/ab1c01101_si_001.pdf",
        "https://static-content.springer.com/esm/art%3A10.1038%2Fs41467-020-1/MediaObjects/41467_2020_1_MOESM1_ESM.pdf",
        "https://journals.plos.org/plosone/article/file?type=supplementary&id=10.1371/journal.pone.0115069.s001",
    ):
        assert is_supplementary_url(url), url


def test_titulo_com_palavra_parecida_nao_e_descartado():
    for url in (
        "https://repositorio.ufmg.br/bitstream/1843/123/1/Supplemental%20oxygen%20in%20preterm%20infants.pdf",
        "https://eprints.exemplo.ac.uk/55/vitamin-d-supplementation-trial.pdf",
        "https://link.springer.com/content/pdf/10.1007/s00253-018-9525-0.pdf",
        "https://www.sciencedirect.com/science/article/pii/S0142961214011223/pdfft",
    ):
        assert not is_supplementary_url(url), url


def test_candidato_suplementar_vindo_de_uma_api_nem_e_baixado(tmp_path, monkeypatch):
    import fetch

    baixados = []
    monkeypatch.setenv("PAPER_FETCH_TITLE_RECOVERY", "0")
    monkeypatch.setattr(fetch, "_download", lambda url, dest, **kw: baixados.append(url) or "http_404")
    monkeypatch.setattr(fetch, "try_semantic_scholar", lambda doi, **kw: (
        "https://static-content.springer.com/esm/art%3A10.1038%2Fx/MediaObjects/41467_2020_1_MOESM1_ESM.pdf", {}, {}))
    fetch.fetch("10.1038/x", tmp_path, dry_run=False, overwrite=False, timeout=5, sources=["semantic_scholar"])
    assert baixados == []
