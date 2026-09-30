# Parte C — IA local (Ollama / Qwen): plano

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A triagem por IA (títulos e resumos, corpus e PCC) roda também num modelo local pelo Ollama (`qwen3:14b` por padrão), com preferência de provedor, e toda execução de IA passa a andar em lotes controlados pela tela, com progresso e **Pausar**.

**Architecture:** `lib/ai-provider.ts` ganha `ollamaProvider`, `listLocalModels` e um `pickProvider` assíncrono que lê um registro plano de valores (as mesmas chaves que a tela de Configurações, parte D, vai gravar). `lib/ai-runner.ts` aceita `limit` e devolve `remaining`, escolhendo só os artigos sem análise atual daquele provedor/modelo. As rotas leem os valores por `lib/ai-env.ts` (ambiente do Worker) — a parte D troca essa fonte por `settingsValues()`.

**Tech Stack:** TypeScript (Worker/vinext), Ollama `/api/chat` e `/api/tags`, `node tests/*.mjs`, Miniflare.

**Spec:** `docs/planos/evolucao-orbis-design.md` (parte C)

## Global Constraints

- Chaves (padrão): `ORBIS_IA_PROVEDOR=automatico` (`automatico`|`local`|`anthropic`|`openai`|`gemini`; `auto` = `automatico`), `OLLAMA_URL=http://localhost:11434`, `OLLAMA_MODELO=qwen3:14b`, `OLLAMA_CONTEXTO=16384` (2048–131072), `OLLAMA_PRAZO=600` s (30–3600), `ORBIS_IA_LOTE=10` (1–50), `ORBIS_IA_MODELO_ANTHROPIC|OPENAI|GEMINI` (padrões de hoje).
- *Automático*: local se o Ollama responde (`/api/tags` em até 2 s); se não, a primeira chave de nuvem na ordem de hoje. Provedor explícito sem chave → nenhum (não cai em outro às escondidas). *Local* não depende do teste de conexão.
- Chamada ao Ollama: `stream:false`, `format:'json'`, `think:false`, `options:{temperature:0,num_ctx}`; prazo pelo `OLLAMA_PRAZO`. Prazo estourado não é repetido (seria o mesmo prazo de novo).
- O texto completo mandado a um modelo local cabe no contexto: `limiteTexto=(num_ctx−6000)×3` caracteres (mín. 8000, máx. `TEXT_LIMIT`); PCC local vai 1 artigo por chamada.
- Mesmas regras de hoje: resultado é rascunho, falha técnica fica pendente.

## Review Focus

1. Ollama fora do ar com *automático* e sem chave de nuvem → "nenhum provedor" (400), não erro 500. Teste: Task 1.
2. Lote com `limit` não reanalisa quem já tem análise atual do mesmo provedor/modelo, mas reanalisa quem só tem de outro. Teste: Task 2 (runner).
3. Laço da tela termina: sem pendentes, `remaining:0`; lote em que tudo falha, `analysed:0` → para. Teste: Task 2 (integração).
4. Texto longo na PCC local não estoura `num_ctx`. Teste: Task 2 (runner, `limiteTexto`).

---

### Task 1: Provedor Ollama e escolha do provedor

**Files:** Modify `lib/ai-provider.ts`, `tests/ai-runner.mjs` (asserts de `pickProvider` passam a `await`). Create `tests/ai-provider.mjs`.

**Produces:** `OLLAMA_PADRAO`, `MODELOS_PADRAO`, `type Provider` (+`local?`, `limiteTexto?`), `ollamaProvider({url,modelo,contexto,prazo},fetchImpl?)`, `listLocalModels(url,fetchImpl?)->Promise<string[]|null>`, `ollamaOnline(url,fetchImpl?)`, `pickProvider(v,{online?})->Promise<Provider|null>`, `configOllama(v)`.

- [ ] Step 1: `tests/ai-provider.mjs` — corpo da chamada (`think:false`, `format`, `num_ctx`, `temperature`, mensagens); resposta lida de `message.content`; HTTP 404 do Ollama e `{error}` viram erro e `LlmCallFailed` sem repetir; prazo estourado com mensagem própria e sem repetir; `limiteTexto`; `listLocalModels` (lista e `null` fora do ar); `pickProvider` automático com/sem Ollama, local, explícito sem chave, modelos configurados, `auto`.
- [ ] Step 2: `node tests/ai-provider.mjs` → FAIL.
- [ ] Step 3: implementar; `tests/ai-runner.mjs` com `await`.
- [ ] Step 4: `node tests/ai-provider.mjs && node tests/ai-runner.mjs` → ok.

### Task 2: Lotes controlados pela tela

**Files:** Modify `lib/ai-runner.ts`, `app/api/projects/[id]/route.ts` (`runAI`), `app/api/projects/[id]/screening/route.ts` (`ai`), `app/ai-controls.tsx`, `app/screening-panel.tsx`, `app/workspace.tsx` (mensagem do `runAI`), `tests/ai-runner.mjs`. Create `lib/ai-env.ts`, `tests/ai-integration.mjs`.

**Consumes:** Task 1.

- `runAITriage(...,{limit})`: com `limit`, só pendentes (sem análise atual de `provider.name`+`provider.model`), no máximo `limit`; devolve `remaining`. Sem `limit`, como hoje (`remaining:0`).
- `runAI` e `ai` da triagem: `limit` do corpo ou `ORBIS_IA_LOTE`; sem pendentes → 200 com `analysed:0, remaining:0`; resposta traz `remaining`.
- `ai-controls.tsx`: **Analisar com IA** repete até `remaining` zerar, com progresso e **Pausar após o lote atual**.

- [ ] Step 1: testes do runner (`limit`/`remaining`, análise de outro provedor não conta, `limiteTexto` e PCC local em lotes de 1) e `tests/ai-integration.mjs` (Worker real com Ollama simulado: triagem do corpus em dois lotes até `remaining:0`, nada pendente → 200; triagem de títulos e resumos pelo Ollama; Ollama fora e sem chave → 400).
- [ ] Step 2: rodar → FAIL.
- [ ] Step 3: implementar.
- [ ] Step 4: `pnpm exec tsc --noEmit`, testes `.mjs`, `pnpm build && node tests/ai-integration.mjs` → verdes.
- [ ] Step 5: verificação real: triagem de 5 registros com `qwen3:14b` nesta máquina (`scripts`/teste manual pelo runner); commit da parte C.
