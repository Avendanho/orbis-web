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


def chamar(tmp_path, modo="baixar", fetch=None, ident=None,
           extrair=lambda dados: ("texto do artigo", 3), projeto="proj-1", doi="10.1/a", pasta=None):
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
    r = chamar(tmp_path, extrair=lambda d: ("a" * 250_000, 10))
    assert len(r["texto"]) == motor_baixar.LIMITE_TEXTO
    assert r["chars"] == motor_baixar.LIMITE_TEXTO
    assert r["texto_truncado"] is True


def test_pdf_sem_texto_entra_com_aviso(tmp_path):
    def quebra(dados):
        raise ValueError("escaneado")
    r = chamar(tmp_path, extrair=quebra)
    assert r["ok"] is True and r["texto"] == "" and r["aviso"] == "sem_texto"
    r = chamar(tmp_path, extrair=lambda d: ("   \n", 1))
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
