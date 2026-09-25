"""A triagem decide o que entra na revisão. Estes testes cuidam disso.

O princípio que todos eles defendem é um só: **falha técnica não é decisão
científica**. Um PDF que não pôde ser lido, uma chamada de API que caiu e um
artigo que o modelo avaliou são três coisas distintas, e a única inaceitável
é confundi-las — porque o produto final é uma contagem que alguém vai citar.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import screening


class ScreenableTextTests(unittest.TestCase):
    """O portão de qualidade: o que pode ou não ser enviado ao modelo."""

    def test_um_artigo_de_verdade_passa(self):
        texto = "# Introdução\n\n" + ("Texto real do artigo sobre autismo. " * 40)
        ok, motivo = screening.is_screenable(texto, {"text_quality": "HIGH"})
        self.assertTrue(ok)
        self.assertIsNone(motivo)

    def test_texto_marcado_como_ERROR_nunca_vai_ao_modelo(self):
        texto = "Erro na extração PyMuPDF4LLM: cannot open broken document"
        ok, motivo = screening.is_screenable(texto, {"text_quality": "ERROR"})
        self.assertFalse(ok)
        self.assertEqual(motivo, "extracao_falhou")

    def test_a_mensagem_de_timeout_e_reconhecida_mesmo_sem_o_metadado(self):
        """O metadata pode ser de uma versão antiga; o conteúdo se denuncia."""
        texto = "Erro: Timeout ao processar o PDF. Excedeu 60 segundos."
        ok, motivo = screening.is_screenable(texto, {})
        self.assertFalse(ok)
        self.assertEqual(motivo, "extracao_falhou")

    def test_a_mensagem_de_extracao_vazia_e_reconhecida(self):
        ok, motivo = screening.is_screenable("Não foi possível extrair o texto do PDF.", {})
        self.assertFalse(ok)
        self.assertEqual(motivo, "extracao_falhou")

    def test_texto_vazio_nao_e_triavel(self):
        for vazio in ("", "   ", "\n\n"):
            with self.subTest(valor=repr(vazio)):
                ok, motivo = screening.is_screenable(vazio, {})
                self.assertFalse(ok)
                self.assertEqual(motivo, "sem_texto")

    def test_texto_curto_demais_para_uma_triagem_honesta(self):
        """Uma capa ou uma errata não sustentam um parecer."""
        ok, motivo = screening.is_screenable("Um parágrafo solto.", {})
        self.assertFalse(ok)
        self.assertEqual(motivo, "texto_insuficiente")

    def test_a_palavra_erro_no_corpo_do_artigo_nao_reprova_nada(self):
        """'erro padrão' é estatística, não falha de extração."""
        texto = "Métodos. O erro padrão da média foi calculado. " * 30
        ok, _motivo = screening.is_screenable(texto, {"text_quality": "HIGH"})
        self.assertTrue(ok)


class ParseLlmJsonTests(unittest.TestCase):
    def test_json_limpo(self):
        self.assertEqual(screening.parse_llm_json('{"parecer_final": "INCLUIDO"}'),
                         {"parecer_final": "INCLUIDO"})

    def test_json_dentro_de_cerca_de_codigo(self):
        bruto = '```json\n{"parecer_final": "EXCLUIDO"}\n```'
        self.assertEqual(screening.parse_llm_json(bruto)["parecer_final"], "EXCLUIDO")

    def test_json_com_texto_em_volta(self):
        bruto = 'Claro! Segue a análise:\n{"parecer_final": "INCLUIDO"}\nEspero ter ajudado.'
        self.assertEqual(screening.parse_llm_json(bruto)["parecer_final"], "INCLUIDO")

    def test_resposta_sem_json_algum_levanta_erro(self):
        with self.assertRaises(ValueError):
            screening.parse_llm_json("Desculpe, não posso ajudar com isso.")

    def test_resposta_vazia_levanta_erro(self):
        for vazio in ("", None, "   "):
            with self.subTest(valor=repr(vazio)):
                with self.assertRaises(ValueError):
                    screening.parse_llm_json(vazio)

    def test_um_json_truncado_nao_vira_parecer_silencioso(self):
        """Fechar a chave à força produziria um objeto válido e incompleto.

        Um parecer montado a partir de um JSON cortado no meio é pior do que
        nenhum parecer: parece íntegro e não é. Tem que falhar alto.
        """
        truncado = '{"parecer_final": "EXCLUIDO", "justificativa": "O artigo nao atende ao crit'
        with self.assertRaises(ValueError):
            screening.parse_llm_json(truncado)


class DecisionTests(unittest.TestCase):
    def test_incluido(self):
        d = screening.decide({"parecer_final": "INCLUIDO"}, protocol_ok=True)
        self.assertEqual(d, "INCLUIDO")

    def test_excluido_com_protocolo_valido(self):
        d = screening.decide({"parecer_final": "EXCLUIDO"}, protocol_ok=True)
        self.assertEqual(d, "EXCLUIDO")

    def test_sem_protocolo_valido_nunca_exclui(self):
        """Sem critérios escritos não existe base para excluir ninguém."""
        d = screening.decide({"parecer_final": "EXCLUIDO"}, protocol_ok=False)
        self.assertEqual(d, "REVISÃO MANUAL")

    def test_variacoes_de_grafia_sao_aceitas(self):
        for texto in ("incluido", "INCLUÍDO", "Incluir", "inclusao"):
            with self.subTest(texto=texto):
                self.assertEqual(screening.decide({"parecer_final": texto}, protocol_ok=True),
                                 "INCLUIDO")

    def test_parecer_ausente_ou_estranho_vira_revisao_manual(self):
        for payload in ({}, {"parecer_final": ""}, {"parecer_final": "talvez"}):
            with self.subTest(payload=payload):
                self.assertEqual(screening.decide(payload, protocol_ok=True), "REVISÃO MANUAL")


class ConfidenceTests(unittest.TestCase):
    def test_numeros_passam(self):
        self.assertEqual(screening.normalize_confidence(95), 95)
        self.assertEqual(screening.normalize_confidence("87"), 87)
        self.assertEqual(screening.normalize_confidence(87.6), 87)

    def test_fora_da_faixa_e_grampeado(self):
        self.assertEqual(screening.normalize_confidence(150), 100)
        self.assertEqual(screening.normalize_confidence(-5), 0)

    def test_rotulos_textuais_viram_none_em_vez_de_zero(self):
        """'BAIXO' não é 0%. Dizer que é inventa um número que ninguém deu."""
        for rotulo in ("BAIXO", "ALTO", "MODERADO", "", None):
            with self.subTest(rotulo=rotulo):
                self.assertIsNone(screening.normalize_confidence(rotulo))


class ProtocolQuestionsTests(unittest.TestCase):
    def test_encontra_as_perguntas_declaradas_no_protocolo(self):
        protocolo = """
        ETAPA 1
        Q1: O estudo é em humanos?
        Q2: Há grupo controle?
        ETAPA 2
        Q3: Mede desfecho genético?
        """
        self.assertEqual(screening.protocol_questions(protocolo), ["Q1", "Q2", "Q3"])

    def test_passa_de_quinze_sem_perder_nenhuma(self):
        """O limite fixo em Q15 descartava as seguintes em silêncio."""
        protocolo = "\n".join(f'"Q{i}": "S",' for i in range(1, 21))
        perguntas = screening.protocol_questions(protocolo)
        self.assertIn("Q20", perguntas)
        self.assertEqual(len(perguntas), 20)

    def test_ordena_por_numero_e_nao_alfabeticamente(self):
        protocolo = "Q10: a\nQ2: b\nQ1: c"
        self.assertEqual(screening.protocol_questions(protocolo), ["Q1", "Q2", "Q10"])

    def test_sem_perguntas_declaradas_cai_num_padrao_util(self):
        perguntas = screening.protocol_questions("Protocolo em prosa, sem chaves Q.")
        self.assertEqual(perguntas, [f"Q{i}" for i in range(1, 16)])

    def test_nao_confunde_palavras_que_comecam_com_q(self):
        self.assertEqual(screening.protocol_questions("Qualidade e Quantidade do estudo."),
                         [f"Q{i}" for i in range(1, 16)])


if __name__ == "__main__":
    unittest.main()
