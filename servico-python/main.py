"""Motor completo de recuperação e triagem, para o ORBIS usar quando disponível.

Por que existe
--------------
O ORBIS roda em Cloudflare Worker, um isolate V8: Python não executa lá. O que
o Worker consegue fazer sozinho — Unpaywall, bucket do PMC, PubMed, OpenAlex,
identidade por metadados e triagem sobre título e resumo — já está implementado
em TypeScript e funciona no site público, sem depender deste serviço.

O que **não** roda em Worker é o que precisa de biblioteca nativa:

* ler o texto de dentro do PDF (PyMuPDF);
* validar identidade pelo conteúdo do arquivo, e não só pelos metadados;
* triagem sobre o texto completo, em vez de título e resumo.

Este serviço expõe exatamente isso. O ORBIS o detecta e, quando responde,
oferece as capacidades extras; quando não, segue funcionando como está.

Como rodar
----------
    pip install -r requirements.txt
    uvicorn main:app --host 127.0.0.1 --port 8900

Não guarda credencial nem estado: recebe o que precisa em cada requisição.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import baixar as motor_baixar

# O pipeline Python já existente entra pelo caminho que o operador apontar.
# O motor vive em `motor/src/download`, ao lado deste serviço. `ORBIS_PIPELINE`
# continua existindo para apontar outro caminho, mas não é mais obrigatório.
_PADRAO = Path(__file__).resolve().parent.parent / "motor" / "src" / "download"
PIPELINE = os.environ.get("ORBIS_PIPELINE", "").strip() or (str(_PADRAO) if _PADRAO.is_dir() else "")
if PIPELINE and Path(PIPELINE).is_dir():
    sys.path.insert(0, PIPELINE)

# Onde o modo "baixar" grava os PDFs. `ORBIS_DATA_DIR` é o mesmo diretório de
# dados que o motor já usa (padrão: a pasta `motor/`).
PASTA_PDFS = Path(os.environ.get("ORBIS_DATA_DIR") or Path(__file__).resolve().parent.parent / "motor") / "pdfs"

# O fetch abre muitas conexões por artigo; mais que 4 em paralelo só gera
# bloqueio (HTTP 429) nas fontes.
_vagas = threading.BoundedSemaphore(4)

app = FastAPI(title="ORBIS — motor de recuperação", version="1.0")

# O Worker chama de outra origem; sem isto o navegador bloqueia.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.environ.get("ORBIS_ORIGINS", "*").split(",")],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _importar(nome: str):
    """Importa um módulo do pipeline, explicando o que falta quando não dá."""
    try:
        return __import__(nome)
    except ImportError as exc:
        raise HTTPException(
            503,
            f"O módulo '{nome}' do pipeline não está disponível. "
            f"Defina ORBIS_PIPELINE apontando para src/download. Detalhe: {exc}",
        ) from exc


def _baixar_pdf(url: str) -> bytes:
    """Baixa e confere que é mesmo um PDF antes de qualquer processamento."""
    import urllib.request

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ORBIS-motor/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            dados = r.read(60 * 1024 * 1024)
    except Exception as exc:
        raise HTTPException(502, f"Não foi possível baixar o arquivo: {exc}") from exc
    if dados[:5] != b"%PDF-":
        raise HTTPException(422, "O endereço não devolveu um PDF.")
    return dados


@app.get("/saude")
def saude():
    """O ORBIS chama isto para saber se o motor completo está no ar."""
    recursos, faltando = [], []
    for nome, recurso in (("fetch", "recuperacao"), ("identity", "identidade"), ("pmc_s3", "pmc")):
        try:
            __import__(nome)
            recursos.append(recurso)
        except ImportError:
            faltando.append(recurso)
    try:
        __import__("pymupdf")
        recursos.append("texto_do_pdf")
    except ImportError:
        try:
            __import__("fitz")
            recursos.append("texto_do_pdf")
        except ImportError:
            faltando.append("texto_do_pdf")
    if "recuperacao" in recursos and "texto_do_pdf" in recursos:
        recursos.append("download_completo")
    return {"ok": True, "pipeline": PIPELINE or None, "recursos": recursos, "faltando": faltando,
            "pasta_pdfs": str(PASTA_PDFS.resolve())}


class PedidoResolver(BaseModel):
    doi: str
    timeout: int = 30


@app.post("/resolver")
def resolver(p: PedidoResolver):
    """Localiza cópias abertas usando a cadeia completa de fontes do pipeline."""
    fetch = _importar("fetch")
    try:
        urls, meta = fetch.try_unpaywall(p.doi, timeout=p.timeout)
    except Exception as exc:
        raise HTTPException(502, f"Falha ao consultar as fontes: {exc}") from exc
    return {"doi": p.doi, "pdfUrls": [urls] if isinstance(urls, str) and urls else [], "meta": meta}


class PedidoTexto(BaseModel):
    url: str
    max_chars: int = 200_000


@app.post("/texto")
def texto(p: PedidoTexto):
    """Extrai o texto de um PDF. É a capacidade central que o Worker não tem."""
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError as exc:
            raise HTTPException(503, "PyMuPDF não está instalado neste serviço.") from exc

    dados = _baixar_pdf(p.url)
    try:
        doc = pymupdf.open(stream=dados, filetype="pdf")
        conteudo = "\n".join(doc[i].get_text() for i in range(doc.page_count))
    except Exception as exc:
        raise HTTPException(422, f"O PDF não pôde ser lido: {exc}") from exc

    return {"paginas": doc.page_count, "chars": len(conteudo), "texto": conteudo[: p.max_chars]}


class PedidoIdentidade(BaseModel):
    url: str
    doi: str
    titulo: str = ""
    autor: str = ""
    periodico: str = ""
    ano: str = ""


@app.post("/identidade")
def identidade(p: PedidoIdentidade):
    """Confere pelo CONTEÚDO do PDF, não pelos metadados da fonte.

    É a verificação mais forte que existe: procura o DOI impresso dentro do
    arquivo. Foi ela que, numa auditoria de 449 PDFs, encontrou os que tinham
    metadados corretos sem serem o artigo — material suplementar, formulário
    de submissão e caderno de resumos.

    Recebe a URL e não o texto porque `extract_pdf_identity` lê os bytes: ele
    olha os metadados do documento além da primeira página, e o texto já
    extraído perderia essa informação.
    """
    identity = _importar("identity")
    dados = _baixar_pdf(p.url)
    try:
        pdf_identity = identity.extract_pdf_identity(dados)
        veredito = identity.validate_article_identity(
            {
                "doi": p.doi,
                "title": p.titulo,
                "author": p.autor,
                "journal": p.periodico,
                "year": p.ano,
            },
            pdf_identity=pdf_identity,
            record_doi_matched=True,
        )
    except Exception as exc:
        raise HTTPException(500, f"Falha ao validar a identidade: {exc}") from exc
    return {**veredito, "pdf_identity": pdf_identity}


class PedidoBaixar(BaseModel):
    doi: str
    projeto: str
    modo: str
    titulo: str = ""
    autor: str = ""
    ano: str = ""
    periodico: str = ""
    prazo: int = 90


@app.post("/baixar")
def rota_baixar(p: PedidoBaixar):
    """Baixa um artigo pela cadeia completa, no modo que o pesquisador escolheu.

    Não achar o PDF é resultado (200 com ``ok: false``), não falha do serviço.
    Erro HTTP fica para o que o ORBIS precisa tratar diferente: entrada
    inválida (400) e pasta inutilizável (507, que pausa o lote).
    """
    fetch = _importar("fetch")
    identity = _importar("identity")
    with _vagas:
        try:
            return motor_baixar.baixar_artigo(
                doi=p.doi, projeto=p.projeto, modo=p.modo,
                esperado={"title": p.titulo, "author": p.autor, "year": p.ano, "journal": p.periodico},
                prazo=max(10, min(p.prazo, 300)), pasta_pdfs=PASTA_PDFS,
                fetch_mod=fetch, identity_mod=identity, extrair=motor_baixar.extrair_texto,
            )
        except motor_baixar.PedidoInvalido as exc:
            raise HTTPException(400, str(exc)) from exc
        except motor_baixar.DiscoIndisponivel as exc:
            raise HTTPException(507, str(exc)) from exc
