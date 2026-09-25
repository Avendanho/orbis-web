# Download de artigos pelo motor — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Com o motor Python no ar, o ORBIS baixa os PDFs pela cadeia completa do `motor/src/download/fetch.py` em um de dois modos — "analisar e baixar todos" (PDF numa pasta local) ou "só analisar no sistema" (PDF lido e descartado) — e leva o texto extraído à PCC automática.

**Architecture:** O navegador comanda o lote. Fase 1: `POST /api/projects/[id]/motor` (4 em paralelo) repassa cada DOI ao `POST /baixar` do motor, que baixa, valida identidade pelo conteúdo, extrai o texto e grava o PDF só no modo baixar; o Worker guarda o texto no R2 e o resultado em `search_items`, sem tocar no estado do projeto. Fase 2: o `PATCH action:'incorporate'` existente cria o artigo a partir desse resultado, sem PDF no R2. A PCC automática lê o texto do R2.

**Tech Stack:** Cloudflare Worker (vinext/Next 16, TypeScript, D1, R2), FastAPI + PyMuPDF no `servico-python/`, testes em `node:assert` (`tests/*.mjs`, Miniflare) e pytest.

**Spec:** `docs/superpowers/specs/2026-09-24-download-motor-design.md`

## Global Constraints

- Node ≥ 22.13; nenhuma dependência nova (npm ou pip).
- Textos da interface, mensagens de erro e comentários em português, no estilo denso do código vizinho.
- Modos: exatamente `'baixar'` e `'analisar'`; padrão `'baixar'`; salvo em `state.settings.downloadMode`.
- Motor: prazo padrão 90 s por artigo (limitado a 10–300), no máximo 4 chamadas `/baixar` simultâneas, texto limitado a 200 000 caracteres.
- Pasta do modo baixar: `ORBIS_DATA_DIR/pdfs/<projeto>/` (padrão de `ORBIS_DATA_DIR`: a pasta `motor/`), com `Relatório.txt` acrescentado linha a linha.
- `projeto` aceito pelo motor: `^[A-Za-z0-9-]{1,64}$`.
- PCC automática: `texto_completo` até 60 000 caracteres por artigo, lote de 2. Triagem nunca recebe texto.
- Texto no R2 em `<projeto>/texto/<32 hex do SHA-256 de doi.toLowerCase() (ou id do artigo sem DOI)>.txt`, contado na cota de 2 GB.
- Nos dois modos nenhum PDF é gravado no R2. Identidade reprovada apaga o PDF nos dois modos.
- Sem motor no ar, o botão atual (Worker → R2) funciona exatamente como hoje.
- O projeto **não é repositório git**: no lugar de commits, cada tarefa termina num checkpoint com os testes da tarefa verdes.

## Review Focus

1. Retomar um lote pausado: DOIs com `motor.ok` não são baixados de novo, e repetir um DOI não conta a cota duas vezes — coberto na Tarefa 5 (POST repetido, `bytes` igual).
2. Excluir um artigo e reincorporar o DOI: o texto some do R2, a cota é devolvida e o DOI volta a exigir download (sem chave órfã) — Tarefa 5.
3. "Limpar busca" com textos de DOIs nunca incorporados: textos apagados e cota devolvida — Tarefas 4 (`orphanTexts`) e 5.
4. Restaurar backup com textos em projeto novo: a chave é reescrita para o projeto novo e o upload funciona durante `importPending` — Tarefa 5.
5. Mesmo DOI baixado duas vezes no modo baixar: um único PDF na pasta, duas linhas no relatório — Tarefa 1.

---

### Task 1: Módulo de download do motor (`servico-python/baixar.py`)

**Files:**
- Create: `servico-python/baixar.py`
- Test: `servico-python/tests/test_baixar.py`

**Interfaces:**
- Consumes: `fetch.fetch(doi, out_dir, *, dry_run, overwrite, timeout)` → `{success, file, source, sources_tried, error}`; `fetch.set_item_deadline(seconds|None)`; `identity.extract_pdf_identity(bytes)`; `identity.validate_article_identity(expected, *, pdf_identity, record_doi_matched)` → `{identity_validated, validation_method, validation_score, reason}`.
- Produces: `baixar_artigo(*, doi, projeto, modo, esperado, prazo, pasta_pdfs, fetch_mod, identity_mod, extrair=extrair_texto) -> dict` com as chaves `ok, erro?, fonte, fontes_tentadas, arquivo? (só baixar), identidade{ok,metodo,score,detalhe}, texto, paginas, chars, texto_truncado, aviso?`; exceções `PedidoInvalido(ValueError)` e `DiscoIndisponivel(OSError)`; constante `LIMITE_TEXTO = 200_000`.

- [ ] **Step 1: Write the failing tests**

`servico-python/tests/test_baixar.py`:

```python
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import baixar as motor_baixar  # noqa: E402

PDF = b"%PDF-1.7\nconteudo"


class FetchFalso:
    def __init__(self, sucesso=True, nome="Silva_2021_T.pdf", erro_depois=False):
        self.sucesso, self.nome, self.erro_depois = sucesso, nome, erro_depois
        self.prazos, self.pastas = [], []

    def set_item_deadline(self, segundos):
        self.prazos.append(segundos)

    def fetch(self, doi, out_dir, *, dry_run, overwrite, timeout, sources=None):
        self.pastas.append(Path(out_dir))
        if not self.sucesso:
            return {"doi": doi, "success": False, "file": None,
                    "sources_tried": ["unpaywall", "pmc"], "error": "not found"}
        arquivo = Path(out_dir) / self.nome
        arquivo.write_bytes(PDF)
        if self.erro_depois:
            raise RuntimeError("falha no meio")
        return {"doi": doi, "success": True, "source": "pmc", "file": str(arquivo), "sources_tried": ["pmc"]}


class IdentidadeFalsa:
    def __init__(self, ok=True, motivo=None):
        self.ok, self.motivo, self.esperado = ok, motivo, None

    def extract_pdf_identity(self, dados):
        return {"doi": None}

    def validate_article_identity(self, esperado, *, pdf_identity=None, record_doi_matched=False):
        self.esperado = esperado
        return {"identity_validated": self.ok, "validation_method": "doi_in_pdf",
                "validation_score": 1.0 if self.ok else 0.0, "reason": self.motivo}


def chamar(tmp_path, modo="baixar", fetch=None, ident=None,
           extrair=lambda dados: ("texto do artigo", 3), projeto="proj-1", doi="10.1/a", pasta=None):
    return motor_baixar.baixar_artigo(
        doi=doi, projeto=projeto, modo=modo, esperado={"title": "T"}, prazo=90,
        pasta_pdfs=pasta or tmp_path / "pdfs", fetch_mod=fetch or FetchFalso(),
        identity_mod=ident or IdentidadeFalsa(), extrair=extrair)


def test_modo_baixar_grava_pdf_e_relatorio(tmp_path):
    r = chamar(tmp_path)
    assert r["ok"] is True
    assert r["arquivo"] == "Silva_2021_T.pdf"
    assert (tmp_path / "pdfs" / "proj-1" / "Silva_2021_T.pdf").read_bytes() == PDF
    relatorio = (tmp_path / "pdfs" / "proj-1" / "Relatório.txt").read_text(encoding="utf-8")
    assert "10.1/a\tbaixado\tpmc\tSilva_2021_T.pdf" in relatorio
    assert r["texto"] == "texto do artigo" and r["paginas"] == 3 and r["chars"] == 15
    assert r["identidade"] == {"ok": True, "metodo": "doi_in_pdf", "score": 1.0, "detalhe": "identidade confirmada"}


def test_modo_analisar_nao_deixa_arquivo(tmp_path):
    f = FetchFalso()
    r = chamar(tmp_path, modo="analisar", fetch=f)
    assert r["ok"] is True and r["texto"] == "texto do artigo"
    assert "arquivo" not in r
    assert not f.pastas[0].exists()
    assert not (tmp_path / "pdfs").exists()


def test_modo_analisar_apaga_mesmo_com_erro(tmp_path):
    f = FetchFalso(erro_depois=True)
    with pytest.raises(RuntimeError):
        chamar(tmp_path, modo="analisar", fetch=f)
    assert not f.pastas[0].exists()


@pytest.mark.parametrize("modo", ["baixar", "analisar"])
def test_identidade_reprovada_apaga_pdf(tmp_path, modo):
    f = FetchFalso()
    r = chamar(tmp_path, modo=modo, fetch=f, ident=IdentidadeFalsa(ok=False, motivo="doi_mismatch"))
    assert r["ok"] is False
    assert r["erro"] == "O DOI impresso no PDF é de outro artigo."
    assert r["identidade"]["ok"] is False
    assert not (f.pastas[0] / "Silva_2021_T.pdf").exists()


def test_prazo_repassado_e_limpo_mesmo_com_erro(tmp_path):
    f = FetchFalso()
    chamar(tmp_path, fetch=f)
    assert f.prazos == [90, None]
    f = FetchFalso(erro_depois=True)
    with pytest.raises(RuntimeError):
        chamar(tmp_path, fetch=f)
    assert f.prazos == [90, None]


def test_esperado_leva_o_doi(tmp_path):
    ident = IdentidadeFalsa()
    chamar(tmp_path, ident=ident)
    assert ident.esperado == {"title": "T", "doi": "10.1/a"}


@pytest.mark.parametrize("projeto", ["../x", "", "a/b", "x" * 65])
def test_projeto_invalido(tmp_path, projeto):
    with pytest.raises(motor_baixar.PedidoInvalido):
        chamar(tmp_path, projeto=projeto)


def test_modo_invalido(tmp_path):
    with pytest.raises(motor_baixar.PedidoInvalido):
        chamar(tmp_path, modo="tudo")


def test_nao_localizado_registra_no_relatorio(tmp_path):
    r = chamar(tmp_path, fetch=FetchFalso(sucesso=False))
    assert r == {"ok": False, "erro": "not found", "fontes_tentadas": ["unpaywall", "pmc"]}
    assert "10.1/a\tnao_localizado" in (tmp_path / "pdfs" / "proj-1" / "Relatório.txt").read_text(encoding="utf-8")


def test_texto_truncado(tmp_path):
    r = chamar(tmp_path, extrair=lambda d: ("a" * 250_000, 10))
    assert len(r["texto"]) == motor_baixar.LIMITE_TEXTO
    assert r["chars"] == motor_baixar.LIMITE_TEXTO
    assert r["texto_truncado"] is True


def test_pdf_sem_texto_entra_com_aviso(tmp_path):
    def quebra(dados):
        raise ValueError("escaneado")
    r = chamar(tmp_path, extrair=quebra)
    assert r["ok"] is True and r["texto"] == "" and r["aviso"] == "sem_texto"
    r = chamar(tmp_path, extrair=lambda d: ("   \n", 1))
    assert r["texto"] == "" and r["aviso"] == "sem_texto"


def test_mesmo_doi_duas_vezes_um_pdf(tmp_path):
    chamar(tmp_path)
    chamar(tmp_path)
    pasta = tmp_path / "pdfs" / "proj-1"
    assert sorted(p.name for p in pasta.iterdir()) == ["Relatório.txt", "Silva_2021_T.pdf"]
    assert len((pasta / "Relatório.txt").read_text(encoding="utf-8").splitlines()) == 2


def test_disco_indisponivel(tmp_path):
    ocupado = tmp_path / "pdfs"
    ocupado.write_text("isto é um arquivo, não uma pasta")
    with pytest.raises(motor_baixar.DiscoIndisponivel):
        chamar(tmp_path, pasta=ocupado)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd servico-python && .venv/bin/python -m pytest tests/test_baixar.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'baixar'`

- [ ] **Step 3: Write the implementation**

`servico-python/baixar.py`:

```python
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

import re
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
        return _executar(doi, esperado, prazo, pasta, fetch_mod, identity_mod, extrair, relatorio=pasta)
    with tempfile.TemporaryDirectory(prefix="orbis-") as tmp:
        return _executar(doi, esperado, prazo, Path(tmp), fetch_mod, identity_mod, extrair, relatorio=None)


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
        resposta["arquivo"] = caminho.name
        _registrar(relatorio, doi, "baixado", fonte, caminho.name)
    return resposta
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd servico-python && .venv/bin/python -m pytest tests/test_baixar.py -q`
Expected: PASS (todos)

- [ ] **Step 5: Checkpoint** — `servico-python/baixar.py` e `servico-python/tests/test_baixar.py` prontos, testes verdes.

---

### Task 2: Rota `/baixar` e `/saude` no serviço

**Files:**
- Modify: `servico-python/main.py` (imports no topo; `/saude`; nova rota no fim)
- Test: `servico-python/tests/test_rota_baixar.py`

**Interfaces:**
- Consumes: `baixar.baixar_artigo`, `baixar.PedidoInvalido`, `baixar.DiscoIndisponivel` (Task 1); `main._importar(nome)`.
- Produces: `POST /baixar` com corpo `{doi, projeto, modo, titulo="", autor="", ano="", periodico="", prazo=90}` → 200 com o dict da Task 1; 400 (`PedidoInvalido`), 507 (`DiscoIndisponivel`), 503 (módulo ausente). `GET /saude` passa a trazer `recursos` com `"download_completo"` (quando `fetch` e PyMuPDF importam) e `pasta_pdfs` (caminho absoluto). Variável `main.PASTA_PDFS: Path`.

- [ ] **Step 1: Write the failing tests**

`servico-python/tests/test_rota_baixar.py`:

```python
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from test_baixar import FetchFalso, IdentidadeFalsa  # noqa: E402


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    modulos = {"fetch": FetchFalso(), "identity": IdentidadeFalsa()}
    monkeypatch.setattr(main, "_importar", lambda nome: modulos[nome])
    monkeypatch.setattr(main, "PASTA_PDFS", tmp_path / "pdfs")
    return TestClient(main.app)


def test_baixar_ok(cliente, tmp_path, monkeypatch):
    monkeypatch.setattr(main.motor_baixar, "extrair_texto", lambda dados: ("texto", 1))
    r = cliente.post("/baixar", json={"doi": "10.1/a", "projeto": "p1", "modo": "baixar", "titulo": "T"})
    assert r.status_code == 200, r.text
    assert r.json()["arquivo"] == "Silva_2021_T.pdf"
    assert (tmp_path / "pdfs" / "p1" / "Silva_2021_T.pdf").exists()


def test_baixar_projeto_invalido(cliente):
    r = cliente.post("/baixar", json={"doi": "10.1/a", "projeto": "../x", "modo": "baixar"})
    assert r.status_code == 400


def test_baixar_disco(cliente, tmp_path, monkeypatch):
    (tmp_path / "ocupado").write_text("arquivo")
    monkeypatch.setattr(main, "PASTA_PDFS", tmp_path / "ocupado")
    r = cliente.post("/baixar", json={"doi": "10.1/a", "projeto": "p1", "modo": "baixar"})
    assert r.status_code == 507
    assert "Não foi possível usar a pasta" in r.json()["detail"]


def test_saude_informa_pasta(cliente, tmp_path):
    d = cliente.get("/saude").json()
    assert d["pasta_pdfs"] == str((tmp_path / "pdfs").resolve())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd servico-python && .venv/bin/python -m pytest tests/test_rota_baixar.py -q`
Expected: FAIL — `AttributeError: ... has no attribute 'PASTA_PDFS'` (ou 404 em `/baixar`)

- [ ] **Step 3: Write the implementation**

Em `servico-python/main.py`:

1. Nos imports do topo, acrescentar `import threading` ao lado de `import os`/`import sys`, e depois de `from pydantic import BaseModel`:

```python
import baixar as motor_baixar
```

2. Logo depois do bloco `if PIPELINE and Path(PIPELINE).is_dir(): sys.path.insert(0, PIPELINE)`:

```python
# Onde o modo "baixar" grava os PDFs. `ORBIS_DATA_DIR` é o mesmo diretório de
# dados que o motor já usa (padrão: a pasta `motor/`).
PASTA_PDFS = Path(os.environ.get("ORBIS_DATA_DIR") or Path(__file__).resolve().parent.parent / "motor") / "pdfs"

# O fetch abre muitas conexões por artigo; mais que 4 em paralelo só gera
# bloqueio (HTTP 429) nas fontes.
_vagas = threading.BoundedSemaphore(4)
```

3. Em `saude()`, trocar o `return` final por:

```python
    if "recuperacao" in recursos and "texto_do_pdf" in recursos:
        recursos.append("download_completo")
    return {"ok": True, "pipeline": PIPELINE or None, "recursos": recursos, "faltando": faltando,
            "pasta_pdfs": str(PASTA_PDFS.resolve())}
```

4. No fim do arquivo:

```python
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
```

(`extrair=motor_baixar.extrair_texto` é lido na hora da chamada para o `monkeypatch` do teste valer.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd servico-python && .venv/bin/python -m pytest tests -q`
Expected: PASS (Task 1 + Task 2)

- [ ] **Step 5: Checkpoint** — `/baixar` e `/saude` prontos; `uvicorn main:app` sobe (`cd servico-python && timeout 5 .venv/bin/python -m uvicorn main:app --port 8911` sem erro de import).

---

### Task 3: Ponte ORBIS → motor e URL do motor no Worker

**Files:**
- Modify: `lib/python-bridge.ts`
- Modify: `vite.config.ts` (`localBindingConfig`)
- Test: `tests/python-bridge.mjs`

**Interfaces:**
- Consumes: `POST /baixar`, `GET /saude` (Task 2).
- Produces (em `lib/python-bridge.ts`):
  - `type EngineStatus={online:boolean;resources:string[];missing:string[];url:string;pdfDir:string}`
  - `type EngineIdentity={ok:boolean;metodo:string;score:number;detalhe:string}`
  - `type EngineDownload={ok:boolean;erro?:string;fonte?:string;fontes_tentadas?:string[];arquivo?:string;identidade?:EngineIdentity;texto?:string;paginas?:number;chars?:number;texto_truncado?:boolean;aviso?:string}`
  - `type DownloadRequest={doi:string;projeto:string;modo:'baixar'|'analisar';titulo?:string;autor?:string;ano?:string;periodico?:string;prazo?:number}`
  - `class EngineRefused extends Error{status:number}`
  - `downloadViaEngine(env:any,req:DownloadRequest):Promise<EngineDownload|null>` — `null` = motor fora do ar (ou sem URL); `{ok:false,erro:'Tempo esgotado…'}` em timeout; lança `EngineRefused` em HTTP de erro.
- Worker: `env.ORBIS_ENGINE_URL` passa a existir no dev quando o `start.py` define a variável.

- [ ] **Step 1: Write the failing test**

`tests/python-bridge.mjs`:

```js
import {readFile} from 'node:fs/promises';import {stripTypeScriptTypes} from 'node:module';import assert from 'node:assert/strict';
const src=stripTypeScriptTypes(await readFile('lib/python-bridge.ts','utf8'));
const bridge=await import('data:text/javascript;base64,'+Buffer.from(src).toString('base64'));
const env={ORBIS_ENGINE_URL:'http://motor.test/'},pedido={doi:'10.1/a',projeto:'p',modo:'analisar'};
const chamadas=[];const responder=f=>{globalThis.fetch=async(url,init)=>{chamadas.push({url,init});return f(url,init)}};

// Sem URL configurada: nada é chamado.
responder(()=>{throw new Error('não deveria chamar')});
assert.equal(await bridge.downloadViaEngine({},pedido),null);

// Sucesso: URL sem barra dupla e prazo padrão.
responder(()=>Response.json({ok:true,fonte:'pmc'}));
let r=await bridge.downloadViaEngine(env,pedido);
assert.equal(r.fonte,'pmc');
assert.equal(chamadas.at(-1).url,'http://motor.test/baixar');
assert.equal(JSON.parse(chamadas.at(-1).init.body).prazo,90);

// Motor fora do ar: null, não exceção.
responder(()=>{throw new TypeError('fetch failed')});
assert.equal(await bridge.downloadViaEngine(env,pedido),null);

// Tempo esgotado: resultado do artigo, não "motor fora".
responder(()=>{throw new DOMException('timeout','TimeoutError')});
r=await bridge.downloadViaEngine(env,pedido);
assert.equal(r.ok,false);assert.match(r.erro,/Tempo esgotado/);

// Recusa do motor: exceção com o status e o detalhe do FastAPI.
responder(()=>Response.json({detail:'sem espaço'},{status:507}));
await assert.rejects(bridge.downloadViaEngine(env,pedido),e=>e instanceof bridge.EngineRefused&&e.status===507&&/sem espaço/.test(e.message));

// Status traz a pasta dos PDFs.
responder(()=>Response.json({ok:true,recursos:['download_completo'],faltando:[],pasta_pdfs:'/d/pdfs'}));
const st=await bridge.engineStatus(env);
assert.equal(st.online,true);assert.equal(st.pdfDir,'/d/pdfs');assert.ok(st.resources.includes('download_completo'));
console.log('python-bridge: ok');
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/python-bridge.mjs`
Expected: FAIL — `TypeError: bridge.downloadViaEngine is not a function`

- [ ] **Step 3: Write the implementation**

Em `lib/python-bridge.ts`:

1. Trocar `export type EngineStatus={online:boolean;resources:string[];missing:string[];url:string};` por:

```ts
export type EngineStatus={online:boolean;resources:string[];missing:string[];url:string;pdfDir:string};
export type EngineIdentity={ok:boolean;metodo:string;score:number;detalhe:string};
export type EngineDownload={ok:boolean;erro?:string;fonte?:string;fontes_tentadas?:string[];arquivo?:string;identidade?:EngineIdentity;texto?:string;paginas?:number;chars?:number;texto_truncado?:boolean;aviso?:string};
export type DownloadRequest={doi:string;projeto:string;modo:'baixar'|'analisar';titulo?:string;autor?:string;ano?:string;periodico?:string;prazo?:number};

// Recusa explícita do motor (entrada inválida, disco cheio). Diferente de
// "fora do ar": o motor respondeu, e o ORBIS precisa mostrar o motivo.
export class EngineRefused extends Error{status:number;constructor(status:number,message:string){super(message);this.status=status}}
```

2. Em `engineStatus`, trocar os dois `return` para incluir `pdfDir`:

```ts
 if(!url)return {online:false,resources:[],missing:[],url:'',pdfDir:''};
 try{
  const d:any=await call(url,'/saude',undefined,AbortSignal.timeout(4000));
  return {online:!!d?.ok,resources:d?.recursos||[],missing:d?.faltando||[],url,pdfDir:String(d?.pasta_pdfs||'')};
 }catch{return {online:false,resources:[],missing:[],url,pdfDir:''};}
```

3. No fim do arquivo:

```ts
// Baixa um artigo pela cadeia completa do motor. O prazo é imposto pelo
// próprio motor; o limite daqui só cobre um motor travado.
export async function downloadViaEngine(env:any,req:DownloadRequest):Promise<EngineDownload|null>{
 const url=engineUrl(env);
 if(!url)return null;
 const prazo=req.prazo??90;
 let r:Response;
 try{
  r=await fetch(url+'/baixar',{method:'POST',headers:{'content-type':'application/json',Accept:'application/json'},body:JSON.stringify({...req,prazo}),signal:AbortSignal.timeout((prazo+30)*1000)});
 }catch(e:any){
  if(e?.name==='TimeoutError'||e?.name==='AbortError')return {ok:false,erro:'Tempo esgotado ao buscar o PDF. Retome o lote para tentar de novo.'};
  return null;
 }
 if(!r.ok){
  const d:any=await r.json().catch(()=>({}));
  throw new EngineRefused(r.status,String(d?.detail||'O motor recusou o pedido (HTTP '+r.status+').'));
 }
 return await r.json() as EngineDownload;
}
```

4. Em `vite.config.ts`, dentro de `localBindingConfig`, depois de `compatibility_flags: ["nodejs_compat"],`:

```ts
  // O start.py define ORBIS_ENGINE_URL quando sobe o motor; o Worker só
  // enxerga o que for declarado aqui.
  vars: process.env.ORBIS_ENGINE_URL
    ? { ORBIS_ENGINE_URL: process.env.ORBIS_ENGINE_URL }
    : {},
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node tests/python-bridge.mjs`
Expected: `python-bridge: ok`

- [ ] **Step 5: Checkpoint** — ponte pronta.

---

### Task 4: Regras puras do download pelo motor (`lib/motor-download.ts`)

**Files:**
- Create: `lib/motor-download.ts`
- Test: `tests/motor-download.mjs`

**Interfaces:**
- Consumes: tipos `EngineDownload`, `EngineIdentity` (Task 3) — só `import type`.
- Produces:
  - `type DownloadMode='baixar'|'analisar'`; `isMode(v:any):v is DownloadMode`
  - `textKey(project:string,ref:string):Promise<string>` → `<project>/texto/<32 hex>.txt` (ref em minúsculas)
  - `type MotorText={key:string;bytes:number;chars:number;paginas:number;truncado:boolean}`
  - `type MotorResult={ok:boolean;modo:DownloadMode;fonte?:string;fontes:string[];arquivo?:string;identidade?:EngineIdentity;erro?:string;aviso?:string;texto?:MotorText|null}`
  - `motorSummary(res:EngineDownload,modo:DownloadMode,texto:{key:string;bytes:number}|null):MotorResult`
  - `motorArticle(doi:string,metadata:any,articleId:string,filename:string,at:string)` → registro do corpus com `source.kind='motor'`, `identity`, `texto`
  - `motorNote(article:any):string` — texto do cartão; `''` quando não é do motor
  - `orphanTexts(rows:{result:string|null}[],articles:any[]):{keys:string[];bytes:number}`

- [ ] **Step 1: Write the failing test**

`tests/motor-download.mjs`:

```js
import {readFile} from 'node:fs/promises';import {stripTypeScriptTypes} from 'node:module';import assert from 'node:assert/strict';
const src=stripTypeScriptTypes(await readFile('lib/motor-download.ts','utf8'));
const md=await import('data:text/javascript;base64,'+Buffer.from(src).toString('base64'));

assert.ok(md.isMode('baixar')&&md.isMode('analisar')&&!md.isMode('tudo')&&!md.isMode(undefined));

const k1=await md.textKey('p1','10.1/ABC');
assert.equal(k1,await md.textKey('p1','10.1/abc'),'DOI sem diferença de caixa');
assert.match(k1,/^p1\/texto\/[0-9a-f]{32}\.txt$/);
assert.notEqual(k1,await md.textKey('p2','10.1/abc'));

const okRes={ok:true,fonte:'pmc',fontes_tentadas:['pmc'],arquivo:'X.pdf',identidade:{ok:true,metodo:'doi_in_pdf',score:1,detalhe:'identidade confirmada'},texto:'abc',paginas:2,chars:3,texto_truncado:false};
let s=md.motorSummary(okRes,'analisar',{key:'k',bytes:3});
assert.equal(s.ok,true);assert.equal(s.arquivo,undefined,'modo analisar não guarda nome de arquivo');
assert.deepEqual(s.texto,{key:'k',bytes:3,chars:3,paginas:2,truncado:false});
s=md.motorSummary(okRes,'baixar',null);
assert.equal(s.arquivo,'X.pdf');assert.equal(s.texto,null);
s=md.motorSummary({ok:false,erro:'e'.repeat(5000),fontes_tentadas:['a']},'baixar',null);
assert.equal(s.ok,false);assert.equal(s.erro.length,3000);assert.deepEqual(s.fontes,['a']);

const meta={title:'T',authors:'A',year:'2021',abstract:'R',motor:md.motorSummary(okRes,'baixar',{key:'k',bytes:3})};
const art=md.motorArticle('10.1/a',meta,'id1','artigo_1_10.1_a.pdf','2026-01-01T00:00:00Z');
assert.equal(art.id,'id1');assert.equal(art.filename,'artigo_1_10.1_a.pdf');assert.equal(art.title,'T');
assert.equal(art.source.kind,'motor');assert.equal(art.source.modo,'baixar');assert.equal(art.source.arquivoLocal,'X.pdf');
assert.equal(art.identity.ok,true);assert.equal(art.identity.method,'conteudo_pdf:doi_in_pdf');
assert.deepEqual(art.texto,{key:'k',bytes:3,chars:3,paginas:2,truncado:false,origem:'motor'});

assert.match(md.motorNote(art),/pasta local: X\.pdf/);
assert.match(md.motorNote({...art,source:{kind:'motor',modo:'analisar'}}),/lido e descartado; texto disponível/);
assert.match(md.motorNote({...art,texto:undefined}),/Sem texto extraído/);
assert.equal(md.motorNote({source:{kind:'local'}}),'');

const rows=[{result:JSON.stringify({motor:{texto:{key:'k1',bytes:10}}})},{result:JSON.stringify({motor:{texto:{key:'k2',bytes:5}}})},{result:null},{result:JSON.stringify({found:true})}];
assert.deepEqual(md.orphanTexts(rows,[{texto:{key:'k1'}},{}]),{keys:['k2'],bytes:5});
console.log('motor-download: ok');
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/motor-download.mjs`
Expected: FAIL — `ENOENT: ... lib/motor-download.ts`

- [ ] **Step 3: Write the implementation**

`lib/motor-download.ts`:

```ts
// Regras do download pelo motor, sem dependência do Worker: o que vai para o
// `search_items`, como vira artigo do corpus e onde fica o texto extraído.
//
// Nos dois modos o PDF NÃO vai para o R2: no modo "baixar" ele fica na pasta
// local do pesquisador; no "analisar" é lido e descartado. O que o ORBIS
// guarda é o texto, porque é a única base da análise que sobra.
import type {EngineDownload,EngineIdentity} from './python-bridge';

export type DownloadMode='baixar'|'analisar';
export const isMode=(v:any):v is DownloadMode=>v==='baixar'||v==='analisar';

export type MotorText={key:string;bytes:number;chars:number;paginas:number;truncado:boolean};
export type MotorResult={ok:boolean;modo:DownloadMode;fonte?:string;fontes:string[];arquivo?:string;identidade?:EngineIdentity;erro?:string;aviso?:string;texto?:MotorText|null};

// Mesmo endereço para o mesmo DOI no mesmo projeto: repetir o download
// sobrescreve em vez de duplicar, e a restauração recalcula para o projeto novo.
export async function textKey(project:string,ref:string){
 const d=new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(ref.toLowerCase())));
 return project+'/texto/'+Array.from(d.slice(0,16),x=>x.toString(16).padStart(2,'0')).join('')+'.txt';
}

export function motorSummary(res:EngineDownload,modo:DownloadMode,texto:{key:string;bytes:number}|null):MotorResult{
 if(!res.ok)return {ok:false,modo,erro:String(res.erro||'O motor não entregou um PDF válido.').slice(0,3000),fontes:res.fontes_tentadas||[],identidade:res.identidade};
 return {ok:true,modo,fonte:res.fonte||'',fontes:res.fontes_tentadas||[],arquivo:modo==='baixar'?res.arquivo:undefined,identidade:res.identidade,aviso:res.aviso,
  texto:texto?{key:texto.key,bytes:texto.bytes,chars:Number(res.chars)||0,paginas:Number(res.paginas)||0,truncado:!!res.texto_truncado}:null};
}

export function motorArticle(doi:string,metadata:any,articleId:string,filename:string,at:string){
 const m:MotorResult=metadata.motor;
 return {id:articleId,doi,title:metadata.title||'',authors:metadata.authors||'',year:metadata.year||'',abstract:metadata.abstract||'',filename,
  identity:{ok:true,method:'conteudo_pdf:'+(m.identidade?.metodo||''),score:m.identidade?.score??0,detail:m.identidade?.detalhe||'',checkedAt:at},
  source:{kind:'motor',modo:m.modo,fonte:m.fonte,arquivoLocal:m.modo==='baixar'?m.arquivo:undefined,reason:m.modo==='baixar'?'PDF validado e salvo na pasta local':'PDF validado, lido e descartado'},
  texto:m.texto?{...m.texto,origem:'motor'}:undefined};
}

export function motorNote(a:any):string{
 if(a?.source?.kind!=='motor')return '';
 const semTexto=' Sem texto extraído — a PCC automática responderá null.';
 if(a.source.modo==='baixar')return 'PDF na pasta local: '+(a.source.arquivoLocal||'—')+'.'+(a.texto?.key?'':semTexto);
 return a.texto?.key?'PDF lido e descartado; texto disponível para a análise.':'PDF lido e descartado.'+semTexto;
}

// Textos de DOIs que nunca entraram no corpus: ao limpar a busca, somem junto.
export function orphanTexts(rows:{result:string|null}[],articles:any[]){
 const kept=new Set(articles.map(a=>a?.texto?.key).filter(Boolean));const keys:string[]=[];let bytes=0;
 for(const row of rows){let t:any;try{t=JSON.parse(row.result||'null')?.motor?.texto}catch{continue}if(t?.key&&!kept.has(t.key)){keys.push(t.key);bytes+=Number(t.bytes||0)}}
 return {keys,bytes};
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node tests/motor-download.mjs`
Expected: `motor-download: ok`

- [ ] **Step 5: Checkpoint** — regras puras prontas.

---

### Task 5: Rotas do Worker — fase 1, incorporação, texto, exclusão, restauração

**Files:**
- Create: `app/api/projects/[id]/motor/route.ts`
- Create: `app/api/projects/[id]/texto/route.ts`
- Modify: `lib/incorporate-pdf.ts` (desvio para o motor)
- Modify: `app/api/projects/[id]/route.ts` (ações `downloadMode`, `removeArticle`, `clearStage`, `restore`; import)
- Test: `tests/motor-integration.mjs`

**Interfaces:**
- Consumes: `downloadViaEngine`, `engineStatus`, `EngineRefused` (Task 3); `isMode`, `textKey`, `motorSummary`, `motorArticle`, `orphanTexts` (Task 4); `identity`, `owned`, `requireRole`, `database`, `bucket`, `body`, `ok`, `fail`, `commit`, `ApiError` de `lib/server.ts`.
- Produces:
  - `GET /api/projects/[id]/motor` → `EngineStatus`
  - `POST /api/projects/[id]/motor {doi, modo}` → `{ok:true|false, motor:MotorResult}`; erros 400 (modo), 404 (DOI fora do lote), 413 (cota), 502, 503 (motor fora), 507 (disco)
  - `GET /api/projects/[id]/texto?article=` → `text/plain`; `POST` idem com corpo `text/plain` (restauração)
  - `PATCH {action:'downloadMode', mode}`; `PATCH {action:'incorporate', doi}` aceita linha com `result.motor.ok`
  - artigo do corpus: `source.kind==='motor'`, `texto:{key,bytes,chars,paginas,truncado,origem}`

- [ ] **Step 1: Write the failing integration test**

`tests/motor-integration.mjs`:

```js
import {createRequire} from 'node:module';import {realpathSync,readFileSync,readdirSync} from 'node:fs';import assert from 'node:assert/strict';
const require=createRequire(realpathSync('./node_modules/wrangler')+'/package.json');const {Miniflare}=require('miniflare');

// Motor falso: responde como o servico-python, por DOI. Toda saída de rede do
// Worker passa por aqui, então o teste não depende da internet.
const texto='Texto integral do artigo. '.repeat(40);
const identidade={ok:true,metodo:'doi_in_pdf',score:1,detalhe:'identidade confirmada'};
const respostas={
 '10.1234/ok':{ok:true,fonte:'unpaywall',fontes_tentadas:['unpaywall'],identidade,texto,paginas:3,chars:texto.length,texto_truncado:false},
 '10.1234/baixado':{ok:true,fonte:'pmc',fontes_tentadas:['pmc'],arquivo:'Silva_2021_Teste.pdf',identidade,texto:'',paginas:2,chars:0,texto_truncado:false,aviso:'sem_texto'},
 '10.1234/nada':{ok:false,erro:'Nenhuma fonte entregou o PDF.',fontes_tentadas:['unpaywall','pmc']},
};
const pedidos=[];
async function motor(req){
 const u=new URL(req.url);
 if(u.origin!=='http://motor.test')return new Response('rede bloqueada no teste',{status:599});
 if(u.pathname==='/saude')return Response.json({ok:true,recursos:['download_completo'],faltando:[],pasta_pdfs:'/dados/pdfs'});
 const b=await req.json();pedidos.push(b);
 if(b.doi==='10.1234/disco')return Response.json({detail:'Não foi possível usar a pasta /dados/pdfs: sem espaço'},{status:507});
 return Response.json(respostas[b.doi]);
}
const mf=new Miniflare({modules:[{type:'ESModule',path:'dist/server/index.js'},...readdirSync('dist/server',{recursive:true}).filter(f=>/\.(js|mjs)$/.test(f)&&f!=='index.js').map(f=>({type:'ESModule',path:'dist/server/'+f}))],compatibilityDate:'2026-05-15',compatibilityFlags:['nodejs_compat'],d1Databases:['DB'],r2Buckets:['BUCKET'],bindings:{ORBIS_ENGINE_URL:'http://motor.test'},outboundService:motor});
try{
 const db=await mf.getD1Database('DB'),r2=await mf.getR2Bucket('BUCKET');
 for(const f of readdirSync('drizzle').filter(f=>f.endsWith('.sql')).sort())for(const sql of readFileSync('drizzle/'+f,'utf8').split('--> statement-breakpoint'))if(sql.trim())await db.prepare(sql).run();
 const auth={'oai-authenticated-user-id':'test-a','oai-authenticated-user-email':'test-a@example.test'};
 async function req(path,method='GET',data){const headers={...auth};if(data!==undefined)headers['content-type']='application/json';const r=await mf.dispatchFetch('https://test.example'+path,{method,headers,body:data!==undefined?JSON.stringify(data):undefined});const raw=await r.text();return {status:r.status,data:raw.startsWith('{')||raw.startsWith('[')?JSON.parse(raw):raw}}
 const bytes=async id=>(await db.prepare('SELECT bytes FROM projects WHERE id=?').bind(id).first()).bytes;

 let r=await req('/api/projects','POST',{name:'Motor'});const id=r.data.id,path='/api/projects/'+id;
 const at=new Date().toISOString();
 for(const doi of ['10.1234/ok','10.1234/baixado','10.1234/nada','10.1234/disco'])
  await db.prepare('INSERT INTO search_items(project,doi,status,result,updated) VALUES(?,?,?,?,?)').bind(id,doi,'done',JSON.stringify({doi,found:true,title:'Artigo '+doi,authors:'Silva',year:'2021',journal:'Teste',pdfUrls:[]}),at).run();

 // Estado do motor para a interface.
 r=await req(path+'/motor');assert.equal(r.status,200);assert.equal(r.data.online,true);assert.equal(r.data.pdfDir,'/dados/pdfs');

 // Modo inválido e DOI fora do lote.
 assert.equal((await req(path+'/motor','POST',{doi:'10.1234/ok',modo:'tudo'})).status,400);
 assert.equal((await req(path+'/motor','POST',{doi:'10.9/fora',modo:'baixar'})).status,404);

 // Analisar: texto no R2, cota conta, metadados vão ao motor.
 r=await req(path+'/motor','POST',{doi:'10.1234/ok',modo:'analisar'});
 assert.equal(r.status,200,JSON.stringify(r.data));assert.equal(r.data.ok,true);
 const key=r.data.motor.texto.key;
 assert.equal(await (await r2.get(key)).text(),texto);
 assert.equal(await bytes(id),texto.length);
 assert.deepEqual([pedidos.at(-1).projeto,pedidos.at(-1).modo,pedidos.at(-1).titulo,pedidos.at(-1).autor],[id,'analisar','Artigo 10.1234/ok','Silva']);

 // Repetir o mesmo DOI não conta a cota duas vezes.
 await req(path+'/motor','POST',{doi:'10.1234/ok',modo:'analisar'});
 assert.equal(await bytes(id),texto.length);

 // Baixar sem texto extraível.
 r=await req(path+'/motor','POST',{doi:'10.1234/baixado',modo:'baixar'});
 assert.equal(r.data.motor.arquivo,'Silva_2021_Teste.pdf');assert.equal(r.data.motor.texto,null);assert.equal(r.data.motor.aviso,'sem_texto');

 // Não localizado é resultado, não erro; o motivo fica na linha.
 r=await req(path+'/motor','POST',{doi:'10.1234/nada',modo:'analisar'});
 assert.equal(r.status,200);assert.equal(r.data.ok,false);
 assert.equal((await req(path)).data.searches.find(x=>x.doi==='10.1234/nada').error,'Nenhuma fonte entregou o PDF.');

 // Disco cheio: 507 com a mensagem do motor.
 r=await req(path+'/motor','POST',{doi:'10.1234/disco',modo:'baixar'});
 assert.equal(r.status,507);assert.match(r.data.message,/sem espaço/);

 // Incorporar a partir do motor: sem PDF no R2.
 let p=(await req(path)).data;
 r=await req(path,'PATCH',{revision:p.revision,action:'incorporate',doi:'10.1234/ok'});assert.equal(r.status,200,JSON.stringify(r.data));
 p=(await req(path)).data;
 const art=p.state.articles.find(a=>a.doi==='10.1234/ok');
 assert.equal(art.source.kind,'motor');assert.equal(art.source.modo,'analisar');assert.equal(art.texto.key,key);assert.equal(art.identity.ok,true);
 assert.equal(p.documents.length,0,'nenhum PDF no R2');
 r=await req(path,'PATCH',{revision:p.revision,action:'incorporate',doi:'10.1234/baixado'});assert.equal(r.status,200);
 p=(await req(path)).data;
 assert.equal(p.state.articles.find(a=>a.doi==='10.1234/baixado').source.arquivoLocal,'Silva_2021_Teste.pdf');
 assert.equal((await req(path,'PATCH',{revision:p.revision,action:'incorporate',doi:'10.1234/nada'})).status,422);

 // Texto pela rota.
 r=await req(path+'/texto?article='+art.id);assert.equal(r.status,200);assert.equal(r.data,texto);
 assert.equal((await req(path+'/texto?article=inexistente')).status,404);

 // Modo salvo no projeto.
 p=(await req(path)).data;
 assert.equal((await req(path,'PATCH',{revision:p.revision,action:'downloadMode',mode:'x'})).status,400);
 assert.equal((await req(path,'PATCH',{revision:p.revision,action:'downloadMode',mode:'analisar'})).status,200);
 assert.equal((await req(path)).data.state.settings.downloadMode,'analisar');

 // Excluir o artigo: texto e cota liberados, DOI volta a exigir download.
 p=(await req(path)).data;
 r=await req(path,'PATCH',{revision:p.revision,action:'removeArticle',article:art.id,confirmation:art.id});assert.equal(r.status,200,JSON.stringify(r.data));
 assert.equal(await r2.get(key),null);assert.equal(await bytes(id),0);
 assert.equal((await req(path)).data.searches.find(x=>x.doi==='10.1234/ok').result.motor,undefined);

 // Limpar a busca apaga textos de DOIs não incorporados.
 await req(path+'/motor','POST',{doi:'10.1234/ok',modo:'analisar'});assert.equal(await bytes(id),texto.length);
 p=(await req(path)).data;
 assert.equal((await req(path,'PATCH',{revision:p.revision,action:'clearStage',stage:'search'})).status,200);
 assert.equal(await r2.get(key),null);assert.equal(await bytes(id),0);

 // Restauração: a chave do texto é reescrita para o projeto novo.
 r=await req('/api/projects','POST',{name:'Restaurado'});const id2=r.data.id,path2='/api/projects/'+id2;
 const st=(await req(path)).data.state;
 st.articles=[{id:'a1',filename:'a1.pdf',doi:'10.1234/ok',title:'T',authors:'',year:'',abstract:'',source:{kind:'motor',modo:'analisar'},texto:{key:id+'/texto/antigo.txt',chars:texto.length,paginas:3,truncado:false,bytes:texto.length,origem:'motor'}}];
 r=await req(path2,'PATCH',{revision:0,action:'restore',state:st,hash:'h',expected:[]});assert.equal(r.status,200,JSON.stringify(r.data));
 assert.ok((await req(path2)).data.state.articles[0].texto.key.startsWith(id2+'/texto/'));
 const up=await mf.dispatchFetch('https://test.example'+path2+'/texto?article=a1',{method:'POST',headers:{...auth,'content-type':'text/plain; charset=utf-8'},body:texto});
 assert.equal(up.status,200,await up.text());
 assert.equal((await req(path2+'/texto?article=a1')).data,texto);
 assert.equal(await bytes(id2),texto.length);
 console.log('motor-integration: ok');
}finally{await mf.dispose()}
```

- [ ] **Step 2: Build and run to verify it fails**

Run: `pnpm build && node tests/motor-integration.mjs`
Expected: FAIL — primeiro `assert` de `/motor` com status 404 (rota inexistente)

- [ ] **Step 3: Create `app/api/projects/[id]/motor/route.ts`**

```ts
import {env} from 'cloudflare:workers';
import {identity,owned,database,bucket,body,ok,fail,ApiError,requireRole} from '@/lib/server';
import {engineStatus,downloadViaEngine,EngineRefused} from '@/lib/python-bridge';
import {isMode,textKey,motorSummary} from '@/lib/motor-download';
const QUOTA=2*1024*1024*1024;
// A interface pergunta isto para decidir se oferece os dois modos.
export async function GET(r:Request,{params}:any){try{const actor=await identity(r),{id}=await params;await owned(id,actor);return ok(await engineStatus(env))}catch(e){return fail(e)}}
// Fase 1 do lote: o motor busca o PDF de UM artigo. Não toca no estado do
// projeto — só na linha do DOI —, por isso o navegador pode chamar vários em
// paralelo sem conflito de revisão.
export async function POST(r:Request,{params}:any){try{
 const actor=await identity(r),{id}=await params,p=await owned(id,actor);requireRole(p,['owner','editor']);if(p.archived)throw new ApiError(409,'Projeto em exclusão.');
 const b=await body(r),doi=String(b.doi||''),modo=b.modo;if(!isMode(modo))throw new ApiError(400,'Modo de download inválido.');
 const db=database(),row=await db.prepare('SELECT result FROM search_items WHERE project=? AND doi=?').bind(id,doi).first<any>();if(!row)throw new ApiError(404,'DOI não está no lote.');
 const meta=JSON.parse(row.result||'null');if(!meta?.found)throw new ApiError(400,'Consulte o DOI antes de buscar o PDF.');
 let res;
 try{res=await downloadViaEngine(env,{doi,projeto:id,modo,titulo:meta.title||'',autor:meta.authors||'',ano:String(meta.year||''),periodico:meta.journal||''})}
 catch(e:any){if(e instanceof EngineRefused)throw new ApiError(e.status===507?507:502,e.message);throw e}
 if(!res)throw new ApiError(503,'O motor não está no ar. O lote foi pausado; suba o motor e retome.');
 const at=new Date().toISOString();
 if(!res.ok){
  const motor=motorSummary(res,modo,null);
  await db.prepare('UPDATE search_items SET result=?,error=?,updated=? WHERE project=? AND doi=?').bind(JSON.stringify({...meta,motor}),motor.erro,at,id,doi).run();
  return ok({ok:false,motor});
 }
 let texto=null;
 if(res.texto){
  // Mesmo endereço por DOI: repetir troca o texto e ajusta só a diferença da cota.
  const bytes=new TextEncoder().encode(res.texto),key=await textKey(id,doi),previous=meta.motor?.texto?.key===key?Number(meta.motor.texto.bytes||0):0,delta=bytes.length-previous;
  const quota=await db.prepare('UPDATE projects SET bytes=MAX(0,bytes+?) WHERE id=? AND archived=0 AND bytes+?<=?').bind(delta,id,delta,QUOTA).run();
  if(!quota.meta.changes)throw new ApiError(413,'O projeto atingiu o limite de 2 GB.');
  try{await bucket().put(key,bytes,{httpMetadata:{contentType:'text/plain; charset=utf-8'}})}
  catch(e){await db.prepare('UPDATE projects SET bytes=MAX(0,bytes-?) WHERE id=?').bind(delta,id).run().catch(()=>{});throw e}
  texto={key,bytes:bytes.length};
 }
 const motor=motorSummary(res,modo,texto);
 await db.prepare('UPDATE search_items SET result=?,error=NULL,updated=? WHERE project=? AND doi=?').bind(JSON.stringify({...meta,motor}),at,id,doi).run();
 return ok({ok:true,motor});
}catch(e){return fail(e)}}
```

- [ ] **Step 4: Create `app/api/projects/[id]/texto/route.ts`**

```ts
import {identity,owned,database,bucket,ok,fail,ApiError,requireRole} from '@/lib/server';
import {textKey} from '@/lib/motor-download';
const QUOTA=2*1024*1024*1024,MAX_TEXT=2*1024*1024;
function article(p:any,r:Request){const s=JSON.parse(p.state),a=s.articles.find((x:any)=>x.id===new URL(r.url).searchParams.get('article'));if(!a?.texto?.key)throw new ApiError(404,'Este artigo não tem texto extraído.');return a;}
export async function GET(r:Request,{params}:any){try{const actor=await identity(r),{id}=await params,p=await owned(id,actor),a=article(p,r);const o=await bucket().get(a.texto.key);if(!o)throw new ApiError(404,'Texto indisponível.');return new Response(o.body,{headers:{'content-type':'text/plain; charset=utf-8','cache-control':'private, no-store','x-content-type-options':'nosniff'}})}catch(e){return fail(e)}}
// Restauração de backup: grava o texto no endereço que a ação `restore` já
// recalculou para este projeto. Não mexe no estado, como o upload de PDF.
export async function POST(r:Request,{params}:any){let reserved=0,project='';try{
 const actor=await identity(r),{id}=await params;project=id;const p=await owned(id,actor);requireRole(p,['owner','editor']);const a=article(p,r);
 if(a.texto.key!==await textKey(id,a.doi||a.id))throw new ApiError(409,'O texto não pertence a este projeto.');
 const bytes=new TextEncoder().encode(await r.text());if(!bytes.length)throw new ApiError(400,'Texto vazio.');if(bytes.length>MAX_TEXT)throw new ApiError(413,'Texto acima de 2 MB.');
 if(await bucket().head(a.texto.key))return ok({existing:true});
 const db=database(),quota=await db.prepare('UPDATE projects SET bytes=bytes+? WHERE id=? AND archived=0 AND bytes+?<=?').bind(bytes.length,id,bytes.length,QUOTA).run();if(!quota.meta.changes)throw new ApiError(413,'O projeto atingiu o limite de 2 GB.');reserved=bytes.length;
 await bucket().put(a.texto.key,bytes,{httpMetadata:{contentType:'text/plain; charset=utf-8'}});reserved=0;
 return ok({size:bytes.length});
}catch(e){if(reserved)await database().prepare('UPDATE projects SET bytes=MAX(0,bytes-?) WHERE id=?').bind(reserved,project).run().catch(()=>{});return fail(e)}}
```

- [ ] **Step 5: Desvio para o motor em `lib/incorporate-pdf.ts`**

1. Imports: acrescentar `import {motorArticle} from './motor-download';`
2. Trocar `throw new ApiError(400,'Metadados não encontrados.');if(!Array.isArray(metadata.pdfUrls)` por:

```ts
throw new ApiError(400,'Metadados não encontrados.');if(metadata.motor?.ok)return incorporateFromMotor(p,actor,state,doi,metadata);if(!Array.isArray(metadata.pdfUrls)
```

3. No fim do arquivo:

```ts
// O motor já baixou, validou a identidade pelo conteúdo e extraiu o texto na
// fase 1. Aqui só se grava o registro: nenhum PDF vai para o R2 (no modo
// "baixar" ele está na pasta local; no "analisar" já foi descartado).
async function incorporateFromMotor(p:any,actor:string,state:any,doi:string,metadata:any){
 const db=database(),articleId=crypto.randomUUID(),at=new Date().toISOString();
 const filename='artigo_'+(state.articles.length+1)+'_'+doi.replace(/[^a-z0-9.-]/gi,'_')+'.pdf';
 state.articles.push(motorArticle(doi,metadata,articleId,filename,at));
 const result=await commit(p,actor,state,'incorporate');
 await db.prepare('INSERT INTO pdf_attempts(id,project,article,status,reason,created) VALUES(?,?,?,?,?,?)').bind(crypto.randomUUID(),p.id,articleId,'saved','PDF validado pelo motor (modo '+metadata.motor.modo+')',at).run().catch(()=>{});
 await db.prepare('UPDATE search_items SET error=NULL WHERE project=? AND doi=?').bind(p.id,doi).run().catch(()=>{});
 return {...result,article:articleId};
}
```

- [ ] **Step 6: Ações do `PATCH` em `app/api/projects/[id]/route.ts`**

1. Import no topo: `import {isMode,textKey,orphanTexts} from '@/lib/motor-download';`
2. Confirmar que o handler termina com o commit genérico usado por `notes` (procure `return ok(await commit(p,actor,s,b.action))` depois da cadeia de `else if`). Nova ação, inserida imediatamente antes de `}else if(b.action==='notes'){`:

```ts
}else if(b.action==='downloadMode'){
 requireRole(p,['owner','editor']);if(!isMode(b.mode))throw new ApiError(400,'Modo de download inválido.');s.settings={...s.settings,downloadMode:b.mode};
```

3. `removeArticle` — trocar
`released=docs.reduce((sum:number,d:any)=>sum+Number(d.size||0),0);if(docs.length)await bucket().delete(docs.map((d:any)=>d.key));`
por:

```ts
released=docs.reduce((sum:number,d:any)=>sum+Number(d.size||0),0)+Number(article.texto?.bytes||0),keys=[...docs.map((d:any)=>d.key),...(article.texto?.key?[article.texto.key]:[])];if(keys.length)await bucket().delete(keys);
```

e, no `db.batch([...])` da mesma ação, depois de `db.prepare('DELETE FROM pdf_attempts WHERE project=? AND article=?').bind(id,article.id)`, acrescentar:

```ts
,db.prepare("UPDATE search_items SET result=json_remove(result,'$.motor') WHERE project=? AND doi=? AND result IS NOT NULL").bind(id,article.doi||'')
```

4. `clearStage` — trocar
`if(b.stage==='search'){await database().prepare('DELETE FROM search_items WHERE project=?').bind(id).run();`
por:

```ts
if(b.stage==='search'){const db=database(),orphan=orphanTexts((await db.prepare('SELECT result FROM search_items WHERE project=?').bind(id).all()).results as any[],s.articles);for(let n=0;n<orphan.keys.length;n+=1000)await bucket().delete(orphan.keys.slice(n,n+1000));if(orphan.bytes)await db.prepare('UPDATE projects SET bytes=MAX(0,bytes-?) WHERE id=?').bind(orphan.bytes,id).run();await db.prepare('DELETE FROM search_items WHERE project=?').bind(id).run();
```

5. `restore` — depois de `importExpected:Array.isArray(b.expected)?b.expected:[]});` acrescentar:

```ts
for(const a of s.articles)if(a.texto?.key)a.texto={...a.texto,key:await textKey(id,a.doi||a.id)};
```

- [ ] **Step 7: Build and run the tests**

Run: `pnpm build && node tests/motor-integration.mjs && node tests/worker-integration.mjs`
Expected: `motor-integration: ok` e o teste antigo continua passando.

- [ ] **Step 8: Checkpoint** — rotas prontas e cobertas.

---

### Task 6: Texto completo na PCC automática

**Files:**
- Modify: `lib/ai-runner.ts`
- Modify: `lib/ai-analysis.ts` (instrução da PCC em `makeAIPackage`)
- Modify: `app/api/projects/[id]/route.ts` (ação `runAI`)
- Test: `tests/ai-runner.mjs` (acrescentar bloco ao fim)

**Interfaces:**
- Consumes: `a.texto.key` do artigo (Task 5); `bucket()` de `lib/server.ts`.
- Produces: `PCC_BATCH_SIZE=2`, `TEXT_LIMIT=60000`, `withFullText(project,items,readText)`, e `runAITriage(project,stage,provider,{batchSize?,onProgress?,readText?:(key:string)=>Promise<string|null>})`. Item da PCC ganha `texto_completo:string` e `texto_truncado:boolean` quando há texto.

- [ ] **Step 1: Write the failing test** — acrescentar ao fim de `tests/ai-runner.mjs`:

```js
// ---------------------------------------------------------------------------
// Texto completo: só na PCC, cortado em TEXT_LIMIT, lotes de 2
// ---------------------------------------------------------------------------
{
 const triado={version:1,decision:'incluir',reason:'',answers:['Sim'],actor:'x'};
 const project={id:'p',documents:[],state:{notes:'',protocol:{population:'P',concept:'C',context:'C',questions:['Q1'],inclusion:'I1',exclusion:'E1',version:1,approved:true},articles:[
  {id:'a',filename:'a.pdf',title:'A',authors:'',doi:'10.1/a',abstract:'R',triage:triado,texto:{key:'k/a'}},
  {id:'b',filename:'b.pdf',title:'B',authors:'',doi:'10.1/b',abstract:'R',triage:triado,texto:{key:'k/b',truncado:false}},
  {id:'c',filename:'c.pdf',title:'C',authors:'',doi:'10.1/c',abstract:'R',triage:triado}]}};
 const longo='x'.repeat(runner.TEXT_LIMIT+10),textos={'k/a':'curto','k/b':longo},lotes=[];
 const provider={name:'fake',model:'m',complete:async(_s,u)=>{lotes.push(JSON.parse(u).artigos);return '{"items":[]}'}};
 await runner.runAITriage(project,'pcc',provider,{readText:async k=>textos[k]??null});
 assert.deepEqual(lotes.map(l=>l.length),[2,1],'PCC em lotes de 2');
 const item=id=>lotes.flat().find(i=>i.article_id===id);
 assert.equal(item('a').texto_completo,'curto');assert.equal(item('a').texto_truncado,false);
 assert.equal(item('b').texto_completo.length,runner.TEXT_LIMIT);assert.equal(item('b').texto_truncado,true);
 assert.equal('texto_completo' in item('c'),false,'sem texto, sem campo');

 lotes.length=0;
 await runner.runAITriage(project,'triagem',provider,{readText:async k=>textos[k]});
 assert.ok(lotes.flat().length&&lotes.flat().every(i=>!('texto_completo' in i)),'triagem não recebe texto');

 lotes.length=0;
 await runner.runAITriage(project,'pcc',provider,{readText:async()=>{throw new Error('R2 fora')}});
 assert.equal(lotes.flat().length,3,'falha ao ler texto não derruba a PCC');
 assert.ok(lotes.flat().every(i=>!('texto_completo' in i)));

 assert.match(ai.makeAIPackage(project,'pcc').instrucoes,/texto_completo/);
}
console.log('ai-runner (texto completo): ok');
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/ai-runner.mjs`
Expected: FAIL — `assert.deepEqual` de lotes (`[3]` em vez de `[2,1]`) ou `TEXT_LIMIT` indefinido

- [ ] **Step 3: Write the implementation**

Em `lib/ai-runner.ts`:

1. Depois de `export const BATCH_SIZE=5;`:

```ts
// Na PCC cada artigo leva o texto completo; 2 por chamada cabem no contexto
// dos provedores com folga.
export const PCC_BATCH_SIZE=2;
export const TEXT_LIMIT=60000;

// Anexa o texto extraído pelo motor aos itens da PCC. Texto que não pôde ser
// lido deixa o item como está: a IA responde null, como já faz sem PDF.
export async function withFullText(project:any,items:any[],readText:(key:string)=>Promise<string|null>){
 const out:any[]=[];
 for(const item of items){
  const a=project.state.articles.find((x:any)=>x.id===item.article_id);
  const t=a?.texto?.key?await readText(a.texto.key).catch(()=>null):null;
  out.push(t?{...item,texto_completo:t.slice(0,TEXT_LIMIT),texto_truncado:t.length>TEXT_LIMIT||!!a.texto.truncado}:item);
 }
 return out;
}
```

2. Na assinatura de `runAITriage`, trocar `opts:{batchSize?:number;onProgress?:(done:number,total:number)=>void}={}` por `opts:{batchSize?:number;onProgress?:(done:number,total:number)=>void;readText?:(key:string)=>Promise<string|null>}={}`.
3. Trocar `const itens:any[]=Array.isArray(pkg.items)?pkg.items:[];` por:

```ts
 const base:any[]=Array.isArray(pkg.items)?pkg.items:[];
 const itens=stage==='pcc'&&opts.readText?await withFullText(project,base,opts.readText):base;
```

4. Trocar `const lotes=chunk(itens,opts.batchSize??BATCH_SIZE);` por `const lotes=chunk(itens,opts.batchSize??(stage==='pcc'?PCC_BATCH_SIZE:BATCH_SIZE));`

Em `lib/ai-analysis.ts`, na instrução da PCC dentro de `makeAIPackage`, trocar `'Leia os PDFs anexados, conferindo a correspondência de cada arquivo.` por:

```ts
'Leia os PDFs anexados ou, quando o artigo trouxer texto_completo (texto extraído do PDF; texto_truncado indica corte), leia esse texto, conferindo a correspondência de cada arquivo.
```

Em `app/api/projects/[id]/route.ts` (ação `runAI`), trocar `{batchSize:Number(b.batchSize)||undefined}` por:

```ts
{batchSize:Number(b.batchSize)||undefined,readText:async(key:string)=>{const o=await bucket().get(key);return o?await o.text():null}}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node tests/ai-runner.mjs && node tests/ai-analysis.mjs`
Expected: os dois passam; `ai-runner (texto completo): ok`

- [ ] **Step 5: Checkpoint** — PCC automática lê o texto.

---

### Task 7: Interface — escolha do modo, lote pelo motor, cartão, ZIP da PCC e backup

**Files:**
- Modify: `app/workspace.tsx`
- Modify: `app/corpus-article-card.tsx`
- Modify: `app/ai-controls.tsx`

**Interfaces:**
- Consumes: `GET/POST /api/projects/[id]/motor`, `GET/POST /api/projects/[id]/texto`, `PATCH action:'downloadMode'` (Task 5); `motorNote` (Task 4).
- Produces: nada consumido por outras tarefas.

- [ ] **Step 1: Baseline de tipos**

Run: `pnpm exec tsc --noEmit -p . 2>&1 | tail -5`
Anote o número de erros existentes; nenhuma edição desta tarefa pode aumentá-lo.

- [ ] **Step 2: `api()` expõe o status HTTP** (`app/workspace.tsx`)

Trocar `throw Error(e.message)}return r.json()}` (dentro de `async function api`) por:

```ts
throw Object.assign(Error(e.message),{status:r.status})}return r.json()}
```

- [ ] **Step 3: Estado do motor** — logo depois da linha `const state=project?.state,articles=state?.articles||[],docs=project?.documents||[],searches=project?.searches||[];` acrescentar:

```ts
 const [motor,setMotor]=useState<any>(null);
 useEffect(()=>{setMotor(null);if(project?.id)api(endpoint(project.id)+'/motor').then(setMotor).catch(()=>setMotor(null));},[project?.id]);
 const motorOnline=!!motor?.online&&(motor.resources||[]).includes('download_completo'),downloadMode=state?.settings?.downloadMode||'baixar';
```

- [ ] **Step 4: Lote pelo motor**

Trocar o início de `incorporateFound`:
`async function incorporateFound(start:any,onlyDoi=''){let p=start,added=0,failed=0;for(const row of p.searches){if(onlyDoi&&row.doi!==onlyDoi)continue;if(!row.result?.found||!(row.result?.pdfUrls||[]).length||`
por:
`async function incorporateFound(start:any,onlyDoi='',viaMotor=false){let p=start,added=0,failed=0;for(const row of p.searches){if(onlyDoi&&row.doi!==onlyDoi)continue;if(!row.result?.found||(viaMotor?!row.result?.motor?.ok:!(row.result?.pdfUrls||[]).length)||`

Logo depois da função `incorporateFound`, acrescentar:

```ts
 // Fase 1 com o motor: 4 artigos por vez, sem tocar no estado do projeto.
 // Motor fora (503), disco (507) e cota (413) valem para todos os próximos: pausam o lote.
 async function downloadWithEngine(start:any,onlyDoi=''){const modo=start.state.settings?.downloadMode||'baixar';const items=start.searches.filter((x:any)=>(!onlyDoi||x.doi===onlyDoi)&&x.result?.found&&!x.result?.motor?.ok&&!start.state.articles.some((a:any)=>a.doi===x.doi));let cursor=0;async function work(){while(!stop.current&&cursor<items.length){const item=items[cursor++];try{await api(endpoint(start.id)+'/motor','POST',{doi:item.doi,modo})}catch(e:any){setMessage(e.message);if([503,507,413].includes(e.status))stop.current=true}await refresh(start.id)}}await Promise.all([work(),work(),work(),work()]);return !stop.current}
 // Sem motor, o caminho de sempre (Worker → R2). Com motor, só entra o que ele validou.
 async function collect(p:any,onlyDoi=''){if(!motorOnline)return incorporateFound(p,onlyDoi);if(!await downloadWithEngine(p,onlyDoi))return null;return incorporateFound(await refresh(p.id),onlyDoi,true)}
```

Trocar `incorporateAll` inteira:
`async function incorporateAll(){await task(async()=>{const result=await incorporateFound(await refresh());toast.success(result.added?`${result.added} PDF(s) incorporado(s) e enviado(s) à triagem.`:'Nenhum novo PDF válido foi incorporado.')})}`
por:

```ts
async function incorporateAll(){if(running||busy)return;setRunning(true);stop.current=false;setMessage('');try{const result=await collect(await refresh());if(!result){toast.info('Lote pausado. Pendências salvas.');return}toast.success(result.added?`${result.added} PDF(s) incorporado(s) e enviado(s) à triagem.`:'Nenhum novo PDF válido foi incorporado.')}catch(e:any){setMessage(e.message)}finally{setRunning(false);await refresh().catch(()=>{})}}
```

Em `incorporateOne`, trocar `const result=await incorporateFound(await refresh(),doi);` por `stop.current=false;const result=await collect(await refresh(),doi);if(!result)return;`

Em `runSearch`, trocar `const result=await incorporateFound(await refresh(p.id));toast.success(` por `const result=await collect(await refresh(p.id));if(!result)toast.info('Lote pausado. Pendências salvas.');else toast.success(`

- [ ] **Step 5: Seletor e botão**

Trocar o botão
`<Button variant="outline" disabled={disabled||!searches.some((x:any)=>x.result?.found&&(x.result?.pdfUrls||[]).length)} onClick={incorporateAll}>Baixar e incorporar PDFs encontrados</Button>`
por:

```tsx
<Button variant="outline" disabled={disabled||running||!searches.some((x:any)=>x.result?.found&&(motorOnline||(x.result?.pdfUrls||[]).length))} onClick={incorporateAll}>{!motorOnline?'Baixar e incorporar PDFs encontrados':downloadMode==='baixar'?'Analisar e baixar PDFs':'Analisar PDFs no sistema'}</Button>
```

Imediatamente antes de `{!['owner','editor'].includes(project.access_role)&&<p className="notice">Sua função neste projeto permite consultar a busca`, inserir:

```tsx
{motorOnline&&<fieldset><legend>PDFs dos artigos encontrados</legend><RadioGroup className="choices" value={downloadMode} onValueChange={v=>task(async()=>{await mutate('downloadMode',{mode:v})})}><label><RadioGroupItem value="baixar" disabled={disabled||running}/>Analisar e baixar todos os PDFs — salvos em {motor.pdfDir}/{project.id}</label><label><RadioGroupItem value="analisar" disabled={disabled||running}/>Só analisar no sistema — o PDF é lido e descartado; nada é salvo no seu computador</label></RadioGroup></fieldset>}
```

- [ ] **Step 6: Backup com os textos**

Em `pack`, imediatamente antes de ` const content=JSON.stringify(data),manifest=`, inserir:

```ts
data.orbis_web.textos=[];for(const a of p.state.articles){if(!a.texto?.key)continue;const r=await fetch(endpoint(p.id)+'/texto?article='+encodeURIComponent(a.id));if(!r.ok)continue;const path='textos/'+String(data.orbis_web.textos.length+1).padStart(4,'0')+'.txt';files.push({name:path,blob:await r.blob()});data.orbis_web.textos.push({arquivo:a.filename,path});}
```

Em `restoreImport`, logo depois do laço de PDFs (`...await api(endpoint(id)+'/pdf?article='+encodeURIComponent(a.id),'POST',d.entries.get(pdf.path));}`), inserir:

```ts
for(const t of d.project.orbis_web?.textos||[]){const a=d.state.articles.find((a:any)=>a.filename===t.arquivo),blob=d.entries.get(t.path);if(!a||!blob)continue;const r=await fetch(endpoint(id)+'/texto?article='+encodeURIComponent(a.id),{method:'POST',headers:{'content-type':'text/plain; charset=utf-8'},body:blob});if(!r.ok){const e:any=await r.json().catch(()=>({}));throw Error(e.message||'Não foi possível restaurar o texto de '+t.arquivo)}}
```

- [ ] **Step 7: Cartão do artigo** (`app/corpus-article-card.tsx`)

Import: `import {motorNote} from '@/lib/motor-download';`
Trocar `{attemptReason||(article.source?.kind==='local'?` por `{motorNote(article)||attemptReason||(article.source?.kind==='local'?`

- [ ] **Step 8: ZIP da PCC manual** (`app/ai-controls.tsx`)

Em `exportPackage`, trocar `if(!doc){missing.push(item.arquivo);continue}` por:

```ts
if(!doc){const a=project.state.articles.find((x:any)=>x.id===item.article_id);if(a?.texto?.key){const t=await fetch('/api/projects/'+project.id+'/texto?article='+encodeURIComponent(a.id));if(t.ok){files.push({name:'textos/'+item.arquivo.replace(/[\\/]/g,'_')+'.txt',blob:await t.blob()});continue}}missing.push(item.arquivo);continue}
```

- [ ] **Step 9: Tipos, build e regressão**

Run: `pnpm exec tsc --noEmit -p . 2>&1 | tail -5` — mesmo número de erros do Step 1.
Run: `pnpm build && for t in tests/*.mjs; do node "$t" || exit 1; done && (cd servico-python && .venv/bin/python -m pytest tests -q)`
Expected: tudo passa.

- [ ] **Step 10: Checkpoint** — interface completa.

---

### Task 8: Verificação real com o motor

**Files:** nenhum (só execução e relato).

- [ ] **Step 1:** Recriar as tabelas locais se preciso (ver `COMO_RODAR.md`) e subir tudo: `python3 start.py`. Conferir `curl -s http://127.0.0.1:8900/saude` → `recursos` contém `download_completo` e `pasta_pdfs`.
- [ ] **Step 2:** No ORBIS, num projeto novo, colar e buscar: `10.1371/journal.pone.0265123`, `10.7717/peerj.4375`, `10.1038/s41586-020-2649-2`. Conferir que o seletor aparece com o caminho da pasta.
- [ ] **Step 3:** Modo **baixar** → "Analisar e baixar PDFs". Conferir: `ls motor/pdfs/<id-do-projeto>/` tem os PDFs e o `Relatório.txt`; os artigos estão no corpus com "PDF na pasta local"; "Baixar PDFs armazenados (ZIP)" não inclui esses PDFs.
- [ ] **Step 4:** Excluir um artigo, trocar para **só analisar**, rodar de novo. Conferir que nenhum PDF novo aparece em `motor/pdfs/` nem em `/tmp/orbis-*`, e o cartão diz "PDF lido e descartado; texto disponível".
- [ ] **Step 5:** Com chave de IA configurada, triar como "incluir" e rodar a PCC automática; conferir que as justificativas citam o texto. Sem chave, exportar o ZIP da PCC e conferir `textos/*.txt`.
- [ ] **Step 6:** Parar o motor no meio de um lote: o lote pausa com "O motor não está no ar"; subir e retomar conclui.
- [ ] **Step 7:** Relatar o resultado de cada passo como ocorreu, inclusive fontes que bloquearem.
