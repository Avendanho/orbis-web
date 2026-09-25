# Plano — motor de recuperação e triagem no ORBIS

Branch: `feat/motor-recuperacao-e-triagem`
Backup: `backup/antes-integracao-python-v21` (a partir de `76c4716`)
Base: ORBIS Web v21

## O que este plano faz

Traz para o ORBIS o que existe num pipeline Python de revisão sistemática já
em produção — mais fontes de acesso aberto, busca no PubMed, validação de
identidade do PDF e triagem por IA automatizada — **sem alterar a interface**.

O ORBIS não ganha telas novas onde já existe tela. Ganha resultado melhor nas
que já tem.

## Diagnóstico

### O que o ORBIS já faz bem, e será preservado

`lib/article-discovery.ts` lê `citation_pdf_url`, `<link rel=alternate>` e
JSON-LD das páginas de artigo, e ainda confere se o DOI da página bate com o
pedido (`mismatch`). É a mesma técnica do pipeline Python e está bem feita.

`lib/article-resolver.ts` consulta Crossref, Europe PMC, Semantic Scholar,
OpenAlex e DataCite, e já percorre `locations[]` do OpenAlex incluindo
`landing_page_url` — coisa que o pipeline Python só passou a fazer depois.

`lib/ai-analysis.ts` é a peça central: `makeAIPackage`, `importAI` e
`aiConsensus` já resolvem concordância, divergência, duplicata e contexto
desatualizado entre várias IAs. Nada disso precisa ser reescrito.

### O que está quebrado

`lib/article-resolver.ts` chama
`https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi`. **Esse serviço foi
desligado na migração do PMC de agosto/2026 e responde 404.** Verificado:

```
curl "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id=PMC10695679"
→ 404 — WWW Error 404 Diagnostic
```

Toda a rota PMC do ORBIS está morta hoje, em silêncio: a função captura a
falha e devolve lista vazia.

### O que falta

| Falta | Consequência |
|---|---|
| **Unpaywall** | É a fonte de maior rendimento medido (34% dos acertos num corpus de 643 artigos). O ORBIS não a consulta. |
| **Bucket do PMC na AWS** | Única via do PMC que ainda atende cliente HTTP. As páginas de artigo passaram a responder reCAPTCHA. |
| **Busca no PubMed** | Hoje o ORBIS só aceita uma lista de DOIs colada. Não há como partir de uma pergunta de pesquisa. |
| **Identidade do PDF** | `incorporateWithPdf` confere `%PDF-` e guarda. Não confere se é *o artigo pedido*. Numa auditoria de 449 PDFs do pipeline Python, 4 eram material suplementar, formulário editorial ou arquivo ilegível — todos com metadados corretos. |
| **IA automatizada** | `lib/ai-analysis.ts` tem zero chamadas `fetch`. A análise é colada à mão, artigo por artigo. |
| **Texto do PDF** | O PDF é armazenado e nunca lido. A triagem de texto completo trabalha só com título e resumo. |

## O princípio que torna isto seguro

Duas fronteiras já existem no ORBIS e vão absorver tudo que for acrescentado:

1. **`Article`** (`lib/doi-batch.ts`) — o que uma fonte devolve. Fonte nova
   não muda nada acima dela; só preenche esse tipo.
2. **`ORBIS_AI_RESULTS_V1`** (`lib/ai-analysis.ts`) — o que uma IA devolve. Se
   a IA automática produzir exatamente esse formato, ela entra por `importAI`
   como qualquer análise colada, e **toda a interface de consenso, divergência
   e adjudicação funciona sem uma linha de mudança**.

Por isso o visual fica intacto: nada aqui inventa fluxo novo.

## Arquitetura

Decisão tomada: **híbrido**, com o sistema rodando tanto no site público
quanto na máquina do pesquisador.

```
Cloudflare Worker  ── sempre disponível, sem dependência externa
 ├─ Unpaywall, bucket PMC/AWS, PubMed, OpenAlex, CORE   [TypeScript, novo]
 ├─ identidade por metadados                            [TypeScript, novo]
 └─ triagem por IA sobre título + resumo                [TypeScript, novo]
          │
          └── opcional ──► Serviço Python (FastAPI, local)
                            ├─ texto completo do PDF (PyMuPDF)
                            ├─ identidade lendo o conteúdo do PDF
                            ├─ as 28 fontes do pipeline
                            └─ triagem de texto completo
```

O Worker nunca depende do serviço. Quando o serviço responde, o ORBIS oferece
as capacidades extras; quando não, segue funcionando como hoje, melhor.

## Fases

### Fase 1 — Consertar e ampliar as fontes *(Worker, TypeScript)*

| # | Item | Arquivo |
|---|---|---|
| 1.1 | Trocar `oa.fcgi` (morto) pelo bucket `pmc-oa-opendata` na AWS | `lib/sources/pmc.ts` (novo), `lib/article-resolver.ts` |
| 1.2 | Acrescentar Unpaywall como fonte | `lib/sources/unpaywall.ts` (novo) |
| 1.3 | Resolver `DOI → PMID → PMCID` pelo conversor oficial do NCBI | `lib/sources/pmc.ts` |

Ganho esperado, pelos números medidos no corpus Python: a rota PMC volta a
funcionar e o Unpaywall entra como a fonte de maior rendimento.

### Fase 2 — Busca no PubMed *(Worker)*

| # | Item | Arquivo |
|---|---|---|
| 2.1 | Busca por termos via E-utilities (`esearch` + `esummary`) | `lib/sources/pubmed-search.ts` (novo) |
| 2.2 | Endpoint que recebe a expressão de busca e enfileira os DOIs encontrados | `app/api/projects/[id]/search/route.ts` (`action:'pubmed'`) |
| 2.3 | Campo de busca na etapa que hoje só aceita DOIs colados | `app/workspace.tsx` |

A interface ganha **um campo** na tela que já existe, usando os componentes já
presentes. Nenhuma tela nova, nenhuma mudança de identidade visual.

### Fase 3 — Identidade do PDF *(Worker)*

| # | Item | Arquivo |
|---|---|---|
| 3.1 | Conferir título, autor e ano do registro contra o que a página declara | `lib/identity.ts` (novo) |
| 3.2 | Gravar o método e o veredito junto do artigo incorporado | `lib/incorporate-pdf.ts` |
| 3.3 | Mostrar como cada PDF foi confirmado no cartão do corpus | `app/corpus-article-card.tsx` |

Respeita a regra 1 do projeto: nada entra no corpus sem PDF validado — agora
com validação mais forte do que "é um PDF".

### Fase 4 — Triagem por IA automatizada *(Worker)*

| # | Item | Arquivo |
|---|---|---|
| 4.1 | Cliente de LLM com repetição e espera para falha transitória | `lib/ai-provider.ts` (novo) |
| 4.2 | Gerar `ORBIS_AI_RESULTS_V1` a partir do pacote que `makeAIPackage` já monta | `lib/ai-runner.ts` (novo) |
| 4.3 | Botão "Analisar com IA" ao lado do "Importar análise" existente | `app/ai-controls.tsx` |

**A análise automática entra como rascunho, exatamente como a colada.** Regra
2 do projeto preservada: nenhuma proposta da IA é aplicada sem ação humana.

Falha de chamada **não vira parecer**: fica registrada como falha técnica e o
artigo continua pendente. É a distinção que evita uma queda de API entrar na
contagem PRISMA como triagem.

### Fase 5 — Serviço Python opcional *(fora do Worker)*

| # | Item | Arquivo |
|---|---|---|
| 5.1 | FastAPI expondo o pipeline atual | `servico-python/main.py` (novo) |
| 5.2 | Detecção do serviço; sem ele, o ORBIS não muda | `lib/python-bridge.ts` (novo) |
| 5.3 | Aviso na interface quando o motor completo está disponível | `app/project-settings.tsx` |

Endpoints previstos: `POST /resolver` (28 fontes), `POST /identidade`
(validação lendo o PDF), `POST /texto` (extração), `POST /triagem`
(texto completo).

## Verificação

O ambiente exige Node ≥ 22.13 (`package.json`). A máquina tem Node 18, então
uma cópia local do Node 22 foi usada só para rodar os testes — nada foi
instalado no sistema.

Os testes de lógica do ORBIS rodam **sem `node_modules`**: carregam o
TypeScript por `stripTypeScriptTypes`. Cada módulo novo terá teste no mesmo
formato, em `tests/`.

```bash
node tests/ai-analysis.mjs        # já passa
node tests/protocol-planner.mjs   # já passa
node tests/domain-backup.mjs      # já passa
node tests/assessment-report.mjs  # já passa
node tests/sources.mjs            # novo
node tests/identity.mjs           # novo
node tests/ai-runner.mjs          # novo
```

`tests/worker-integration.mjs` precisa do build (`dist/`) e portanto de
`pnpm install`; será rodado antes de qualquer publicação, conforme a regra 8.

## Regras do projeto, e como cada uma é respeitada

1. **Nada no corpus sem PDF validado** — a Fase 3 endurece a validação.
2. **Nada da IA aplicado sem ação humana** — a Fase 4 produz rascunho, nunca decisão.
3. **Decisão humana não é sobrescrita** — passa por `importAI`, que já garante isso.
4. **Análises de IAs separadas e rastreáveis** — cada execução grava provider e model.
5. **Motivos de falha preservados** — falha técnica é registrada como tal, distinta de parecer.
6. **Backup antes de mudança estrutural** — `backup/antes-integracao-python-v21` criado.
7. **Sem segredos no repositório** — chaves por variável de ambiente; `servico-python` sem credencial.
8. **TypeScript, build e integração antes de publicar** — ver Verificação.

## Fora de escopo, de propósito

- **Bibliotecas-sombra** (Sci-Hub, LibGen). Existem no pipeline Python, ficam
  de fora do ORBIS: rendimento medido de 2,9% e estatuto legal distinto.
  Numa ferramenta compartilhada com uma equipe, isso é decisão institucional,
  não técnica.
- **Trocar a autenticação do ChatGPT Sites.** O pacote de continuidade já
  aponta isso como item próprio.
- **Mexer em `components/ui`.** A identidade visual fica intacta.
