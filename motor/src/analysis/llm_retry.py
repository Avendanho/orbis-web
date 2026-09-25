"""Repetição com espera para as chamadas de LLM.

O problema que isto resolve: a triagem roda com vários workers contra o mesmo
provedor, então 429 e 5xx são rotina. Sem repetição, cada um desses virava um
"REVISÃO MANUAL" gravado no banco — ou seja, uma falha de infraestrutura
entrando no resultado da revisão sistemática como se fosse uma decisão.

A política é a mesma do lado do download (``src/download/http_retry.py``):
espera exponencial com jitter, e só para o que pode melhorar se pedir de novo.
Credencial inválida, prompt grande demais e recusa de conteúdo não melhoram —
insistir neles só gasta cota e tempo.

Quando desiste, levanta ``LlmCallFailed``, que é um tipo próprio justamente
para o chamador conseguir distinguir "a chamada falhou" de "o modelo decidiu".
"""
from __future__ import annotations

import random
import time

DEFAULT_ATTEMPTS = 4
BASE_DELAY = 1.5     # segundos, dobrados a cada tentativa
MAX_DELAY = 30.0

# Sinais de "agora não" — o pedido é válido, o serviço é que não pôde atender.
_RETRYABLE_MARKERS = (
    "429", "resource_exhausted", "rate limit", "rate_limit", "quota",
    "too many requests", "500", "502", "503", "504", "529",
    "internal server error", "bad gateway", "service unavailable",
    "overloaded", "timeout", "timed out", "connection reset",
    "connection error", "temporarily unavailable",
)

# Sinais de "não" — repetir não muda a resposta.
_PERMANENT_MARKERS = (
    "401", "403", "unauthenticated", "unauthorized", "permission denied",
    "api key not valid", "invalid api key", "invalid_api_key",
    "maximum token", "context length", "too long", "content filter",
    "safety", "blocked", "not found", "404",
)


class LlmCallFailed(Exception):
    """A chamada não foi concluída. Não é um parecer de triagem."""

    def __init__(self, message: str, *, attempts: int):
        super().__init__(message)
        self.attempts = attempts


def is_retryable(exc: BaseException) -> bool:
    """True quando pedir de novo tem chance real de dar certo."""
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    # Um JSON que não deu para ler pode ser uma geração ruim pontual, mas é o
    # chamador que decide repetir isso — aqui não conta como transitório.
    if isinstance(exc, ValueError):
        return False

    texto = f"{type(exc).__name__}: {exc}".lower()
    if any(marca in texto for marca in _PERMANENT_MARKERS):
        return False
    return any(marca in texto for marca in _RETRYABLE_MARKERS)


def backoff_delay(attempt: int) -> float:
    """Espera antes da tentativa ``attempt`` (base 0), com jitter total."""
    janela = min(MAX_DELAY, BASE_DELAY * (2 ** attempt))
    return random.uniform(janela / 2, janela)


def call_with_retry(fn, *, attempts: int = DEFAULT_ATTEMPTS):
    """Executa ``fn`` repetindo o que vale a pena. Levanta LlmCallFailed."""
    ultima: BaseException | None = None
    tentativas_feitas = 0

    for tentativa in range(max(1, attempts)):
        tentativas_feitas = tentativa + 1
        try:
            return fn()
        except Exception as exc:
            ultima = exc
            if not is_retryable(exc) or tentativa == attempts - 1:
                break
            time.sleep(backoff_delay(tentativa))

    erro = LlmCallFailed(
        f"Chamada ao LLM falhou após {tentativas_feitas} tentativa(s): {ultima}",
        attempts=tentativas_feitas,
    )
    raise erro from ultima
