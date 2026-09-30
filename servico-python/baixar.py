"""Baixa UM artigo pela cadeia completa do motor, em um de dois modos.

* ``baixar``: o PDF fica em ``<pasta_pdfs>/<projeto>/`` e cada artigo ganha uma
  linha no ``Relatório.txt`` dessa pasta — o mesmo papel do relatório do
  pipeline original, só que escrito artigo a artigo.
* ``analisar``: o PDF é baixado numa pasta temporária, lido e apagado. Nada
  fica no computador do pesquisador.

Nos dois modos o PDF só é aceito depois da identidade conferida pelo CONTEÚDO
do arquivo. Um PDF de outro artigo é apagado até no modo ``baixar``: um
arquivo errado na pasta passaria por certo.

O texto sai em Markdown (``extracao.py``). No modo ``baixar``, o ``.md`` e as
imagens (``<nome>_imagens/``) ficam ao lado do PDF.

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

import extracao

MODOS = {"baixar", "analisar"}
LIMITE_TEXTO = 200_000
_PROJETO = re.compile(r"[A-Za-z0-9-]{1,64}")
_relatorio = threading.Lock()

_MOTIVOS = {
    "doi_mismatch": "O DOI impresso no PDF é de outro artigo.",
    "supplementary_material": "O arquivo é material suplementar, não o artigo.",
    "pdf_title_contradicts_record": "O título impresso no PDF é de outra obra.",
}


_FALHAS = {
    "not_found": "Nenhuma cópia gratuita foi encontrada nas fontes consultadas.",
    "resolve_network_error": "As bases de metadados não responderam; a disponibilidade é desconhecida. Tente de novo mais tarde.",
    "download_timeout": "A fonte que tem o PDF não respondeu a tempo. Tente de novo.",
    "download_network_error": "A conexão com a fonte que tem o PDF falhou. Tente de novo.",
    "download_item_deadline": "O tempo reservado para este artigo acabou antes de alguma fonte entregar o PDF. Tente de novo.",
    "download_host_cooldown": "A fonte que tem o PDF recusou pedidos há pouco e está em pausa. Tente de novo em alguns minutos.",
    "download_not_a_pdf": "A fonte devolveu uma página em vez do PDF.",
    # Sessão institucional (CAPES/EZproxy no navegador).
    "download_sem_acesso_institucional": "A sua instituição não dá acesso a este artigo: a editora pediu compra ou login.",
    "download_sem_pdf_na_pagina": "A página do artigo abriu com a sua sessão institucional, mas não ofereceu o PDF.",
    "download_pdf_nao_entregue": "A editora mostrou o link do PDF, mas não o entregou pela sessão institucional.",
    "download_prazo_esgotado_na_fila": "O tempo deste artigo acabou enquanto a sessão institucional atendia outros. Tente de novo.",
    "download_prazo_esgotado": "O tempo deste artigo acabou dentro da sessão institucional. Tente de novo.",
}


_SESSAO_EXPIRADA = ("A sessão institucional (CAPES/EZproxy) expirou: renove o login com "
                    "`python motor/src/download/sessao_navegador.py login` — o motor volta a usá-la sozinho.")


def _motivo_falha(erro) -> tuple[str, str]:
    """Frase para o pesquisador e código, a partir do erro estruturado do fetch."""
    if not isinstance(erro, dict):
        return str(erro or "Nenhuma fonte entregou o PDF."), ""
    codigo = str(erro.get("code") or "")
    if codigo in _FALHAS:
        return _FALHAS[codigo], codigo
    if codigo == "article_identity_not_confirmed":
        n = re.match(r"\d+", str(erro.get("message") or ""))
        return (f"{n.group(0) if n else 'Alguns'} PDF(s) encontrados não eram este artigo "
                "(material suplementar ou outro documento) e foram descartados."), codigo
    http = re.fullmatch(r"download_http_(\d{3})", codigo)
    if http and http.group(1) in ("401", "403"):
        return ("O site que tem o PDF bloqueia o download automático "
                f"(HTTP {http.group(1)}). Abra a página do artigo e baixe manualmente."), codigo
    if http:
        return (f"A fonte que tem o PDF está limitando pedidos ou fora do ar (HTTP {http.group(1)}). "
                "Tente de novo em alguns minutos."), codigo
    return str(erro.get("message") or codigo or "Nenhuma fonte entregou o PDF."), codigo


class PedidoInvalido(ValueError):
    """Entrada que nunca vai dar certo: o ORBIS deve mostrar e não repetir."""


class DiscoIndisponivel(OSError):
    """A pasta de destino não pode ser usada; os próximos artigos falhariam igual."""


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
                  pasta_pdfs: Path, fetch_mod, identity_mod, extrair=None) -> dict:
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
    # Resolvido na hora: as opções da extração mudam pela tela de Configurações.
    extrair = extrair or (lambda caminho, imagens: extracao.extrair_conforme_opcoes(caminho, imagens))
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
        motivo, codigo = _motivo_falha(r.get("error"))
        resposta = {"ok": False, "erro": motivo, **({"codigo": codigo} if codigo else {}), "fontes_tentadas": fontes}
        # A sessão caída vale para todos os próximos artigos, qualquer que tenha
        # sido o último erro deste: é isso que o pesquisador precisa saber.
        if any(t.get("reason") == "sessao_expirada" for t in r.get("download_attempts") or []):
            resposta["erro"] = f"{motivo} {_SESSAO_EXPIRADA}"
            resposta["sessao_expirada"] = True
        return resposta

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

    # No modo "baixar" o PDF vai para a pasta do projeto antes da extração: as
    # imagens e o .md nascem ao lado dele, com o mesmo nome.
    arquivo = _guardar(caminho, relatorio, doi) if relatorio else None
    if arquivo:
        caminho = relatorio / arquivo
    base = caminho.with_suffix("")
    imagens = base.parent / f"{base.name}_imagens" if arquivo else None
    if imagens:
        # Baixar de novo substitui as imagens (e some com elas se agora estão desligadas).
        shutil.rmtree(imagens, ignore_errors=True)
    # PDF escaneado ou corrompido: a identidade já foi confirmada, então o
    # artigo entra; só a análise de texto completo fica sem base.
    try:
        ex = extrair(caminho, imagens)
    except Exception:
        ex = {"texto": "", "formato": "texto", "paginas": 0, "imagens": 0}
    texto = str(ex.get("texto") or "")
    aviso = None if texto.strip() else "sem_texto"
    if aviso:
        texto = ""
    formato = "markdown" if ex.get("formato") == "markdown" and texto else "texto"
    resposta = {
        "ok": True, "fonte": fonte, "fontes_tentadas": fontes, "identidade": identidade,
        "texto": texto[:LIMITE_TEXTO], "paginas": int(ex.get("paginas") or 0), "chars": min(len(texto), LIMITE_TEXTO),
        "texto_truncado": len(texto) > LIMITE_TEXTO, "formato": formato, "imagens": 0,
    }
    if aviso:
        resposta["aviso"] = aviso
    if ex.get("aviso"):
        resposta["aviso_extracao"] = str(ex["aviso"])
    if arquivo:
        resposta["arquivo"] = arquivo
        md = base.parent / f"{base.name}.md"
        md.unlink(missing_ok=True)  # um .md antigo mentiria sobre o texto atual
        if formato == "markdown":
            md.write_text(texto, encoding="utf-8")
            resposta["arquivo_md"] = md.name
        if ex.get("imagens") and imagens and imagens.is_dir():
            resposta["imagens"] = int(ex["imagens"])
            resposta["pasta_imagens"] = imagens.name
        _registrar(relatorio, doi, "baixado", fonte, arquivo)
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
