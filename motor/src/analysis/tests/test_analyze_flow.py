"""O comando `analyze` inteiro, com o modelo trocado por um dublê.

Os testes de unidade cobrem as regras; este cobre a fiação. É a categoria de
erro que passa por todos os testes de unidade e só aparece rodando: a função
certa chamada com o argumento errado, o portão que existe mas ninguém
consulta, o registro de falha que nunca é gravado.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database
import llm_retry
import analysis_main  # registrado pelo conftest desta pasta
from config import settings

PARECER = json.dumps({
    "parecer_final": "INCLUIDO",
    "justificativa": "Atende aos critérios da etapa 1.",
    "motivo_principal": "-",
    "confidence_score": 92,
    "key_synthesis": "Estudo de associação genética.",
    "project_value_added": "Alto.",
    "Q1": "S",
    "extracted_snippets": {"Q1": ["trecho verbatim"]},
})

ARTIGO = "# Introdução\n\n" + ("Texto real do artigo analisado. " * 40)


class AnalyzeFlowTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        raiz = Path(self._tmp.name)
        self.extracted = raiz / "data" / "extracted"
        self.extracted.mkdir(parents=True)

        originais = (settings.db_dir, settings.output_dir)
        settings.db_dir = str(raiz / "data")
        settings.output_dir = str(raiz / "relatorio")
        self.addCleanup(lambda: (
            setattr(settings, "db_dir", originais[0]),
            setattr(settings, "output_dir", originais[1]),
        ))
        database.init_db()

    def artigo(self, article_id, texto, *, text_quality="HIGH"):
        d = self.extracted / article_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "content.md").write_text(texto, encoding="utf-8")
        (d / "metadata.json").write_text(json.dumps({
            "article_id": article_id, "filename": f"{article_id}.pdf",
            "text_quality": text_quality, "hash": f"hash-{article_id}",
        }), encoding="utf-8")

    def rodar(self, resposta_llm):
        """Executa analyze() com um LLM dublê e um protocolo válido no lugar."""
        protocolo = Path(self._tmp.name) / "protocolo_teste.txt"
        protocolo.write_text(
            "PROTOCOLO DE TRIAGEM DE TESTE. " * 30 + "\nQ1: O estudo é em humanos?",
            encoding="utf-8",
        )
        with patch.object(analysis_main, "PROTOCOL_PATH", protocolo), \
             patch("llm_client.get_llm_client", return_value=(resposta_llm, "Dublê 1.0")):
            analysis_main.analyze(workers=1)


class PortaoDeQualidadeTests(AnalyzeFlowTestCase):
    def test_artigo_bom_e_triado_e_gravado_com_procedencia(self):
        self.artigo("bom", ARTIGO)
        chamadas = []

        def llm(system, user, images=None):
            chamadas.append(user)
            return PARECER

        self.rodar(llm)

        row = database.get_article("bom")
        self.assertEqual(row["status"], "COMPLETED")
        self.assertEqual(row["decision"], "INCLUIDO")
        self.assertEqual(row["confidence"], "92")
        self.assertEqual(row["provider"], "Dublê 1.0")
        self.assertTrue(row["protocol_hash"])
        self.assertEqual(len(chamadas), 1)

    def test_pdf_com_extracao_quebrada_nunca_chega_ao_modelo(self):
        """O defeito central: a mensagem de erro ia ao LLM como se fosse o artigo."""
        self.artigo("quebrado", "Erro: Timeout ao processar o PDF. Excedeu 60 segundos.",
                    text_quality="ERROR")
        chamadas = []

        def llm(system, user, images=None):
            chamadas.append(user)
            return PARECER

        self.rodar(llm)

        self.assertEqual(chamadas, [], "o modelo foi consultado sobre um PDF ilegível")
        row = database.get_article("quebrado")
        self.assertEqual(row["status"], "FAILED")
        self.assertIsNone(row["decision"])
        self.assertEqual(row["failure_reason"], "extracao_falhou")


class FalhaDeApiTests(AnalyzeFlowTestCase):
    def test_api_fora_do_ar_vira_falha_e_nao_revisao_manual(self):
        self.artigo("a1", ARTIGO)

        def llm_caido(system, user, images=None):
            raise Exception("429 RESOURCE_EXHAUSTED: quota exceeded")

        with patch.object(llm_retry.time, "sleep"):
            self.rodar(llm_caido)

        row = database.get_article("a1")
        self.assertEqual(row["status"], "FAILED")
        self.assertIsNone(row["decision"], "uma queda de API virou decisão de triagem")
        self.assertEqual(row["failure_reason"], "llm_indisponivel")

    def test_uma_queda_passageira_e_superada(self):
        self.artigo("a1", ARTIGO)
        tentativas = {"n": 0}

        def llm_instavel(system, user, images=None):
            tentativas["n"] += 1
            if tentativas["n"] == 1:
                raise Exception("503 Service Unavailable")
            return PARECER

        with patch.object(llm_retry.time, "sleep"):
            self.rodar(llm_instavel)

        self.assertEqual(tentativas["n"], 2)
        self.assertEqual(database.get_article("a1")["decision"], "INCLUIDO")

    def test_resposta_ilegivel_e_falha_tecnica(self):
        self.artigo("a1", ARTIGO)

        with patch.object(llm_retry.time, "sleep"):
            self.rodar(lambda s, u, images=None: "Desculpe, não posso ajudar.")

        row = database.get_article("a1")
        self.assertEqual(row["status"], "FAILED")
        self.assertIsNone(row["decision"])

    def test_nenhum_artigo_desaparece(self):
        """Antes, um erro inesperado só imprimia e o artigo sumia do banco."""
        for i in range(3):
            self.artigo(f"a{i}", ARTIGO)

        def llm_explosivo(system, user, images=None):
            raise KeyError("algo totalmente inesperado")

        with patch.object(llm_retry.time, "sleep"):
            self.rodar(llm_explosivo)

        for i in range(3):
            row = database.get_article(f"a{i}")
            self.assertIsNotNone(row, f"a{i} sumiu do banco")
            self.assertEqual(row["status"], "FAILED")


if __name__ == "__main__":
    unittest.main()
