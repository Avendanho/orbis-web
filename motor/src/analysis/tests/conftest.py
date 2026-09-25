"""Carrega o `main` da análise sob um nome que ninguém mais disputa.

As três etapas rodam como scripts soltos, cada uma com o próprio diretório no
sys.path, e duas têm um módulo chamado `main` (`src/search/main.py` e
`src/analysis/main.py`). Cada suíte isolada funciona; na suíte inteira, quem
importar primeiro ocupa o nome `main` em sys.modules e a outra recebe o módulo
errado, com um erro enganoso do tipo "module 'main' has no attribute 'report'".

Apenas limpar sys.modules não resolve: os conftest são carregados no começo da
coleta, antes dos módulos de teste, então o nome é reocupado depois. Aqui o
módulo é carregado pelo caminho do arquivo e registrado como `analysis_main`,
que é só nosso. Os demais módulos da etapa (`config`, `database`, `screening`,
`llm_retry`) têm nome único no projeto e seguem importáveis normalmente.

A correção de fundo é transformar as etapas em pacotes de verdade — mexe em
`backend.py` e `start.py`, e merece leva própria.
"""
import importlib.util
import sys
from pathlib import Path

ANALYSIS_DIR = Path(__file__).resolve().parents[1]

# Precisa vir primeiro para que os imports internos de main.py (`config`,
# `screening`, `llm_retry`) resolvam para os desta etapa.
if str(ANALYSIS_DIR) in sys.path:
    sys.path.remove(str(ANALYSIS_DIR))
sys.path.insert(0, str(ANALYSIS_DIR))


def _carregar_analysis_main():
    if "analysis_main" in sys.modules:
        return sys.modules["analysis_main"]
    spec = importlib.util.spec_from_file_location("analysis_main", ANALYSIS_DIR / "main.py")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["analysis_main"] = modulo
    spec.loader.exec_module(modulo)
    return modulo


_carregar_analysis_main()
