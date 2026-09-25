"""O relatório é o produto final. Ele precisa contar certo.

O risco específico: uma falha técnica entrar nas contagens de triagem. Se
isso acontece, o número que sai daqui vai para o fluxograma PRISMA e de lá
para o texto da revisão — um erro que ninguém percebe porque a soma fecha.
"""
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database
import analysis_main  # registrado pelo conftest desta pasta
from config import settings


class ReportTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        raiz = Path(self._tmp.name)
        self.saida = raiz / "relatorio"

        originais = (settings.db_dir, settings.output_dir)
        settings.db_dir = str(raiz / "data")
        settings.output_dir = str(self.saida)
        self.addCleanup(lambda: (
            setattr(settings, "db_dir", originais[0]),
            setattr(settings, "output_dir", originais[1]),
        ))
        database.init_db()

    def parecer(self, article_id, decision, *, conf=90, code=None, qs=None):
        raw = {"key_synthesis": "síntese", "project_value_added": "valor",
               "confidence_score": conf}
        raw.update(qs or {})
        database.save_analysis(article_id, f"{article_id}.pdf", "h", {
            "decision": decision, "exclusion_code": code, "confidence": conf,
            "justificativa": "porque sim", "raw_json": raw,
        })

    def relatorio(self):
        analysis_main.report()
        return (self.saida / "RELATORIO_FINAL.md").read_text(encoding="utf-8")


class ContagemTests(ReportTestCase):
    def test_falhas_nao_entram_nas_contagens_de_triagem(self):
        self.parecer("a1", "INCLUIDO")
        self.parecer("a2", "EXCLUIDO", code="E1")
        database.save_failure("a3", "quebrado.pdf", "h", "extracao_falhou")
        database.save_failure("a4", "caiu.pdf", "h", "llm_indisponivel")

        texto = self.relatorio()

        self.assertIn("**INCLUÍDOS:** 1", texto)
        self.assertIn("**EXCLUÍDOS:** 1", texto)
        self.assertIn("**REVISÃO MANUAL:** 0", texto)
        self.assertIn("**NÃO TRIADOS (falha técnica):** 2", texto)
        self.assertIn("Triados: 2 de 4 artigos", texto)

    def test_uma_falha_de_api_nao_vira_revisao_manual(self):
        """Era o defeito mais perigoso: infraestrutura virando decisão."""
        database.save_failure("a1", "caiu.pdf", "h", "llm_indisponivel")
        texto = self.relatorio()
        self.assertIn("**REVISÃO MANUAL:** 0", texto)
        self.assertIn("o provedor de IA não respondeu", texto)

    def test_os_nao_triados_aparecem_com_nome_e_motivo(self):
        database.save_failure("a1", "quebrado.pdf", "h", "extracao_falhou")
        texto = self.relatorio()
        self.assertIn("## 🚫 Artigos não triados", texto)
        self.assertIn("quebrado.pdf", texto)
        self.assertIn("o texto do PDF não pôde ser extraído", texto)

    def test_sem_falhas_a_secao_nao_aparece(self):
        self.parecer("a1", "INCLUIDO")
        self.assertNotIn("Artigos não triados", self.relatorio())

    def test_csv_so_traz_os_triados(self):
        self.parecer("a1", "INCLUIDO")
        database.save_failure("a2", "quebrado.pdf", "h", "extracao_falhou")
        analysis_main.report()
        linhas = (self.saida / "resultados.csv").read_text(encoding="utf-8").strip().split("\n")
        self.assertEqual(len(linhas), 2, "cabeçalho + 1 artigo triado")
        self.assertIn("a1", linhas[1])


class ConfiancaTests(ReportTestCase):
    def test_confianca_numerica_sai_com_porcentagem(self):
        self.parecer("a1", "INCLUIDO", conf=88)
        self.assertIn("88%", self.relatorio())

    def test_confianca_sem_numero_nao_vira_BAIXO_porcento(self):
        database.save_analysis("a1", "x.pdf", "h", {
            "decision": "REVISÃO MANUAL", "confidence": "BAIXO",
            "justificativa": "sem dados", "raw_json": {},
        })
        texto = self.relatorio()
        self.assertNotIn("BAIXO%", texto)
        self.assertIn("não informada", texto)


class PerguntasTests(ReportTestCase):
    def test_perguntas_alem_da_decima_quinta_sao_exportadas(self):
        """Q20 era descartada em silêncio pela lista fixa em Q15."""
        protocolo = Path(self._tmp.name) / "protocolo_teste.txt"
        protocolo.write_text("\n".join(f"Q{i}: pergunta {i}" for i in range(1, 21)),
                             encoding="utf-8")

        self.parecer("a1", "INCLUIDO", qs={"Q20": "S", "extracted_snippets": {"Q20": ["trecho"]}})
        with patch.object(analysis_main, "PROTOCOL_PATH", protocolo):
            analysis_main.report()

        q20 = self.saida / "relatorio_Q20.json"
        self.assertTrue(q20.exists(), "a pergunta Q20 não foi exportada")
        self.assertEqual(json.loads(q20.read_text(encoding="utf-8"))["a1"]["answer"], "S")


if __name__ == "__main__":
    unittest.main()
