# Parte D — Configurações: plano

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Uma tela **Configurações** onde a pessoa ajusta bases de busca, IA (nuvem e local), ritmo do lote, extração e fontes de download, sem editar ambiente nem `motor/.env`.

**Base:** `docs/planos/configuracoes-plano.md` (Tasks 1–8, com o código). Este plano executa aquele, com os deltas da parte D de `docs/planos/evolucao-orbis-design.md` listados abaixo. Onde os dois divergem, vale este.

**Spec:** `docs/planos/evolucao-orbis-design.md` (parte D) sobre `docs/planos/configuracoes-design.md`.

## Deltas sobre `configuracoes-plano.md`

1. **Grupos**: `bases` (Bases de busca), `ia` (IA), `lote` (Triagem e lote), `extracao` (Extração — motor), `fontes` (Fontes de download — motor). Diagnóstico = botões **Testar** por grupo.
2. **Catálogo**:
   - `ia`: `ORBIS_IA_PROVEDOR` (`automatico`|`local`|`anthropic`|`openai`|`gemini`, padrão `automatico`), `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` (destino **orbis**), `ORBIS_IA_MODELO_*`, `OLLAMA_URL`, `OLLAMA_MODELO` (lista dos instalados na tela), `OLLAMA_CONTEXTO` (2048–131072, 16384), `OLLAMA_PRAZO` (30–3600, 600), `ORBIS_IA_LOTE` (1–50, 10).
   - `extracao`: `ORBIS_EXTRAIR_MARKDOWN`, `ORBIS_SALVAR_IMAGENS` (booleanos ligados por padrão: desligar grava `0`), `ORBIS_EXTRACAO_PRAZO` (5–600, 90).
   - `fontes`: `UNPAYWALL_EMAIL` (ambos, reinício), `OPENALEX_API_KEY`, `SEMANTIC_SCHOLAR_API_KEY` (ambos), `CORE_API_KEY` (reinício), `SPRINGER_API_KEY` (reinício), `WILEY_TDM_TOKEN`, `PAPER_FETCH_NO_{SCIHUB,LIBGEN,WAYBACK,OSTI,SCHOLAR,FATCAT,BASE,OPENALEX_CONTENT}`.
   - Fora (acesso institucional segue só pelo `motor/.env`): `EZPROXY_*`, `PAPER_FETCH_PROXY`, `PAPER_FETCH_INSTITUTIONAL`, `PAPER_FETCH_CLOAK`, `PAPER_FETCH_BROWSER`, `SCOPUS_API_KEY`, `IEEE_API_KEY`, `CROSSREF_MAILTO`, `OPENALEX_MAILTO`.
3. **Alvos de teste**: + `ollama` (modelo instalado? e uma chamada mínima).
4. **`pickProvider`**: já feito na parte C (registro plano); a Task 2 do plano base vira só usar `settingsValues()` no lugar de `lib/ai-env.ts` (apagado).
5. **Migração**: `drizzle/0005_settings.sql`.
6. **Token**: persistente em `$(ORBIS_DATA_DIR ou motor)/data/orbis-token`, criado uma vez pelo `start.py`, entregue ao motor e ao ORBIS como `ORBIS_ENGINE_TOKEN` (declarado nos `vars` do `vite.config.ts`); o motor também lê o arquivo quando sobe sem a variável.
7. **Carga do `.env` no motor**: `main.py` já carrega `motor/.env` (`carregar_env_do_motor`, com o filtro de valores do modelo); `config_env.py` só lê/grava e mantém esse carregador.
8. **GET `/api/settings`** traz também `ollama:{online,modelos}`.

## Global Constraints

As do plano base (tela → ambiente → padrão; segredo mascarado; recusa de `\r\n\0`; `/config` com `X-Orbis-Token`; `lib/settings.ts` sem imports), mais: commits direto na `main`, um no fim da parte, com todas as suítes verdes.

## Review Focus

Os cinco do plano base, mais:
6. Booleano ligado por padrão desligado na tela → grava `0` (não apaga), e a extração respeita. Teste: Task 1 (`validar`) e `test_config_env.py`.
7. Token persistente: segunda execução do `start.py` reaproveita o mesmo arquivo; motor iniciado à mão com o arquivo presente aceita o ORBIS. Teste: Task 3.

---

### Task 1: Catálogo e regras puras — plano base Task 1 + deltas 1–3, 6 (booleanos).
### Task 2: Provedor lendo Configurações — delta 4.
### Task 3: Motor lê e grava `motor/.env`, com token — plano base Task 3 + deltas 2, 6, 7.
### Task 4: Persistência e ponte com o motor — plano base Task 4 + delta 5.
### Task 5: Rota `/api/settings` e testes de credencial — plano base Task 5 + deltas 3, 8.
### Task 6: Quem passa a ler as configurações — plano base Task 6 + triagem de títulos e resumos (`ai`, `refreshAbstract`).
### Task 7: Tela de Configurações — plano base Task 7 + grupos novos, lista de modelos do Ollama, booleanos com padrão.
### Task 8: Documentação curta em `COMO_RODAR.md` e suíte completa (a documentação completa é a parte E).
