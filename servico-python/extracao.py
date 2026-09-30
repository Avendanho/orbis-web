"""Texto de um PDF em Markdown (``pymupdf4llm``), com prazo e plano B.

O Markdown preserva seções, títulos e tabelas, que o texto simples achata — e
é isso que a IA lê na análise PCC. O ``pymupdf4llm`` roda em processos à
parte: num PDF patológico ele pode levar minutos, e só um processo pode ser
interrompido no prazo. Os processos ficam vivos entre um artigo e outro —
importar o ``pymupdf4llm`` (e o modelo de layout) custa mais que extrair um
artigo — e o que estoura o prazo é morto e substituído. Se falhar ou estourar,
vale o texto simples do PyMuPDF, como antes: o artigo entra assim mesmo.

As imagens (quando há pasta para elas) ficam em ``<nome>_imagens/``, ao lado
do ``.md``, e os links no Markdown são relativos a ele.
"""
from __future__ import annotations

import multiprocessing
import os
import shutil
import tempfile
import threading
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


def _servir(conn) -> None:
    """Processo de extração: importa o pymupdf4llm uma vez e atende um artigo por vez."""
    import pymupdf4llm

    while True:
        try:
            pedido = conn.recv()
        except EOFError:
            return
        if pedido is None:
            return
        pdf, cwd, imagens = pedido
        try:
            # As imagens são gravadas relativas à pasta de trabalho: é assim que
            # os links no Markdown saem relativos ao .md.
            os.chdir(cwd)
            md = pymupdf4llm.to_markdown(pdf, write_images=bool(imagens), image_path=imagens,
                                         image_format="png", show_progress=False)
            conn.send((True, md))
        except Exception as exc:
            conn.send((False, f"{type(exc).__name__}: {exc}"[:500]))


class Trabalhadores:
    """Processos de extração reaproveitados, no máximo ``maximo`` ao mesmo tempo.

    ``spawn`` e não ``fork``: o motor roda dentro do uvicorn, com threads, e um
    fork copiaria travas no meio do uso. É também o que o Windows e o macOS usam.
    """

    def __init__(self, maximo: int = 4):
        self._ctx = multiprocessing.get_context("spawn")
        self._vagas = threading.BoundedSemaphore(maximo)
        self._trava = threading.Lock()
        self._livres: list = []
        self._vivos: set = set()

    def pids(self) -> list[int]:
        with self._trava:
            return sorted(p.pid for p, _ in self._vivos if p.is_alive())

    def _pegar(self):
        with self._trava:
            while self._livres:
                t = self._livres.pop()
                if t[0].is_alive():
                    return t
                self._vivos.discard(t)
        pai, filho = self._ctx.Pipe()
        proc = self._ctx.Process(target=_servir, args=(filho,), daemon=True, name="orbis-extracao")
        proc.start()
        filho.close()
        with self._trava:
            self._vivos.add((proc, pai))
        return proc, pai

    def _matar(self, t) -> None:
        proc, conn = t
        # Morto e esperado antes de seguir: nada mais é gravado na pasta de
        # imagens depois que ela for apagada.
        proc.kill()
        proc.join(10)
        conn.close()
        with self._trava:
            self._vivos.discard(t)

    def markdown(self, caminho: Path, pasta_imagens: Path | None, prazo: float) -> str:
        with self._vagas:
            t = self._pegar()
            try:
                t[1].send((str(Path(caminho).resolve()),
                           str(pasta_imagens.parent) if pasta_imagens else tempfile.gettempdir(),
                           pasta_imagens.name if pasta_imagens else ""))
                if not t[1].poll(prazo):
                    raise TimeoutError(prazo)
                ok, valor = t[1].recv()
            except BaseException:
                # Prazo estourado, processo que caiu ou pedido interrompido: esse
                # processo não atende mais ninguém.
                self._matar(t)
                raise
            with self._trava:
                self._livres.append(t)
        if not ok:
            raise RuntimeError(valor)
        return valor

    def encerrar(self) -> None:
        with self._trava:
            todos, self._livres = list(self._vivos), []
        for t in todos:
            self._matar(t)


# Tantos quanto os downloads simultâneos do motor (main.py).
TRABALHADORES = Trabalhadores(maximo=4)


def extrair(caminho: Path, pasta_imagens: Path | None = None, *, markdown: bool = True,
            prazo: float = PRAZO_PADRAO, rodar=None) -> dict:
    caminho = Path(caminho)
    rodar = rodar or TRABALHADORES.markdown
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

