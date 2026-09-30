# Parte B — Extração para Markdown com imagens: plano

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** O texto de cada artigo baixado pelo motor chega ao ORBIS em Markdown (`pymupdf4llm`); no modo "baixar" o `.md` e as imagens ficam ao lado do PDF.

**Architecture:** `servico-python/extracao.py` roda o `pymupdf4llm` num processo filho com prazo e cai para o texto simples do PyMuPDF quando falha. `baixar.py` passa a extrair do arquivo (e não dos bytes) depois de guardar o PDF, grava o `.md` e informa `formato`, `imagens`, `pasta_imagens`, `arquivo_md`. O ORBIS guarda o `formato` em `article.texto`, serve `text/markdown` e mostra o formato na ficha.

**Tech Stack:** Python (FastAPI, pytest, pymupdf4llm já no `motor/requirements.txt`), TypeScript (Worker), `node tests/*.mjs`.

**Spec:** `docs/planos/evolucao-orbis-design.md` (parte B)

## Global Constraints

- Opções lidas do ambiente a cada chamada (a tela de Configurações, parte D, grava no `motor/.env` e em `os.environ`): `ORBIS_EXTRAIR_MARKDOWN` (padrão `1`), `ORBIS_SALVAR_IMAGENS` (padrão `1`), `ORBIS_EXTRACAO_PRAZO` (padrão 90 s, 5–600).
- Modo "analisar": nada fica no disco (nem `.md`, nem imagens).
- Falha ou prazo estourado da extração → texto simples, `formato: 'texto'`, `aviso_extracao` com o motivo; o artigo entra assim mesmo.
- Limite de texto enviado ao ORBIS mantido (`LIMITE_TEXTO`, com `texto_truncado`); o `.md` no disco é inteiro.
- Links das imagens no `.md` são relativos (`<nome>_imagens/...`).
- Testes do motor: `cd servico-python && ../motor/.venv/bin/python -m pytest -q`.

## Review Focus

1. `pymupdf4llm` trava num PDF → o filho é morto no prazo, o artigo entra com texto simples, sem pasta de imagens pela metade. Teste: Task 1 (prazo estourado).
2. Modo "analisar" com imagens ligadas → nenhuma imagem nem `.md` no disco. Teste: Task 2.
3. Baixar o mesmo DOI de novo → `.md` e pasta de imagens substituídos, não duplicados. Teste: Task 2.
4. Texto antigo (sem `formato`) continua servido como `text/plain`. Teste: Task 3.

---

### Task 1: `extracao.py`

**Files:** Create `servico-python/extracao.py`, `servico-python/tests/test_extracao.py`.

**Produces:** `opcoes()->{markdown:bool,imagens:bool,prazo:int}`; `texto_simples(caminho)->(texto,paginas)`; `markdown_no_filho(caminho,pasta_imagens,prazo,comando=None)->str` (lança `TimeoutError`/`RuntimeError`); `extrair(caminho,pasta_imagens=None,*,markdown=True,prazo=90,rodar=markdown_no_filho)->{texto,formato,paginas,imagens,aviso?}`; `extrair_conforme_opcoes(caminho,pasta_imagens)`.

- [ ] Step 1: testes — PDF real pequeno gerado no teste (título + imagem) → Markdown com o título e link relativo; imagens gravadas na pasta; sem pasta de imagens → nada gravado; `rodar` que falha → texto simples e aviso; prazo estourado (comando que dorme) → texto simples, aviso de prazo e pasta de imagens removida; `opcoes()` lê o ambiente e limita o prazo; `markdown=False` → texto simples sem chamar o filho.
- [ ] Step 2: `pytest tests/test_extracao.py` → FAIL (módulo não existe).
- [ ] Step 3: implementar.
- [ ] Step 4: `pytest -q` → verde.

### Task 2: `baixar.py` e `main.py` usam a extração

**Files:** Modify `servico-python/baixar.py`, `servico-python/main.py`, `servico-python/tests/test_baixar.py`, `servico-python/tests/test_rota_baixar.py`.

**Consumes:** Task 1. `extrair` injetado passa a ser `(caminho, pasta_imagens|None) -> dict`.

- Modo "baixar": guarda o PDF, extrai do arquivo guardado com `pasta_imagens=<pasta>/<stem>_imagens`, grava `<stem>.md` quando `formato=='markdown'`; resposta com `formato`, `imagens`, `pasta_imagens` (se houver imagens), `arquivo_md`.
- Modo "analisar": extrai do arquivo temporário com `pasta_imagens=None`.
- Repetir o DOI apaga a pasta de imagens anterior antes de extrair.

- [ ] Step 1: testes novos (grava `.md` e imagens; analisar não deixa nada; repetir substitui; formato na resposta; aviso de extração repassado) e ajuste dos existentes para a assinatura nova.
- [ ] Step 2: `pytest -q` → FAIL.
- [ ] Step 3: implementar.
- [ ] Step 4: `pytest -q` → verde.

### Task 3: ORBIS guarda e mostra o formato

**Files:** Modify `lib/python-bridge.ts`, `lib/motor-download.ts`, `app/api/projects/[id]/motor/route.ts`, `app/api/projects/[id]/texto/route.ts`, `app/corpus-article-card.tsx`, `app/workspace.tsx` (backup: extensão `.md`), `tests/motor-download.mjs`, `tests/motor-integration.mjs`.

- `motorSummary` guarda `texto.formato` (`markdown`|`texto`), `arquivoMd`, `pastaImagens`, `imagens`, `avisoExtracao`; `motorArticle` leva ao artigo; `motorNote` cita o `.md` e a pasta das imagens.
- `textoTipo(a)` → `text/markdown; charset=utf-8` ou `text/plain; charset=utf-8`; usado no `GET /texto` e no `put` do R2.
- Ficha do artigo: "Texto extraído: Markdown · N páginas" com **Abrir texto** e **Baixar .md**.

- [ ] Step 1: testes em `tests/motor-download.mjs` (formato no resumo e no artigo; texto antigo = `texto`; nota com o `.md`) e em `tests/motor-integration.mjs` (GET `/texto` devolve `text/markdown` quando o motor devolve Markdown).
- [ ] Step 2: rodar → FAIL.
- [ ] Step 3: implementar.
- [ ] Step 4: `pnpm exec tsc --noEmit`, testes `.mjs`, `pnpm build && node tests/motor-integration.mjs` → verdes; commit da parte B.
