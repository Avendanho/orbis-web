"""Texto de um PDF em Markdown (``pymupdf4llm``), com prazo e plano B.

O Markdown preserva seções, títulos e tabelas, que o texto simples achata — e
é isso que a IA lê na análise PCC. O ``pymupdf4llm`` roda num processo filho:
num PDF patológico ele pode levar minutos, e só um processo à parte pode ser
interrompido no prazo. Se falhar ou estourar, vale o texto simples do PyMuPDF,
como antes: o artigo entra assim mesmo.

As imagens (quando há pasta para elas) ficam em ``<nome>_imagens/``, ao lado
do ``.md``, e os links no Markdown são relativos a ele.

    python extracao.py <pdf> [<pasta_imagens>]   (uso interno: é o processo filho)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PRAZO_PADRAO = 90
_DESLIGADO = {"0", "false", "nao", "não", "off", "no", ""}


def _ligado(nome: str, padrao: bool) -> bool:
    valor = os.environ.get(nome)
    return padrao if valor is None else valor.strip().lower() not in _DESLIGADO


def opcoes() -> dict:
    """Lidas a cada artigo: a tela de Configurações muda o ambiente do motor."""
    try:
        prazo = int(os.environ.get("ORBIS_EXTRACAO_PRAZO") or PRAZO_PADRAO)
    except ValueError:
        prazo = PRAZO_PADRAO
    return {"markdown": _ligado("ORBIS_EXTRAIR_MARKDOWN", True), "imagens": _ligado("ORBIS_SALVAR_IMAGENS", True),
            "prazo": max(5, min(prazo, 600))}


def _pymupdf():
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf
    return pymupdf


def texto_simples(caminho: Path) -> tuple[str, int]:
    doc = _pymupdf().open(caminho)
    try:
        return "\n".join(doc[i].get_text() for i in range(doc.page_count)), doc.page_count
    finally:
        doc.close()


def markdown_no_filho(caminho: Path, pasta_imagens: Path | None, prazo: int, comando: list[str] | None = None) -> str:
    """Roda este arquivo como processo filho; ``subprocess`` o mata no prazo."""
    cmd = comando or [sys.executable, str(Path(__file__).resolve()), str(Path(caminho).resolve()),
                      *([pasta_imagens.name] if pasta_imagens else [])]
    with tempfile.TemporaryDirectory(prefix="orbis-md-") as vazio:
        # O filho grava as imagens relativas à pasta de trabalho: é assim que os
        # links saem relativos. Sem pasta de imagens, trabalha numa pasta vazia.
        cwd = pasta_imagens.parent if pasta_imagens else vazio
        try:
            r = subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=prazo,
                               env={**os.environ, "PYTHONUTF8": "1"})
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(prazo) from exc
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace")[-500:] or f"código {r.returncode}")
    return r.stdout.decode("utf-8", "replace")


def extrair(caminho: Path, pasta_imagens: Path | None = None, *, markdown: bool = True,
            prazo: int = PRAZO_PADRAO, rodar=markdown_no_filho) -> dict:
    caminho = Path(caminho)
    if pasta_imagens:
        # Baixar de novo o mesmo artigo substitui as imagens, não acumula.
        shutil.rmtree(pasta_imagens, ignore_errors=True)
    aviso = None
    if markdown:
        try:
            texto = rodar(caminho, pasta_imagens, prazo)
            paginas = _paginas(caminho)
            imagens = _contar(pasta_imagens)
            return {"texto": texto, "formato": "markdown", "paginas": paginas, "imagens": imagens}
        except TimeoutError:
            aviso = f"A extração em Markdown passou de {prazo} s; foi usado o texto simples."
        except Exception:
            aviso = "Falha na extração em Markdown; foi usado o texto simples."
        if pasta_imagens:
            shutil.rmtree(pasta_imagens, ignore_errors=True)
    texto, paginas = texto_simples(caminho)
    resposta = {"texto": texto, "formato": "texto", "paginas": paginas, "imagens": 0}
    if aviso:
        resposta["aviso"] = aviso
    return resposta


def extrair_conforme_opcoes(caminho: Path, pasta_imagens: Path | None) -> dict:
    o = opcoes()
    return extrair(caminho, pasta_imagens if o["imagens"] else None, markdown=o["markdown"], prazo=o["prazo"])


def _paginas(caminho: Path) -> int:
    doc = _pymupdf().open(caminho)
    try:
        return doc.page_count
    finally:
        doc.close()


def _contar(pasta: Path | None) -> int:
    if not pasta or not pasta.is_dir():
        return 0
    n = sum(1 for f in pasta.iterdir() if f.is_file())
    if not n:
        pasta.rmdir()
    return n


if __name__ == "__main__":
    import pymupdf4llm

    pdf, *resto = sys.argv[1:]
    md = pymupdf4llm.to_markdown(pdf, write_images=bool(resto), image_path=resto[0] if resto else "",
                                 image_format="png", show_progress=False)
    sys.stdout.buffer.write(md.encode("utf-8"))
