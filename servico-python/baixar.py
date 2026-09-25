"""Baixa UM artigo pela cadeia completa do motor, em um de dois modos.

* ``baixar``: o PDF fica em ``<pasta_pdfs>/<projeto>/`` e cada artigo ganha uma
  linha no ``Relatório.txt`` dessa pasta — o mesmo papel do relatório do
  pipeline original, só que escrito artigo a artigo.
* ``analisar``: o PDF é baixado numa pasta temporária, lido e apagado. Nada
  fica no computador do pesquisador.

Nos dois modos o PDF só é aceito depois da identidade conferida pelo CONTEÚDO
do arquivo. Um PDF de outro artigo é apagado até no modo ``baixar``: um
arquivo errado na pasta passaria por certo.

As dependências (``fetch``, ``identity``, extração de texto) chegam por
parâmetro para que os testes rodem sem rede e sem o pipeline.
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import threading
from datetime import datetime
from pathlib import Path

MODOS = {"baixar", "analisar"}
LIMITE_TEXTO = 200_000
_PROJETO = re.compile(r"[A-Za-z0-9-]{1,64}")
_relatorio = threading.Lock()

_MOTIVOS = {
    "doi_mismatch": "O DOI impresso no PDF é de outro artigo.",
    "supplementary_material": "O arquivo é material suplementar, não o artigo.",
    "pdf_title_contradicts_record": "O título impresso no PDF é de outra obra.",
}


class PedidoInvalido(ValueError):
    """Entrada que nunca vai dar certo: o ORBIS deve mostrar e não repetir."""


class DiscoIndisponivel(OSError):
    """A pasta de destino não pode ser usada; os próximos artigos falhariam igual."""


def extrair_texto(dados: bytes) -> tuple[str, int]:
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf
    doc = pymupdf.open(stream=dados, filetype="pdf")
    try:
        return "\n".join(doc[i].get_text() for i in range(doc.page_count)), doc.page_count
    finally:
        doc.close()


def _identidade(veredito: dict) -> dict:
    ok = bool(veredito.get("identity_validated"))
    motivo = veredito.get("reason")
    return {
        "ok": ok,
        "metodo": str(veredito.get("validation_method") or ""),
        "score": float(veredito.get("validation_score") or 0),
        "detalhe": "identidade confirmada" if ok else _MOTIVOS.get(
            motivo, "Não foi possível confirmar que o PDF é o artigo pedido."),
    }


def _registrar(pasta: Path, doi: str, situacao: str, fonte: str | None, arquivo: str | None) -> None:
    linha = f"{datetime.now().isoformat(timespec='seconds')}\t{doi}\t{situacao}\t{fonte or '-'}\t{arquivo or '-'}\n"
    with _relatorio, open(pasta / "Relatório.txt", "a", encoding="utf-8") as f:
        f.write(linha)


def baixar_artigo(*, doi: str, projeto: str, modo: str, esperado: dict, prazo: int,
                  pasta_pdfs: Path, fetch_mod, identity_mod, extrair=extrair_texto) -> dict:
    if not _PROJETO.fullmatch(projeto or ""):
        raise PedidoInvalido("Identificador de projeto inválido.")
    if modo not in MODOS:
        raise PedidoInvalido("O modo deve ser 'baixar' ou 'analisar'.")
    if modo == "baixar":
        pasta = Path(pasta_pdfs) / projeto
        try:
            pasta.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DiscoIndisponivel(f"Não foi possível usar a pasta {pasta}: {exc}") from exc
    else:
        pasta = None
    # O fetch sempre baixa numa pasta só deste artigo. Direto na pasta do
    # projeto, dois artigos com o mesmo nome gerado (a fonte pmc_s3 não traz
    # autor nem título: tudo vira "unknown_nd_paper.pdf") se sobrescreviam, e a
    # recusa de identidade de um apagava o PDF do outro.
    with tempfile.TemporaryDirectory(prefix="orbis-") as tmp:
        return _executar(doi, esperado, prazo, Path(tmp), fetch_mod, identity_mod, extrair, relatorio=pasta)


def _executar(doi, esperado, prazo, pasta, fetch_mod, identity_mod, extrair, relatorio):
    # O orçamento é por thread: o fetch confere o prazo entre uma fonte e outra.
    fetch_mod.set_item_deadline(prazo)
    try:
        r = fetch_mod.fetch(doi, pasta, dry_run=False, overwrite=False, timeout=min(30, prazo))
    finally:
        fetch_mod.set_item_deadline(None)
    fontes = [str(f) for f in (r.get("sources_tried") or [])]
    caminho = Path(r["file"]) if r.get("success") and r.get("file") else None
    if caminho is None or not caminho.is_file():
        if relatorio:
            _registrar(relatorio, doi, "nao_localizado", None, None)
        return {"ok": False, "erro": str(r.get("error") or "Nenhuma fonte entregou o PDF."), "fontes_tentadas": fontes}

    dados = caminho.read_bytes()
    identidade = _identidade(identity_mod.validate_article_identity(
        {**esperado, "doi": doi},
        pdf_identity=identity_mod.extract_pdf_identity(dados),
        record_doi_matched=True,
    ))
    fonte = str(r.get("source") or "")
    if not identidade["ok"]:
        caminho.unlink(missing_ok=True)
        if relatorio:
            _registrar(relatorio, doi, "recusado_identidade", fonte, None)
        return {"ok": False, "erro": identidade["detalhe"], "fontes_tentadas": fontes, "identidade": identidade}

    # PDF escaneado ou corrompido: a identidade já foi confirmada, então o
    # artigo entra; só a análise de texto completo fica sem base.
    try:
        texto, paginas = extrair(dados)
    except Exception:
        texto, paginas = "", 0
    aviso = None if texto.strip() else "sem_texto"
    if aviso:
        texto = ""
    resposta = {
        "ok": True, "fonte": fonte, "fontes_tentadas": fontes, "identidade": identidade,
        "texto": texto[:LIMITE_TEXTO], "paginas": paginas, "chars": min(len(texto), LIMITE_TEXTO),
        "texto_truncado": len(texto) > LIMITE_TEXTO,
    }
    if aviso:
        resposta["aviso"] = aviso
    if relatorio:
        resposta["arquivo"] = _guardar(caminho, relatorio, doi)
        _registrar(relatorio, doi, "baixado", fonte, resposta["arquivo"])
    return resposta


def _slug_doi(doi: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", doi).strip("_") or "artigo"


def _guardar(caminho: Path, pasta: Path, doi: str) -> str:
    """Move o PDF validado para a pasta do projeto sem tocar no de outro artigo.

    O índice ``.orbis-index.json`` lembra o nome de cada DOI: baixar de novo
    substitui o mesmo arquivo em vez de criar outro.
    """
    with _relatorio:
        indice_arq = pasta / ".orbis-index.json"
        try:
            indice = json.loads(indice_arq.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            indice = {}
        chave = doi.lower()
        nome = indice.get(chave)
        if not nome:
            nome = f"{_slug_doi(doi)}.pdf" if caminho.name.startswith("unknown_") else caminho.name
            if nome in indice.values() or (pasta / nome).exists():
                nome = f"{Path(nome).stem}__{_slug_doi(doi)}.pdf"
        try:
            shutil.move(str(caminho), str(pasta / nome))
            indice[chave] = nome
            indice_arq.write_text(json.dumps(indice, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as exc:
            raise DiscoIndisponivel(f"Não foi possível gravar em {pasta}: {exc}") from exc
    return nome
