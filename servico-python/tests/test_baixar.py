import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import baixar as motor_baixar  # noqa: E402

PDF = b"%PDF-1.7\nconteudo"


class FetchFalso:
    def __init__(self, sucesso=True, nome="Silva_2021_T.pdf", erro_depois=False, conteudo=None):
        self.sucesso, self.nome, self.erro_depois, self.conteudo = sucesso, nome, erro_depois, conteudo
        self.prazos, self.pastas = [], []

    def set_item_deadline(self, segundos):
        self.prazos.append(segundos)

    def fetch(self, doi, out_dir, *, dry_run, overwrite, timeout, sources=None):
        self.pastas.append(Path(out_dir))
        if not self.sucesso:
            return {"doi": doi, "success": False, "file": None,
                    "sources_tried": ["unpaywall", "pmc"], "error": "not found"}
        arquivo = Path(out_dir) / self.nome
        arquivo.write_bytes(self.conteudo(doi) if self.conteudo else PDF)
        if self.erro_depois:
            raise RuntimeError("falha no meio")
        return {"doi": doi, "success": True, "source": "pmc", "file": str(arquivo), "sources_tried": ["pmc"]}


class IdentidadeFalsa:
    def __init__(self, ok=True, motivo=None):
        self.ok, self.motivo, self.esperado = ok, motivo, None

    def extract_pdf_identity(self, dados):
        return {"doi": None}

    def validate_article_identity(self, esperado, *, pdf_identity=None, record_doi_matched=False):
        self.esperado = esperado
        return {"identity_validated": self.ok, "validation_method": "doi_in_pdf",
                "validation_score": 1.0 if self.ok else 0.0, "reason": self.motivo}


def texto_de(texto, paginas=3):
    """Extração falsa no formato de ``extracao.extrair``: (caminho, pasta_imagens) -> dict."""
    return lambda caminho, pasta_imagens: {"texto": texto, "formato": "texto", "paginas": paginas, "imagens": 0}


def chamar(tmp_path, modo="baixar", fetch=None, ident=None,
           extrair=texto_de("texto do artigo"), projeto="proj-1", doi="10.1/a", pasta=None):
    return motor_baixar.baixar_artigo(
        doi=doi, projeto=projeto, modo=modo, esperado={"title": "T"}, prazo=90,
        pasta_pdfs=pasta or tmp_path / "pdfs", fetch_mod=fetch or FetchFalso(),
        identity_mod=ident or IdentidadeFalsa(), extrair=extrair)


def test_modo_baixar_grava_pdf_e_relatorio(tmp_path):
    r = chamar(tmp_path)
    assert r["ok"] is True
    assert r["arquivo"] == "Silva_2021_T.pdf"
    assert (tmp_path / "pdfs" / "proj-1" / "Silva_2021_T.pdf").read_bytes() == PDF
    relatorio = (tmp_path / "pdfs" / "proj-1" / "Relatório.txt").read_text(encoding="utf-8")
    assert "10.1/a\tbaixado\tpmc\tSilva_2021_T.pdf" in relatorio
    assert r["texto"] == "texto do artigo" and r["paginas"] == 3 and r["chars"] == 15
    assert r["identidade"] == {"ok": True, "metodo": "doi_in_pdf", "score": 1.0, "detalhe": "identidade confirmada"}


def test_modo_analisar_nao_deixa_arquivo(tmp_path):
    f = FetchFalso()
    r = chamar(tmp_path, modo="analisar", fetch=f)
    assert r["ok"] is True and r["texto"] == "texto do artigo"
    assert "arquivo" not in r
    assert not f.pastas[0].exists()
    assert not (tmp_path / "pdfs").exists()


def test_modo_analisar_apaga_mesmo_com_erro(tmp_path):
    f = FetchFalso(erro_depois=True)
    with pytest.raises(RuntimeError):
        chamar(tmp_path, modo="analisar", fetch=f)
    assert not f.pastas[0].exists()


@pytest.mark.parametrize("modo", ["baixar", "analisar"])
def test_identidade_reprovada_apaga_pdf(tmp_path, modo):
    f = FetchFalso()
    r = chamar(tmp_path, modo=modo, fetch=f, ident=IdentidadeFalsa(ok=False, motivo="doi_mismatch"))
    assert r["ok"] is False
    assert r["erro"] == "O DOI impresso no PDF é de outro artigo."
    assert r["identidade"]["ok"] is False
    assert not (f.pastas[0] / "Silva_2021_T.pdf").exists()


def test_prazo_repassado_e_limpo_mesmo_com_erro(tmp_path):
    f = FetchFalso()
    chamar(tmp_path, fetch=f)
    assert f.prazos == [90, None]
    f = FetchFalso(erro_depois=True)
    with pytest.raises(RuntimeError):
        chamar(tmp_path, fetch=f)
    assert f.prazos == [90, None]


def test_esperado_leva_o_doi(tmp_path):
    ident = IdentidadeFalsa()
    chamar(tmp_path, ident=ident)
    assert ident.esperado == {"title": "T", "doi": "10.1/a"}


@pytest.mark.parametrize("projeto", ["../x", "", "a/b", "x" * 65])
def test_projeto_invalido(tmp_path, projeto):
    with pytest.raises(motor_baixar.PedidoInvalido):
        chamar(tmp_path, projeto=projeto)


def test_modo_invalido(tmp_path):
    with pytest.raises(motor_baixar.PedidoInvalido):
        chamar(tmp_path, modo="tudo")


def test_nao_localizado_registra_no_relatorio(tmp_path):
    r = chamar(tmp_path, fetch=FetchFalso(sucesso=False))
    assert r == {"ok": False, "erro": "not found", "fontes_tentadas": ["unpaywall", "pmc"]}
    assert "10.1/a\tnao_localizado" in (tmp_path / "pdfs" / "proj-1" / "Relatório.txt").read_text(encoding="utf-8")


def test_texto_truncado(tmp_path):
    r = chamar(tmp_path, extrair=texto_de("a" * 250_000, 10))
    assert len(r["texto"]) == motor_baixar.LIMITE_TEXTO
    assert r["chars"] == motor_baixar.LIMITE_TEXTO
    assert r["texto_truncado"] is True


def test_pdf_sem_texto_entra_com_aviso(tmp_path):
    def quebra(caminho, pasta_imagens):
        raise ValueError("escaneado")
    r = chamar(tmp_path, extrair=quebra)
    assert r["ok"] is True and r["texto"] == "" and r["aviso"] == "sem_texto"
    r = chamar(tmp_path, extrair=texto_de("   \n", 1))
    assert r["texto"] == "" and r["aviso"] == "sem_texto"


def test_mesmo_doi_duas_vezes_um_pdf(tmp_path):
    chamar(tmp_path)
    chamar(tmp_path)
    pasta = tmp_path / "pdfs" / "proj-1"
    assert sorted(p.name for p in pasta.glob("*.pdf")) == ["Silva_2021_T.pdf"]
    assert len((pasta / "Relatório.txt").read_text(encoding="utf-8").splitlines()) == 2


def test_disco_indisponivel(tmp_path):
    ocupado = tmp_path / "pdfs"
    ocupado.write_text("isto é um arquivo, não uma pasta")
    with pytest.raises(motor_baixar.DiscoIndisponivel):
        chamar(tmp_path, pasta=ocupado)


# --- Colisão de nomes (achada na verificação real: a fonte pmc_s3 não traz
# autor nem título, e o motor nomeia todos os PDFs "unknown_nd_paper.pdf").

def _por_doi(doi):
    return b"%PDF-1.7\n" + doi.encode()


def test_baixar_usa_pasta_temporaria_no_fetch(tmp_path):
    f = FetchFalso()
    chamar(tmp_path, fetch=f)
    assert f.pastas[0] != tmp_path / "pdfs" / "proj-1"
    assert not f.pastas[0].exists()


def test_dois_dois_com_mesmo_nome_nao_se_sobrescrevem(tmp_path):
    f = FetchFalso(nome="Silva_2021_T.pdf", conteudo=_por_doi)
    a = chamar(tmp_path, fetch=f, doi="10.1/a")
    b = chamar(tmp_path, fetch=f, doi="10.1/b")
    pasta = tmp_path / "pdfs" / "proj-1"
    assert a["arquivo"] != b["arquivo"]
    assert (pasta / a["arquivo"]).read_bytes() == _por_doi("10.1/a")
    assert (pasta / b["arquivo"]).read_bytes() == _por_doi("10.1/b")


def test_nome_generico_vira_o_doi(tmp_path):
    r = chamar(tmp_path, fetch=FetchFalso(nome="unknown_nd_paper.pdf"), doi="10.1234/x.y(z)")
    assert r["arquivo"] == "10.1234_x.y_z.pdf"


def test_mesmo_doi_reaproveita_o_nome(tmp_path):
    f = FetchFalso(nome="Silva_2021_T.pdf", conteudo=_por_doi)
    chamar(tmp_path, fetch=f, doi="10.1/a")
    chamar(tmp_path, fetch=f, doi="10.1/b")
    again = chamar(tmp_path, fetch=f, doi="10.1/b")
    assert again["arquivo"] != "Silva_2021_T.pdf"
    assert len(list((tmp_path / "pdfs" / "proj-1").glob("*.pdf"))) == 2


def test_identidade_reprovada_nao_apaga_pdf_de_outro_artigo(tmp_path):
    f = FetchFalso(nome="unknown_nd_paper.pdf", conteudo=_por_doi)
    a = chamar(tmp_path, fetch=f, doi="10.1/a")
    r = chamar(tmp_path, fetch=f, doi="10.1/b", ident=IdentidadeFalsa(ok=False, motivo="doi_mismatch"))
    assert r["ok"] is False
    assert (tmp_path / "pdfs" / "proj-1" / a["arquivo"]).read_bytes() == _por_doi("10.1/a")


class FetchComErro(FetchFalso):
    def __init__(self, erro):
        super().__init__(sucesso=False)
        self.erro = erro

    def fetch(self, doi, out_dir, *, dry_run, overwrite, timeout, sources=None):
        return {"doi": doi, "success": False, "file": None, "sources_tried": ["scihub"], "error": self.erro}


@pytest.mark.parametrize("erro,trecho", [
    ({"code": "not_found", "message": "No open-access PDF found"}, "Nenhuma cópia gratuita"),
    ({"code": "article_identity_not_confirmed", "message": "3 candidate PDF(s) were downloaded but rejected"}, "3 PDF(s)"),
    ({"code": "download_http_403", "message": "Download failed from crossref: http_403"}, "bloqueia o download automático"),
    ({"code": "download_http_500", "message": "Download failed from libgen: http_500"}, "HTTP 500"),
    ({"code": "download_http_429", "message": "x"}, "HTTP 429"),
    ({"code": "download_timeout", "message": "x"}, "não respondeu a tempo"),
    ({"code": "download_item_deadline", "message": "x"}, "tempo reservado"),
    ({"code": "resolve_network_error", "message": "x"}, "bases de metadados"),
    ({"code": "download_algo_novo", "message": "Download failed from x: algo_novo"}, "algo_novo"),
    ({"code": "download_sem_acesso_institucional", "message": "x"}, "não dá acesso a este artigo"),
    ({"code": "download_sem_pdf_na_pagina", "message": "x"}, "não ofereceu o PDF"),
    ({"code": "download_prazo_esgotado_na_fila", "message": "x"}, "sessão institucional atendia"),
])
def test_falha_vira_frase_legivel(tmp_path, erro, trecho):
    r = chamar(tmp_path, fetch=FetchComErro(erro))
    assert r["ok"] is False
    assert trecho in r["erro"]
    assert "{" not in r["erro"] and "'code'" not in r["erro"]
    assert r["codigo"] == erro["code"]


class FetchSessaoExpirada(FetchFalso):
    def fetch(self, doi, out_dir, *, dry_run, overwrite, timeout, sources=None):
        return {"doi": doi, "success": False, "file": None, "sources_tried": ["sessao_institucional", "crossref"],
                "download_attempts": [{"source": "sessao_institucional", "url": None, "reason": "sessao_expirada"},
                                      {"source": "crossref", "url": "https://x", "reason": "http_403"}],
                "error": {"code": "download_http_403", "message": "x"}}


def test_sessao_institucional_expirada_diz_como_renovar(tmp_path):
    r = chamar(tmp_path, fetch=FetchSessaoExpirada())
    assert r["ok"] is False
    assert "bloqueia o download automático" in r["erro"]
    assert "sessão institucional" in r["erro"] and "sessao_navegador.py login" in r["erro"]
    assert r["sessao_expirada"] is True


# --- Markdown (parte B): o texto sai do arquivo guardado; no modo "baixar" o
# .md e as imagens ficam ao lado do PDF.

class ExtracaoFalsa:
    """Grava uma imagem na pasta pedida, como o pymupdf4llm."""
    def __init__(self, formato="markdown", aviso=None):
        self.formato, self.aviso, self.chamadas = formato, aviso, []

    def __call__(self, caminho, pasta_imagens):
        self.chamadas.append((Path(caminho), pasta_imagens))
        assert Path(caminho).read_bytes().startswith(b"%PDF"), "extrai do arquivo, não dos bytes"
        imagens = 0
        if pasta_imagens:
            pasta_imagens.mkdir()
            (pasta_imagens / "fig-1.png").write_bytes(b"png")
            imagens = 1
        r = {"texto": "# Titulo\n\n![](%s/fig-1.png)" % (pasta_imagens.name if pasta_imagens else "x"),
             "formato": self.formato, "paginas": 2, "imagens": imagens}
        if self.aviso:
            r["aviso"] = self.aviso
        return r


def test_baixar_grava_md_e_imagens_ao_lado_do_pdf(tmp_path):
    ex = ExtracaoFalsa()
    r = chamar(tmp_path, extrair=ex)
    pasta = tmp_path / "pdfs" / "proj-1"
    assert ex.chamadas == [(pasta / "Silva_2021_T.pdf", pasta / "Silva_2021_T_imagens")]
    assert r["formato"] == "markdown" and r["imagens"] == 1
    assert r["arquivo_md"] == "Silva_2021_T.md" and r["pasta_imagens"] == "Silva_2021_T_imagens"
    assert (pasta / "Silva_2021_T.md").read_text(encoding="utf-8").startswith("# Titulo")
    assert (pasta / "Silva_2021_T_imagens" / "fig-1.png").exists()
    assert r["texto"].startswith("# Titulo")


def test_analisar_nao_deixa_md_nem_imagens(tmp_path):
    ex, f = ExtracaoFalsa(), FetchFalso()
    r = chamar(tmp_path, modo="analisar", fetch=f, extrair=ex)
    assert ex.chamadas[0][1] is None, "sem pasta de imagens no modo analisar"
    assert r["formato"] == "markdown" and r["imagens"] == 0
    assert "arquivo_md" not in r and "pasta_imagens" not in r
    assert not (tmp_path / "pdfs").exists() and not f.pastas[0].exists()


def test_texto_simples_nao_grava_md(tmp_path):
    r = chamar(tmp_path, extrair=ExtracaoFalsa(formato="texto", aviso="Falha na extração em Markdown; foi usado o texto simples."))
    assert r["formato"] == "texto" and "arquivo_md" not in r
    assert r["aviso_extracao"] == "Falha na extração em Markdown; foi usado o texto simples."
    assert not list((tmp_path / "pdfs" / "proj-1").glob("*.md"))


def test_repetir_o_doi_substitui_md(tmp_path):
    chamar(tmp_path, extrair=ExtracaoFalsa())
    chamar(tmp_path, extrair=texto_de("sem markdown agora"))
    pasta = tmp_path / "pdfs" / "proj-1"
    assert not (pasta / "Silva_2021_T.md").exists(), ".md antigo não fica mentindo sobre o texto"
    assert not (pasta / "Silva_2021_T_imagens").exists()


def test_extracao_padrao_usa_o_modulo(tmp_path, monkeypatch):
    import extracao
    visto = []
    monkeypatch.setattr(extracao, "extrair_conforme_opcoes", lambda c, p: visto.append((c, p)) or
                        {"texto": "t", "formato": "texto", "paginas": 1, "imagens": 0})
    motor_baixar.baixar_artigo(doi="10.1/a", projeto="p", modo="analisar", esperado={}, prazo=90,
                               pasta_pdfs=tmp_path, fetch_mod=FetchFalso(), identity_mod=IdentidadeFalsa())
    assert len(visto) == 1
