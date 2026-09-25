"""O banco precisa saber a diferença entre 'não deu' e 'o modelo decidiu'.

Antes, um artigo que estourava exceção não era gravado em lugar nenhum: sumia
do CSV, do relatório e das contagens PRISMA. Num fluxo cujo produto é uma
contagem auditável, perder um item em silêncio é o pior modo de falhar — a
soma continua parecendo certa.
"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database
from config import settings


class DatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db_dir_original = settings.db_dir
        settings.db_dir = self._tmp.name
        self.addCleanup(lambda: setattr(settings, "db_dir", self._db_dir_original))
        database.init_db()


class GravacaoTests(DatabaseTestCase):
    def test_um_parecer_completo_fica_como_COMPLETED(self):
        database.save_analysis("a1", "artigo.pdf", "h1", {
            "decision": "INCLUIDO", "confidence": 90, "justificativa": "atende",
        })
        row = database.get_article("a1")
        self.assertEqual(row["status"], "COMPLETED")
        self.assertEqual(row["decision"], "INCLUIDO")

    def test_uma_falha_tecnica_fica_como_FAILED_e_sem_decisao(self):
        database.save_failure("a2", "quebrado.pdf", "h2", "extracao_falhou")
        row = database.get_article("a2")
        self.assertEqual(row["status"], "FAILED")
        self.assertIsNone(row["decision"])
        self.assertEqual(row["failure_reason"], "extracao_falhou")

    def test_a_falha_nao_inventa_uma_confianca(self):
        database.save_failure("a3", "x.pdf", "h3", "llm_indisponivel")
        self.assertIsNone(database.get_article("a3")["confidence"])

    def test_um_parecer_posterior_substitui_a_falha_anterior(self):
        """Rodar de novo depois de resolver o problema tem que limpar o estado."""
        database.save_failure("a4", "x.pdf", "h4", "llm_indisponivel")
        database.save_analysis("a4", "x.pdf", "h4", {"decision": "EXCLUIDO", "confidence": 70})
        row = database.get_article("a4")
        self.assertEqual(row["status"], "COMPLETED")
        self.assertEqual(row["decision"], "EXCLUIDO")
        self.assertIsNone(row["failure_reason"])

    def test_procedencia_do_veredito_fica_registrada(self):
        """Numa revisão sistemática é preciso dizer quem decidiu e com qual protocolo."""
        database.save_analysis("a5", "x.pdf", "h5", {"decision": "INCLUIDO", "confidence": 80},
                               provider="Gemini 2.5 Flash", protocol_hash="abc123")
        row = database.get_article("a5")
        self.assertEqual(row["provider"], "Gemini 2.5 Flash")
        self.assertEqual(row["protocol_hash"], "abc123")

    def test_artigo_inexistente_devolve_none(self):
        self.assertIsNone(database.get_article("nao_existe"))


class MigracaoTests(DatabaseTestCase):
    def test_banco_antigo_ganha_as_colunas_novas_sem_perder_dados(self):
        """Quem já rodou a versão anterior não pode ter que apagar o banco."""
        db_path = Path(self._tmp.name) / "analysis.db"
        db_path.unlink(missing_ok=True)

        antigo = sqlite3.connect(db_path)
        antigo.execute("""
            CREATE TABLE articles (
                article_id TEXT PRIMARY KEY, filename TEXT, hash TEXT, status TEXT,
                decision TEXT, exclusion_code TEXT, confidence TEXT,
                analysis_json TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
        antigo.execute(
            "INSERT INTO articles (article_id, filename, status, decision) VALUES (?,?,?,?)",
            ("velho", "antigo.pdf", "COMPLETED", "INCLUIDO"))
        antigo.commit()
        antigo.close()

        database.init_db()

        row = database.get_article("velho")
        self.assertEqual(row["decision"], "INCLUIDO", "o registro antigo foi perdido")
        self.assertIn("failure_reason", row.keys())
        self.assertIn("provider", row.keys())

    def test_init_db_pode_rodar_varias_vezes(self):
        for _ in range(3):
            database.init_db()
        database.save_analysis("a6", "x.pdf", "h6", {"decision": "INCLUIDO"})
        self.assertIsNotNone(database.get_article("a6"))


if __name__ == "__main__":
    unittest.main()
