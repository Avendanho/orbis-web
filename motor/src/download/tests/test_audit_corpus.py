"""Testes da auditoria de procedência do acervo.

O risco aqui é o inverso do usual: uma auditoria que acusa demais é tão
inútil quanto uma que não acusa nada. Na primeira rodada real, o padrão de
"sumário do periódico" reprovou dois artigos corretos — a Frontiers in
Bioscience imprime um "TABLE OF CONTENTS" dentro do próprio artigo. Por isso
há tantos testes de *não* acusar quanto de acusar.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit_corpus


class ListaDeDoisTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_le_lista_txt(self):
        p = self.dir / "l.txt"
        p.write_text("10.1000/abc\n10.1001/def\n", encoding="utf-8")
        self.assertEqual(audit_corpus.dois_de(p), {"10.1000/abc", "10.1001/def"})

    def test_le_csv_com_o_doi_em_qualquer_coluna(self):
        p = self.dir / "l.csv"
        p.write_text("arquivo,doi\nArtigo_2020.pdf,10.1000/abc\n", encoding="utf-8")
        self.assertEqual(audit_corpus.dois_de(p), {"10.1000/abc"})

    def test_normaliza_maiusculas_e_url(self):
        p = self.dir / "l.txt"
        p.write_text("https://doi.org/10.1000/ABC\n", encoding="utf-8")
        self.assertEqual(audit_corpus.dois_de(p), {"10.1000/abc"})

    def test_ignora_pontuacao_final(self):
        p = self.dir / "l.txt"
        p.write_text("10.1000/abc.\n10.1001/def;\n", encoding="utf-8")
        self.assertEqual(audit_corpus.dois_de(p), {"10.1000/abc", "10.1001/def"})


class ConteudoSuspeitoTests(unittest.TestCase):
    """As marcas são procuradas só no topo do documento."""

    def _inspecionar(self, texto, paginas=10):
        class FakePage:
            def __init__(self, t): self._t = t
            def get_text(self): return self._t

        class FakeDoc:
            page_count = paginas
            def __init__(self, t): self._p = [FakePage(t), FakePage("")]
            def __getitem__(self, i): return self._p[i]

        with patch.object(audit_corpus, "_abrir", return_value=FakeDoc(texto)):
            return audit_corpus.inspecionar_conteudo(Path("x.pdf"))

    def test_material_suplementar_e_acusado(self):
        _pg, problema = self._inspecionar(
            "SUPPLEMENTARY TABLE 1 Summary of genetically modified models\nGene\nBrain")
        self.assertIn("suplementar", problema)

    def test_formulario_editorial_e_acusado(self):
        _pg, problema = self._inspecionar(
            "nature research | life sciences reporting summary\nCorresponding author(s): X")
        self.assertIn("formulário", problema)

    def test_indice_de_resumos_e_acusado(self):
        _pg, problema = self._inspecionar(
            "ABSTRACTS BY NUMBER\n" + "\n".join(f"{i}. Resumo do trabalho {i}" for i in range(1, 12)))
        self.assertIn("resumos", problema)

    def test_pdf_so_de_imagem_e_acusado(self):
        _pg, problema = self._inspecionar("")
        self.assertIn("sem texto extraível", problema)

    def test_artigo_normal_passa(self):
        _pg, problema = self._inspecionar(
            "Targeted knockout of a chemokine-like gene increases anxiety\n"
            "Jung-Hwa Choi, Yun-Mi Jeong\nDepartment of Biology\nAbstract\n" + "texto " * 40)
        self.assertIsNone(problema)

    def test_sumario_dentro_do_artigo_nao_e_acusado(self):
        """Frontiers in Bioscience imprime TABLE OF CONTENTS no próprio artigo."""
        _pg, problema = self._inspecionar(
            "[Frontiers in Bioscience 6, d936-943, August 1, 2001]\n"
            "THE ASSOCIATION OF MHC GENES WITH AUTISM\n"
            "Anthony R. Torres, Alma Maciulis\nTABLE OF CONTENTS\n1. Abstract\n2. Introduction")
        self.assertIsNone(problema, "artigo correto foi acusado de ser sumário")

    def test_citar_material_suplementar_no_corpo_nao_acusa(self):
        """Um artigo que menciona seu próprio suplemento continua sendo o artigo."""
        corpo = ("The Microglial Receptor TREM2 Is Required for Synapse Elimination\n"
                 "Filipello et al.\nAbstract\n" + "texto " * 30 +
                 "\nas shown in Supplementary Table 2, the effect persists.\n")
        _pg, problema = self._inspecionar(corpo)
        self.assertIsNone(problema)

    def test_pdf_ilegivel_e_acusado(self):
        with patch.object(audit_corpus, "_abrir", side_effect=RuntimeError("zlib")):
            _pg, problema = audit_corpus.inspecionar_conteudo(Path("x.pdf"))
        self.assertIn("ilegível", problema)


class AuditoriaTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def pdf(self, nome, *, doi, detectado=None, validado=True, metodo="doi_in_pdf", sha=None):
        (self.dir / f"{nome}.pdf").write_bytes(b"%PDF-1.4\n")
        (self.dir / f"{nome}.pdf.identity.json").write_text(json.dumps({
            "identity_validated": validado, "validation_method": metodo,
            "detected_doi": detectado if detectado is not None else doi,
            "sha256": sha or nome, "expected": {"doi": doi},
        }), encoding="utf-8")

    def auditar(self, autorizados):
        return audit_corpus.auditar(self.dir, set(autorizados), conteudo=False)["achados"]

    def test_pdf_autorizado_nao_gera_achado(self):
        self.pdf("a", doi="10.1000/abc")
        a = self.auditar({"10.1000/abc"})
        self.assertEqual(a["fora_da_lista"], [])

    def test_pdf_fora_da_lista_e_acusado(self):
        self.pdf("a", doi="10.9999/intruso")
        a = self.auditar({"10.1000/abc"})
        self.assertEqual(len(a["fora_da_lista"]), 1)
        self.assertEqual(a["fora_da_lista"][0][1], "10.9999/intruso")

    def test_doi_divergente_e_acusado(self):
        self.pdf("a", doi="10.1000/abc", detectado="10.9999/outro")
        a = self.auditar({"10.1000/abc"})
        self.assertEqual(len(a["doi_divergente"]), 1)

    def test_pdf_sem_registro_de_identidade_e_acusado(self):
        (self.dir / "orfao.pdf").write_bytes(b"%PDF-1.4\n")
        a = self.auditar({"10.1000/abc"})
        self.assertEqual(a["sem_sidecar"], ["orfao.pdf"])

    def test_identidade_nao_validada_e_acusada(self):
        self.pdf("a", doi="10.1000/abc", validado=False)
        a = self.auditar({"10.1000/abc"})
        self.assertEqual(len(a["nao_validado"]), 1)

    def test_mesmo_doi_em_dois_arquivos_e_acusado(self):
        self.pdf("a", doi="10.1000/abc", sha="h1")
        self.pdf("b", doi="10.1000/abc", sha="h2")
        a = self.auditar({"10.1000/abc"})
        self.assertEqual(len(a["doi_duplicado"]), 1)

    def test_conteudo_identico_e_acusado(self):
        self.pdf("a", doi="10.1000/abc", sha="mesmo")
        self.pdf("b", doi="10.1000/def", sha="mesmo")
        a = self.auditar({"10.1000/abc", "10.1000/def"})
        self.assertEqual(len(a["conteudo_duplicado"]), 1)

    def test_lista_vazia_nao_acusa_todo_mundo(self):
        """Sem lista de referência, a procedência não pode ser julgada."""
        self.pdf("a", doi="10.1000/abc")
        a = self.auditar(set())
        self.assertEqual(a["fora_da_lista"], [])

    def test_metodo_de_validacao_e_contabilizado(self):
        self.pdf("a", doi="10.1000/abc", metodo="doi_in_pdf")
        self.pdf("b", doi="10.1000/def", metodo="doi_in_record")
        res = audit_corpus.auditar(self.dir, {"10.1000/abc", "10.1000/def"}, conteudo=False)
        self.assertEqual(res["metodos"]["doi_in_pdf"], 1)
        self.assertEqual(res["metodos"]["doi_in_record"], 1)


if __name__ == "__main__":
    unittest.main()
