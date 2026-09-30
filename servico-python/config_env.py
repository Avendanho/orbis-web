"""Leitura e escrita do `motor/.env` pela tela de Configurações do ORBIS.

A carga do arquivo na partida continua em `main.carregar_env_do_motor` (o
terminal prevalece e o texto do modelo não conta como chave). Aqui fica só a
parte da tela: mostrar o que está preenchido, sem entregar segredos, e gravar
sem desmontar o modelo — comentários e ordem ficam onde estavam.
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path

ARQUIVO = Path(__file__).resolve().parent.parent / "motor" / ".env"

# chave -> (é segredo, exige reiniciar o motor)
# "Exige reiniciar" = o motor lê a variável uma vez, na importação (EMAIL,
# CORE_API_KEY e SPRINGER_API_KEY em fetch.py). As demais valem na próxima
# chamada. Acesso institucional (EZproxy, proxy, sessão) fica fora de propósito:
# só no .env. Um teste do ORBIS confere esta lista contra o catálogo da tela.
PERMITIDAS: dict[str, tuple[bool, bool]] = {
    "ELSEVIER_API_KEY": (True, False),
    "ELSEVIER_INST_TOKEN": (True, False),
    "ORBIS_EXTRAIR_MARKDOWN": (False, False),
    "ORBIS_SALVAR_IMAGENS": (False, False),
    "ORBIS_EXTRACAO_PRAZO": (False, False),
    "UNPAYWALL_EMAIL": (False, True),
    "OPENALEX_API_KEY": (True, False),
    "SEMANTIC_SCHOLAR_API_KEY": (True, False),
    "CORE_API_KEY": (True, True),
    "SPRINGER_API_KEY": (True, True),
    "WILEY_TDM_TOKEN": (True, False),
    "PAPER_FETCH_NO_SCIHUB": (False, False),
    "PAPER_FETCH_NO_LIBGEN": (False, False),
    "PAPER_FETCH_NO_WAYBACK": (False, False),
    "PAPER_FETCH_NO_OSTI": (False, False),
    "PAPER_FETCH_NO_SCHOLAR": (False, False),
    "PAPER_FETCH_NO_FATCAT": (False, False),
    "PAPER_FETCH_NO_BASE": (False, False),
    "PAPER_FETCH_NO_OPENALEX_CONTENT": (False, False),
}

_LINHA = re.compile(r"^\s*(#\s*)?(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=(.*)$")
_PRECISA_ASPAS = re.compile(r"[\s#\"'\\]")


def _arquivo(arquivo: Path | None) -> Path:
    return arquivo or ARQUIVO


def _decodificar(bruto: str) -> str:
    v = bruto.strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        return v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1]
    return v


def _codificar(valor: str) -> str:
    if not _PRECISA_ASPAS.search(valor):
        return valor
    return '"' + valor.replace("\\", "\\\\").replace('"', '\\"') + '"'


def ler(arquivo: Path | None = None) -> dict[str, str]:
    f = _arquivo(arquivo)
    if not f.exists():
        return {}
    out: dict[str, str] = {}
    for linha in f.read_text(encoding="utf-8").splitlines():
        m = _LINHA.match(linha)
        if m and not m.group(1):
            out[m.group(2)] = _decodificar(m.group(3))
    return out


def mascarar(valor: str) -> str:
    if not valor:
        return ""
    return "••••" if len(valor) < 12 else "••••" + valor[-4:]


def visao() -> dict[str, dict]:
    # Do ambiente, não do arquivo: é o que o motor está usando de fato (o texto
    # do modelo, "sua_chave_…", nunca chega ao ambiente).
    out = {}
    for k, (segredo, _r) in PERMITIDAS.items():
        v = os.environ.get(k, "").strip()
        out[k] = {"preenchido": bool(v), "valor": mascarar(v) if segredo else v}
    return out


def gravar(mudancas: dict[str, str | None], arquivo: Path | None = None) -> list[str]:
    # Valida tudo antes de tocar no arquivo: ou grava tudo, ou nada.
    for k, v in mudancas.items():
        if k not in PERMITIDAS:
            raise ValueError(f"{k} não configurável pela tela.")
        if v is not None and re.search(r"[\r\n\0]", v):
            raise ValueError(f"{k}: não pode ter quebra de linha.")
    f = _arquivo(arquivo)
    linhas = f.read_text(encoding="utf-8").splitlines() if f.exists() else []
    pendentes = dict(mudancas)
    saida: list[str] = []
    # 1ª passada: linhas ativas (substituir ou apagar).
    for linha in linhas:
        m = _LINHA.match(linha)
        if m and not m.group(1) and m.group(2) in pendentes:
            v = pendentes.pop(m.group(2))
            if v:
                saida.append(f"{m.group(2)}={_codificar(v)}")
            continue
        saida.append(linha)
    # 2ª passada: a linha comentada do modelo vira a ativa, no mesmo lugar.
    for i, linha in enumerate(saida):
        m = _LINHA.match(linha)
        if m and m.group(1) and pendentes.get(m.group(2)):
            k = m.group(2)
            saida[i] = f"{k}={_codificar(pendentes.pop(k))}"
    # O que sobrou (e não é apagar) vai para o fim.
    for k, v in pendentes.items():
        if v:
            saida.append(f"{k}={_codificar(v)}")
    # O .env guarda chaves: a cópia nova herda a permissão do arquivo atual (um
    # chmod 600 feito à mão continua valendo) e, se o arquivo é novo, só o dono
    # lê. O temporário já nasce assim, sem ficar legível nem por um instante.
    modo = stat.S_IMODE(f.stat().st_mode) if f.exists() else 0o600
    tmp = f.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, modo)
    with os.fdopen(fd, "w", encoding="utf-8") as saida_arq:
        saida_arq.write("\n".join(saida) + "\n")
    os.chmod(tmp, modo)  # o umask pode ter tirado bits que o arquivo tinha
    tmp.replace(f)
    for k, v in mudancas.items():
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)
    return sorted(k for k in mudancas if PERMITIDAS[k][1])
