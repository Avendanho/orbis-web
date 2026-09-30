# Parte A — Triagem de títulos e resumos antes do download: plano

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Os DOIs do lote viram registros triados por título e resumo antes de qualquer download; só os incluídos são baixados e entram no corpus com a decisão copiada.

**Architecture:** Tabela `screening` no D1 (uma linha por registro, fora do estado do projeto). Regras puras em `lib/screening.ts`; ações que gravam em `app/api/projects/[id]/screening/route.ts`; o `GET` do projeto passa a devolver as linhas de triagem, e a tela pagina e filtra no navegador. `incorporate` e a rota do motor exigem decisão `incluir` na versão atual.

**Tech Stack:** TypeScript (Worker/vinext), D1, testes `node tests/*.mjs` com carregador de TS, Miniflare em `tests/worker-integration.mjs`.

**Spec:** `docs/planos/evolucao-orbis-design.md` (parte A)

## Global Constraints

- Mesmas regras da triagem atual: protocolo aprovado; todas as perguntas respondidas; exclusão exige justificativa (`triageDecision` com `questionRules`); revisor pode triar; IA só sugere.
- Decisão com `version` diferente da do protocolo conta como pendente.
- Texto de interface em português; estilo compacto do repositório.
- Commits direto na `main` (autorizado), cada um com a suíte verde.

## Review Focus

1. Registro sem resumo → a IA recebe `resumo_status: nao_localizado` e a tela oferece **Buscar resumo de novo**. Teste: Task 1 (pacote de IA).
2. Protocolo muda de versão depois da triagem → decisões antigas viram pendentes e **Baixar os incluídos** não baixa nada delas. Teste: Task 1 (`situacao`, `incluidosParaBaixar`).
3. Sugestão de IA feita com resumo antigo (resumo atualizado depois) → não pode ser aceita. Teste: Task 1 (`sugestaoAtual`).
4. DOI incorporado sem triagem (chamada direta à API) → 409. Teste: Task 2 (worker-integration).
5. Backup/restauração → decisões de triagem voltam. Teste: Task 2 (worker-integration).

---

### Task 1: Regras da triagem pré-download

**Files:** Create `lib/screening.ts`, `tests/screening.mjs`. Modify `lib/ai-analysis.ts` (exportar `aiRows`; `makeAIPackage` aceita `itens` opcionais).

**Produces (`lib/screening.ts`):**
- `type Registro={doi,title,authors,year,journal,abstract,abstractSource}`
- `type Sugestao={provider,model,version,criteriaHash,sourceHash,decision:'incluir'|'excluir',questions:AIRow[],at}`
- `type LinhaTriagem={doi,decision:string|null,answers:string[],reasons:string[],reason:string,actor:string,version:number|null,source:string,ai:Sugestao|null}`
- `POR_PAGINA=50`
- `registroDe(row)`, `pseudoArtigo(reg)`, `registroHash(reg)`
- `situacao(linha,versao):'pendente'|'incluir'|'excluir'`
- `listar(searches,linhas,protocolo,{filtro,busca,pagina})→{itens,total,paginas,contagens}`
- `decidir(protocolo,answers,reason,reasons,actor)→Decisao`
- `pacoteIA(projectId,protocolo,registros)` (mesmo formato de `makeAIPackage` de triagem)
- `sugestaoDe(item,protocolo,provider,model,at)`, `sugestaoAtual(s,reg,protocolo)`, `aceitar(s,protocolo,actor)`
- `triagemParaArtigo(linha)`, `incluidosParaBaixar(linhas,versao,articles)`
- `contagensPrisma(searches,linhas,articles,versao)`

- [ ] Step 1: escrever `tests/screening.mjs` cobrindo cada função (registro sem metadados é ignorado; filtros e contagens; paginação; versão antiga é pendente; decisão inválida/exclusão sem justificativa lançam; pacote com `resumo_status` e hash; sugestão lida de uma resposta ORBIS_AI_RESULTS_V1; sugestão desatualizada quando o resumo muda; aceitar gera decisão com origem `ia_accepted`; `incluidosParaBaixar` ignora versão antiga e já incorporados; PRISMA).
- [ ] Step 2: rodar `node tests/screening.mjs` → FAIL (arquivo `lib/screening.ts` não existe).
- [ ] Step 3: implementar `lib/screening.ts` e os ajustes em `lib/ai-analysis.ts`.
- [ ] Step 4: `node tests/screening.mjs` → `screening: ok`; `node tests/ai-analysis.mjs` e `node tests/ai-runner.mjs` continuam passando.
- [ ] Step 5: checkpoint `pnpm exec tsc --noEmit`.

### Task 2: Tabela, rota e guardas no servidor

**Files:** Create `drizzle/0004_screening.sql`, `app/api/projects/[id]/screening/route.ts`. Modify `db/schema.ts`, `app/api/projects/[id]/route.ts` (GET devolve `screening`; `clearStage`; `restore`), `lib/incorporate-pdf.ts`, `app/api/projects/[id]/motor/route.ts`, `lib/backup.ts`, `tests/worker-integration.mjs`.

**Consumes:** Task 1.

- Tabela: `screening(project, doi, decision, answers, reasons, reason, actor, version, source, ai, updated, PRIMARY KEY(project,doi))`.
- Rota `POST /api/projects/:id/screening`:
  - `decide {doi,answers,reason,reasons}` (dono/editor/revisor) → grava a decisão;
  - `ai {limit}` (dono/editor) → sugere para até `limit` pendentes sem sugestão atual, em lotes de 5; devolve `{analysed,remaining,failures,provider,model}`;
  - `acceptAI {dois}` (dono/editor/revisor; 1–500) → aplica as sugestões atuais a registros sem decisão atual;
  - `refreshAbstract {doi}` (dono/editor) → `resolveArticle(doi,true)` e atualiza o `result` do lote.
- `incorporateWithPdf`, `incorporateFromMotor` e a rota do motor: 409 se o DOI não estiver incluído na versão atual; o artigo recebe `triage=triagemParaArtigo(linha)`.
- `clearStage` `search` e `triagem` apagam as linhas de `screening` do projeto.
- Backup: `orbis_web.screening`; `restore` recebe `screening` e grava.

- [ ] Step 1: acrescentar ao `tests/worker-integration.mjs`: incorporar sem triagem → 409; triar via rota (incluir) → incorporar sem PDF → 422 (como antes); decisão inválida → 400; `GET` do projeto traz `screening`; `clearStage search` apaga; restauração com `screening`.
- [ ] Step 2: `pnpm build && node tests/worker-integration.mjs` → FAIL.
- [ ] Step 3: implementar.
- [ ] Step 4: `pnpm build && node tests/worker-integration.mjs` → ok; `pnpm exec tsc --noEmit`; testes `.mjs`.
- [ ] Step 5: checkpoint.

### Task 3: Tela de triagem, "Baixar os incluídos" e PRISMA

**Files:** Create `app/screening-panel.tsx`. Modify `app/workspace.tsx`, `app/open-article-results.tsx`, `app/prisma-panel.tsx`.

- Etapa `screening` ("Triagem de títulos e resumos") depois do Artigo Aberto no menu.
- Painel: filtros com contagens, busca, paginação de 50, ficha com resumo e perguntas (Sim/Não/Indeterminado por pergunta, motivo por pergunta, justificativa geral), sugestão de IA com **Aceitar**, **Sugerir com IA** em lotes com progresso e **Pausar**, **Aceitar sugestões concordantes em bloco**, **Buscar resumo de novo**, **Baixar os incluídos** (usa o `collect` do workspace restrito aos incluídos).
- Artigo Aberto: sem os botões de incorporar; aviso levando à triagem.
- PRISMA: identificados, triados, excluídos na triagem, buscados, não obtidos.

- [ ] Step 1: `pnpm exec tsc --noEmit` e `node tests/*.mjs` verdes após a tela.
- [ ] Step 2: `pnpm build && node tests/worker-integration.mjs`.
- [ ] Step 3: checkpoint e commit da parte A (spec + plano + código).
