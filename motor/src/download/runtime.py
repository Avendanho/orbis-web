"""Ponte para o substrato compartilhado que vive em ``fetch.py``.

Por que isto existe
-------------------
``fetch.py`` guarda o substrato da execução: o cliente HTTP (``_get_json``), o
emissor de eventos (``_progress``), o orçamento de tempo por artigo e alguns
contadores de estado (``_base_blocked``, ``_fatcat_failures``). Os resolvers
de fonte que foram extraídos para módulos próprios precisam desse substrato.

O caminho óbvio — ``from fetch import _get_json`` — teria dois defeitos:

* **Import circular**: ``fetch`` importa os resolvers, que importariam
  ``fetch`` de volta no momento da carga.
* **Quebraria o monkeypatch.** Os testes fazem ``patch.object(fetch,
  "_get_json", ...)`` e ``run_parallel`` faz ``fetch_module._format =
  "silent"``. Um ``from ... import`` congela o valor no momento da carga, então
  a substituição feita depois não seria vista por quem já importou.

O ``__getattr__`` de módulo (PEP 562) resolve os dois: o atributo é buscado em
``fetch`` **no momento do uso**, não no da importação. Assim o ciclo se desfaz
(o import só acontece na primeira chamada, com ``fetch`` já carregado) e todo
monkeypatch continua valendo, porque a busca sempre passa pelo módulo real.

Uso::

    import runtime

    def try_alguma_fonte(doi, *, timeout):
        dados = runtime._get_json(url, timeout=timeout)   # respeita o patch
        runtime._progress("source_hit", doi=doi)
        runtime.set_state("_base_blocked", True)          # escrita em fetch
"""
from __future__ import annotations

import importlib


def _fetch_module():
    return importlib.import_module("fetch")


def __getattr__(name: str):
    """Qualquer nome não definido aqui é buscado em ``fetch`` na hora do uso."""
    try:
        return getattr(_fetch_module(), name)
    except AttributeError as exc:  # pragma: no cover - erro de programação
        raise AttributeError(f"'{name}' não existe em fetch.py") from exc


def set_state(name: str, value) -> None:
    """Escreve um global de ``fetch.py``.

    Necessário porque ``runtime.x = valor`` gravaria neste módulo, e não no
    ``fetch``, que é onde os testes e o ``run_parallel`` leem esse estado.
    """
    setattr(_fetch_module(), name, value)
