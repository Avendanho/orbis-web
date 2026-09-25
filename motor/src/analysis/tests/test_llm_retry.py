"""Uma chamada que caiu não é um parecer de triagem.

Com 10 workers contra o Gemini, 429 é rotina, não exceção. Sem repetição cada
um desses vira "REVISÃO MANUAL" e entra no PRISMA como artigo triado. Este
módulo repete o que vale a pena repetir e, quando desiste, desiste de um jeito
que o chamador consegue distinguir de uma decisão.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import llm_retry


class FalhaDeQuota(Exception):
    """Imita o 429 dos SDKs, que expõem o código no texto."""
    def __init__(self):
        super().__init__("429 RESOURCE_EXHAUSTED: quota exceeded, retry in 12s")


class FalhaPermanente(Exception):
    def __init__(self):
        super().__init__("401 UNAUTHENTICATED: API key not valid")


class ClassificacaoTests(unittest.TestCase):
    def test_limite_de_taxa_vale_a_pena_repetir(self):
        self.assertTrue(llm_retry.is_retryable(FalhaDeQuota()))

    def test_erros_de_servidor_valem_a_pena(self):
        for msg in ("500 Internal Server Error", "503 Service Unavailable",
                    "502 Bad Gateway", "529 Overloaded"):
            with self.subTest(msg=msg):
                self.assertTrue(llm_retry.is_retryable(Exception(msg)))

    def test_quedas_de_conexao_valem_a_pena(self):
        for exc in (TimeoutError("timed out"), ConnectionError("connection reset by peer")):
            with self.subTest(exc=type(exc).__name__):
                self.assertTrue(llm_retry.is_retryable(exc))

    def test_credencial_invalida_nao_melhora_com_insistencia(self):
        self.assertFalse(llm_retry.is_retryable(FalhaPermanente()))

    def test_prompt_grande_demais_nao_melhora_com_insistencia(self):
        self.assertFalse(llm_retry.is_retryable(Exception("400 request exceeds the maximum token limit")))

    def test_recusa_de_conteudo_nao_melhora_com_insistencia(self):
        self.assertFalse(llm_retry.is_retryable(ValueError("Não foi possível extrair um objeto JSON")))


class ChamadaTests(unittest.TestCase):
    def setUp(self):
        self.dormidas = []
        patcher = patch.object(llm_retry.time, "sleep", side_effect=self.dormidas.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_sucesso_de_primeira_nao_espera(self):
        resultado = llm_retry.call_with_retry(lambda: "ok")
        self.assertEqual(resultado, "ok")
        self.assertEqual(self.dormidas, [])

    def test_repete_ate_dar_certo(self):
        tentativas = {"n": 0}

        def instavel():
            tentativas["n"] += 1
            if tentativas["n"] < 3:
                raise FalhaDeQuota()
            return "ok"

        self.assertEqual(llm_retry.call_with_retry(instavel), "ok")
        self.assertEqual(tentativas["n"], 3)
        self.assertEqual(len(self.dormidas), 2)

    def test_a_espera_cresce_a_cada_tentativa(self):
        def sempre_falha():
            raise FalhaDeQuota()

        with self.assertRaises(llm_retry.LlmCallFailed):
            llm_retry.call_with_retry(sempre_falha, attempts=4)
        self.assertEqual(len(self.dormidas), 3)
        self.assertLess(self.dormidas[0], self.dormidas[-1])

    def test_falha_permanente_nao_e_repetida(self):
        tentativas = {"n": 0}

        def credencial_ruim():
            tentativas["n"] += 1
            raise FalhaPermanente()

        with self.assertRaises(llm_retry.LlmCallFailed):
            llm_retry.call_with_retry(credencial_ruim)
        self.assertEqual(tentativas["n"], 1, "insistiu numa falha que não melhora")
        self.assertEqual(self.dormidas, [])

    def test_a_falha_final_carrega_a_causa_original(self):
        def sempre_falha():
            raise FalhaDeQuota()

        with self.assertRaises(llm_retry.LlmCallFailed) as ctx:
            llm_retry.call_with_retry(sempre_falha, attempts=2)
        self.assertIsInstance(ctx.exception.__cause__, FalhaDeQuota)
        self.assertIn("429", str(ctx.exception))

    def test_a_falha_final_diz_quantas_tentativas_houve(self):
        with self.assertRaises(llm_retry.LlmCallFailed) as ctx:
            llm_retry.call_with_retry(lambda: (_ for _ in ()).throw(FalhaDeQuota()), attempts=3)
        self.assertEqual(ctx.exception.attempts, 3)

    def test_a_espera_nunca_passa_do_teto(self):
        with self.assertRaises(llm_retry.LlmCallFailed):
            llm_retry.call_with_retry(lambda: (_ for _ in ()).throw(FalhaDeQuota()), attempts=12)
        self.assertTrue(all(d <= llm_retry.MAX_DELAY for d in self.dormidas))


if __name__ == "__main__":
    unittest.main()
