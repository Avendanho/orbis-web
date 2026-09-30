"""Leva as chaves de ``motor/.env`` ao ambiente de quem roda o motor pela CLI.

O serviço HTTP (``servico-python/main.py``) já fazia isso; a linha de comando
(``run_parallel.py``) e o benchmark não, e rodavam sem e-mail da Unpaywall nem
chave nenhuma mesmo com o ``.env`` preenchido.

Duas regras, as mesmas do serviço:

* o ambiente do terminal prevalece sobre o arquivo;
* um valor igual ao do ``.env.example`` é o texto do modelo
  ("sua_chave_…", "seuemail@exemplo.com"), não uma chave: mandá-lo às APIs só
  rende recusas (ou, pior, identifica o motor com o e-mail de outra pessoa).
"""
from __future__ import annotations

import os
from pathlib import Path

RAIZ_MOTOR = Path(__file__).resolve().parents[2]


def ler_env(arquivo: Path) -> dict[str, str]:
    valores: dict[str, str] = {}
    try:
        linhas = arquivo.read_text(encoding="utf-8").splitlines()
    except OSError:
        return valores
    for linha in linhas:
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.removeprefix("export ").split("=", 1)
        valores[chave.strip()] = valor.strip().strip("'\"")
    return valores


def carregar(raiz: Path = RAIZ_MOTOR) -> dict[str, list[str]]:
    """Aplica ``raiz/.env`` ao ``os.environ`` e diz o que fez com cada chave.

    Devolve ``{"aplicadas": [...], "modelo": [...]}``; ``modelo`` lista as
    chaves que ainda estão com o texto do exemplo, para o chamador avisar.
    """
    modelo = ler_env(raiz / ".env.example")
    aplicadas: list[str] = []
    placeholders: list[str] = []
    for chave, valor in ler_env(raiz / ".env").items():
        if not valor:
            continue
        if valor == modelo.get(chave):
            placeholders.append(chave)
            continue
        if chave not in os.environ:
            os.environ[chave] = valor
            aplicadas.append(chave)
    return {"aplicadas": aplicadas, "modelo": placeholders}
