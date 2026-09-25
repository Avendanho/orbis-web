"""Regras de triagem, isoladas de I/O.

Tudo aqui é função pura: entra texto, sai veredito. É de propósito — esta é a
lógica que decide o que entra numa revisão sistemática, e lógica assim precisa
ser verificável sem banco, sem rede e sem chave de API.

A ideia que organiza o módulo é a separação entre **falha técnica** e
**decisão científica**. Um PDF ilegível, uma chamada de API que caiu e um
artigo que o modelo leu e avaliou são três situações diferentes. Tratar as
duas primeiras como "REVISÃO MANUAL" faz com que entrem na contagem PRISMA
como se fossem triagem, o que é falso: ninguém triou nada.
"""
from __future__ import annotations

import json
import re

# Abaixo disso não há artigo suficiente para sustentar um parecer: é capa,
# errata, página de rosto ou sobra de extração.
MIN_SCREENABLE_CHARS = 300

# Marcas que `pdf_processor` escreve em content.md quando a extração falha.
# Ficam ancoradas no início do texto porque é lá que elas aparecem — procurar
# no corpo inteiro reprovaria artigos que discutem erros de medição.
_EXTRACTION_FAILURE_PREFIXES = (
    "erro: timeout ao processar o pdf",
    "erro na extração pymupdf4llm",
    "erro na extracao pymupdf4llm",
    "não foi possível extrair o texto do pdf",
    "nao foi possivel extrair o texto do pdf",
)

_QUESTION_RE = re.compile(r"\bQ(\d{1,3})\b")
_DEFAULT_QUESTIONS = [f"Q{i}" for i in range(1, 16)]


def is_screenable(text: str | None, meta: dict | None = None) -> tuple[bool, str | None]:
    """O texto pode ser enviado ao modelo? Devolve (pode, motivo_da_recusa).

    O motivo é ``None`` quando pode. Quando não pode, é um código estável
    (``extracao_falhou``, ``sem_texto``, ``texto_insuficiente``) que o chamador
    grava como falha técnica — nunca como decisão de triagem.
    """
    meta = meta or {}
    content = (text or "").strip()

    if not content:
        return False, "sem_texto"

    # O metadado é a fonte primária; o conteúdo é a rede de segurança para
    # extrações feitas por uma versão anterior, que não gravava text_quality.
    if str(meta.get("text_quality", "")).upper() == "ERROR":
        return False, "extracao_falhou"

    head = content[:200].lower()
    if any(head.startswith(mark) for mark in _EXTRACTION_FAILURE_PREFIXES):
        return False, "extracao_falhou"

    if len(content) < MIN_SCREENABLE_CHARS:
        return False, "texto_insuficiente"

    return True, None


def parse_llm_json(result_text: str | None) -> dict:
    """Extrai o objeto JSON da resposta do modelo.

    Tolera o que é cosmético — cercas de código, um "Claro, segue:" antes,
    texto depois. Não tolera truncamento: um JSON cortado no meio, fechado à
    força, vira um objeto válido com metade dos campos faltando, e um parecer
    montado sobre isso parece íntegro sem ser. Nesse caso levanta ValueError
    para que o chamador trate como falha e tente de novo.
    """
    stripped = (result_text or "").strip()
    if not stripped:
        raise ValueError("Resposta vazia do LLM.")

    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```\s*$", "", stripped)
        stripped = stripped.strip()

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None

    if parsed is None:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(stripped[start:end + 1])
            except json.JSONDecodeError:
                parsed = None

    if not isinstance(parsed, dict):
        raise ValueError("Não foi possível extrair um objeto JSON da resposta do LLM.")
    return parsed


def decide(result_json: dict, *, protocol_ok: bool) -> str:
    """Converte o parecer do modelo em INCLUIDO / EXCLUIDO / REVISÃO MANUAL.

    Sem protocolo válido, EXCLUIDO é rebaixado para REVISÃO MANUAL: sem
    critérios escritos não existe base para excluir ninguém, e essa é a direção
    segura do erro — um falso incluído é corrigido na leitura seguinte, um
    falso excluído desaparece da revisão sem deixar rastro.
    """
    parecer = str((result_json or {}).get("parecer_final") or "").upper()
    if "INCLU" in parecer:
        return "INCLUIDO"
    if "EXCLU" in parecer and protocol_ok:
        return "EXCLUIDO"
    return "REVISÃO MANUAL"


def normalize_confidence(value) -> int | None:
    """Confiança como inteiro de 0 a 100, ou None quando não é um número.

    Rótulos como "BAIXO" viram None em vez de 0: o modelo não disse zero, e
    escrever 0% inventa um dado que ninguém produziu.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return max(0, min(100, int(value)))
    if isinstance(value, str):
        texto = value.strip().rstrip("%").strip()
        try:
            return max(0, min(100, int(float(texto))))
        except ValueError:
            return None
    return None


def protocol_questions(protocol_text: str | None) -> list[str]:
    """As chaves Q que o protocolo realmente declara, em ordem numérica.

    Antes a lista era fixa em Q1..Q15, então um protocolo com Q16 perdia a
    pergunta sem aviso. Quando o protocolo não declara nenhuma (está em prosa),
    volta-se ao intervalo padrão, que é o que o gerador de protocolos produz.
    """
    numeros = {int(n) for n in _QUESTION_RE.findall(protocol_text or "")}
    if not numeros:
        return list(_DEFAULT_QUESTIONS)
    return [f"Q{n}" for n in sorted(numeros)]
