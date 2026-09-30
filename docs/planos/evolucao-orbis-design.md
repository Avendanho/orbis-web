# Evolução do ORBIS: triagem antes do download, Markdown, IA local, Configurações — design

Data: 2026-09-30 · Status: aguardando revisão

## Objetivo

Levar o ORBIS à ordem de trabalho de uma revisão sistemática (identificar →
triar título e resumo → buscar o texto completo só do que passou → avaliar),
com o texto completo em Markdown para a análise, IA local além da de nuvem, uma
tela de Configurações, documentação completa, e o projeto publicado e
empacotado para compartilhar.

**Sucesso:** um pesquisador cola ou busca DOIs, tria por título e resumo (com
sugestões de um Qwen local), clica **Baixar os incluídos**, recebe o texto de
cada artigo em Markdown (imagens na pasta local), roda a análise PCC com a IA
local, ajusta tudo pela tela de Configurações, e qualquer pessoa entende o
sistema lendo `docs/SISTEMA.md`.

## Decisões já tomadas

- **Triagem formal antes do PDF (A1):** os registros vivem numa tabela própria,
  fora do estado do projeto (limite de 1,8 MB por estado).
- **Markdown no ORBIS, imagens na pasta local;** a IA analisa só o texto.
- **Modelo local padrão:** `qwen3:14b` pelo Ollama já instalado (GPU de 16 GB).
- **Configurações** partem de `docs/planos/configuracoes-design.md`, com as
  opções novas. A busca e a sessão pelo portal CAPES **não** entram; acesso
  institucional (EZproxy, proxy, sessão) segue só pelo `motor/.env`.
- **Publicação:** commits direto na `main` e `git push`; zip por `git archive`.
- `docs/planos/busca-multibase-design.md` permanece como registro; não é
  implementado.

## Ordem de implementação

A → B → C → D → E → F. D vem depois de A–C para nascer com as opções delas;
até lá, as opções novas têm padrão no código e aceitam variável de ambiente.

---

## A. Triagem de títulos e resumos antes do download

### Comportamento

1. **Artigo Aberto** continua buscando ou recebendo DOIs e consultando cada
   um (título, autores, ano, periódico, resumo). Os botões de incorporar/baixar
   saem desta etapa.
2. **Triagem de títulos e resumos** (etapa nova, depois do Artigo Aberto):
   - lista paginada (50 por página) dos registros — DOIs do lote cuja consulta
     achou metadados —, com filtros *pendentes / incluídos / excluídos / sem
     resumo* e busca por texto;
   - ficha: título, autores, ano, periódico, fonte e texto do resumo, as
     perguntas positivas do protocolo com Sim / Não / Indeterminado e
     justificativa — mesma regra de `triageDecision` e das regras por pergunta;
   - sem resumo: aviso e **Buscar resumo de novo** (`resolveArticle(doi,true)`);
   - exige protocolo aprovado, como a triagem de hoje.
3. **Sugerir com IA:** roda o provedor configurado (C) sobre os pendentes em
   lotes controlados pela tela (progresso e pausa). A sugestão fica na ficha
   (resposta e evidência por pergunta) e pode ser aceita uma a uma ou em
   bloco. Nunca vira decisão sozinha.
4. **Baixar os incluídos:** para cada incluído na versão atual do protocolo que
   ainda não está no corpus, baixa pelo motor (se no ar, no modo de download do
   projeto — "baixar" ou "analisar") ou pelo ORBIS e incorpora. O artigo entra
   no corpus **com a decisão de triagem copiada**. Os que não baixarem ficam
   como *incluídos, PDF não obtido*, com o motivo.
5. **Corpus, Análise PCC, Revisão geral** seguem como hoje.
6. **PDFs importados do computador** (sem DOI) não passam por esta etapa:
   continuam sendo triados no corpus, como hoje.

### Dados

- Migração `drizzle/0004_screening.sql` e `db/schema.ts`:

  ```sql
  CREATE TABLE screening(
    project TEXT NOT NULL REFERENCES projects(id), doi TEXT NOT NULL,
    decision TEXT, answers TEXT, reason TEXT, actor TEXT, version INTEGER,
    ai TEXT, updated TEXT NOT NULL, PRIMARY KEY(project, doi));
  ```

  `answers` e `ai` são JSON. `ai` guarda a última sugestão por provedor/modelo,
  com a versão do protocolo e o hash dos critérios (mesma noção de "atual" de
  `lib/ai-analysis.ts`).
- Registro = linha de `search_items` com `status IN ('done','partial')` e
  `result.found`; título e resumo vêm de `result`.
- Decisão com `version` diferente da do protocolo conta como pendente e é
  mostrada como "decisão de versão anterior".

### Componentes

- `lib/screening.ts` (sem imports de rede): regras puras — listar/filtrar/
  paginar registros a partir das linhas, contar, validar decisão, montar o
  pacote de IA a partir de registros, aplicar sugestões, copiar a decisão para
  o artigo.
- `app/api/projects/[id]/screening/route.ts`:
  - `GET ?page&filter&q` → `{items, total, counts}`;
  - `POST {action:'decide', doi, answers, reason}`;
  - `POST {action:'ai', limit}` → analisa até `limit` pendentes sem sugestão
    atual e devolve `{analysed, remaining, failures}`;
  - `POST {action:'acceptAI', dois}`;
  - `POST {action:'refreshAbstract', doi}`.
- `lib/incorporate-pdf.ts`: exige decisão `incluir` na versão atual para DOI
  com registro; copia a decisão para `article.triage`.
- `app/screening-panel.tsx` (tela) e `app/workspace.tsx` (etapa nova no menu;
  Artigo Aberto sem os botões de incorporar; botão **Baixar os incluídos**).
- `lib/domain.ts` `metrics` e `app/prisma-panel.tsx`: identificados, triados,
  excluídos na triagem, buscados para obtenção, não obtidos, avaliados (PCC).
- Backup (`lib/backup.ts`, rota `restore`): leva e restaura as linhas de
  `screening`.
- `clearStage`: limpar o Artigo Aberto apaga também as linhas de `screening`
  desses DOIs; limpar a triagem apaga as decisões.

### Erros

- Protocolo não aprovado → 400 "Aprove o protocolo antes da triagem".
- Decisão com respostas incompletas ou exclusão sem justificativa → 400 (regra
  de hoje).
- Falha de IA num lote → registros continuam pendentes; a tela mostra o motivo.
- Download que falha → registro fica *incluído, PDF não obtido*, com o motivo;
  **Baixar os incluídos** pode ser repetido.

### Testes

- `tests/screening.mjs`: filtro/paginação/contagem, decisão válida e inválida,
  versão antiga conta como pendente, pacote de IA a partir de registros,
  aplicar sugestões, cópia para o artigo.
- `tests/worker-integration.mjs` (existente) ganha: triar, baixar incluídos
  (com fonte simulada), PRISMA e backup/restauração com `screening`.

---

## B. Extração para Markdown com imagens

### Comportamento

- Ao baixar pelo motor, o texto é extraído em **Markdown** com `pymupdf4llm`
  (seções, títulos e tabelas preservados) e substitui o texto simples que o
  ORBIS guarda hoje. A IA (triagem de corpus e PCC) passa a ler o Markdown.
- **Modo "baixar":** imagens em `<pasta_pdfs>/<projeto>/<nome>_imagens/` e o
  `<nome>.md` ao lado do PDF, com links relativos para as imagens.
- **Modo "analisar":** nada fica no disco; só o Markdown vai ao ORBIS.
- A extração tem prazo; se falhar ou estourar, cai para o texto simples de
  hoje. Limite de 2 MB por texto mantido (corte com aviso).
- A ficha do artigo mostra o formato (Markdown ou texto), abre/baixa o `.md` e
  informa a pasta das imagens.
- Sem motor, o download pelo ORBIS guarda só o PDF (sem extração), como hoje.

### Componentes

- `servico-python/extracao.py`: `extrair(caminho_pdf, pasta_imagens | None,
  prazo) -> {texto, formato, paginas, imagens}` — `pymupdf4llm` num processo
  filho com prazo; recurso ao texto simples.
- `servico-python/baixar.py`: usa `extracao.extrair`; no modo "baixar" grava
  `.md` e imagens junto do PDF; resposta ganha `formato`, `imagens`,
  `pasta_imagens`.
- Opções (padrão / variável): extrair Markdown (`ORBIS_EXTRAIR_MARKDOWN=1`),
  salvar imagens (`ORBIS_SALVAR_IMAGENS=1`), prazo (`ORBIS_EXTRACAO_PRAZO=90`).
- ORBIS: `lib/motor-download.ts` guarda o `formato` em `article.texto`; a rota
  `/texto` devolve `text/markdown` quando for o caso; a ficha do artigo
  (`app/corpus-article-card.tsx`) mostra o Markdown.

### Testes

- `servico-python/tests/test_extracao.py`: PDF real pequeno gerado no teste →
  Markdown com o título; imagens gravadas na pasta; recurso ao texto simples
  quando `pymupdf4llm` falha; prazo estourado.
- `servico-python/tests/test_baixar.py`: modo "baixar" grava `.md` e imagens;
  modo "analisar" não deixa nada no disco.
- `tests/motor-download.mjs`: `formato` guardado no artigo.

---

## C. IA local (Ollama / Qwen)

### Comportamento

- Provedor **Local (Ollama)** ao lado de Anthropic, OpenAI e Gemini, pela API
  do Ollama (`/api/chat`, `stream:false`, `format:'json'`, `think:false`,
  `temperature:0`, `num_ctx` configurável). Padrão `qwen3:14b`.
- **Preferência de provedor:** *automático* (local se o Ollama responde; se
  não, a primeira chave de nuvem), *local*, *Anthropic*, *OpenAI*, *Gemini*.
- **Lotes controlados pela tela:** a execução de IA passa a processar até
  `limit` itens por requisição e devolver quantos faltam; a tela repete com
  progresso e **Pausar**. Vale para a triagem nova (A), a triagem do corpus e a
  análise PCC.
- Mesmas regras de hoje: resultado entra como rascunho, falha técnica fica
  pendente e não vira parecer.

### Componentes

- `lib/ai-provider.ts`: `ollamaProvider(opts)`; `pickProvider(env, prefs)` com a
  preferência; `listLocalModels(url)` (GET `/api/tags`).
- `lib/ai-runner.ts`: `runAITriage(..., {limit})` devolve também `remaining`.
- `app/api/projects/[id]/route.ts` (`runAI`) aceita `limit`; `app/ai-controls.tsx`
  faz o laço com progresso e pausa.
- Opções (padrão / variável): `ORBIS_IA_PROVEDOR=automatico`,
  `OLLAMA_URL=http://localhost:11434`, `OLLAMA_MODELO=qwen3:14b`,
  `OLLAMA_CONTEXTO=16384`, `OLLAMA_PRAZO=600` (segundos).

### Testes

- `tests/ai-provider.mjs` (novo) / `tests/ai-runner.mjs` (existente): corpo da
  chamada ao Ollama (`think:false`, `format`, `num_ctx`), erro do Ollama vira
  `LlmCallFailed`, escolha *automático* com e sem Ollama, `limit` e
  `remaining` no runner.
- Verificação real: uma triagem de 5 artigos com `qwen3:14b` nesta máquina.

---

## D. Configurações

Base: `docs/planos/configuracoes-design.md` (catálogo único, tabela `settings`
no D1, itens do motor gravados no `motor/.env` pelo próprio motor, resolução
*tela → ambiente → padrão*, segredo mascarado, recusa de quebra de linha).

### O que a tela configura

| Grupo | Itens | Onde vale |
|---|---|---|
| Bases de busca | NCBI chave e e-mail; Embase/Elsevier chave e token; base padrão; limite de resultados (1–500) | ORBIS |
| IA | provedor preferido; chaves Anthropic/OpenAI/Gemini; modelo de cada provedor; Ollama: endereço, modelo (lista dos instalados), contexto, prazo; tamanho do lote | ORBIS |
| Triagem e lote | consultas simultâneas (1–4); modo de download padrão para projetos novos | ORBIS |
| Extração | extrair Markdown; salvar imagens; prazo da extração | motor |
| Fontes de download | e-mail Unpaywall; chaves OpenAlex, Semantic Scholar, CORE, Springer, Wiley TDM, Elsevier; opções liga/desliga `PAPER_FETCH_NO_*` | motor |
| Diagnóstico | **Testar** por credencial e pelo Ollama (chamada real mínima) | — |

### Componentes

- `lib/settings.ts` (catálogo e regras puras, sem imports),
  `drizzle/0005_settings.sql`, `app/api/settings/route.ts` (GET/PUT/POST
  testar), `app/settings-panel.tsx`, item **Configurações** em `app/workspace.tsx`.
- Motor: `servico-python/config_env.py` (lista permitida, leitura e gravação do
  `.env` preservando comentários) e rotas `GET/PUT /config` com o cabeçalho
  `X-Orbis-Token`. O token fica em `$(ORBIS_DATA_DIR ou motor)/data/orbis-token`
  (criado uma vez), e o `start.py` o entrega ao ORBIS como `ORBIS_ENGINE_TOKEN`
  (declarado nos `vars` do `vite.config.ts`).
- Quem passa a ler as configurações: busca (NCBI/Embase, base padrão, limite),
  `pickProvider` (C), `workspace.tsx` (simultâneas), criação de projeto (modo
  de download), motor (B e fontes, pelo `.env`).

### Testes

`tests/settings.mjs` (validação, resolução, máscara, testes de credencial),
`servico-python/tests/test_config_env.py` (reescrita do `.env`, token),
paridade entre o catálogo do ORBIS e a lista permitida do motor.

---

## E. Documentação

- `docs/SISTEMA.md` — para qualquer leitor: o que o ORBIS é; o fluxo natural
  etapa por etapa (planejamento PCC, busca, triagem de títulos e resumos,
  download e extração, corpus, análise PCC, revisão geral, PRISMA, backup); o
  papel da IA (nuvem e local) e o que ela nunca faz; onde ficam os dados;
  diagrama Mermaid do fluxo.
- `docs/ARQUITETURA.md` — para quem mexe no código: componentes (interface/
  Worker, `servico-python`, motor), tabelas e armazenamento (D1, R2, pastas
  locais), cascata de download e validação de identidade, extração, provedores
  de IA, Configurações e token, segurança, testes, como estender; diagrama
  Mermaid de componentes.
- Atualizar `README.md`, `COMO_RODAR.md` (Configurações, IA local) e
  `IMPLEMENTATION.md` (tirar o que deixou de valer e apontar para os novos).
- Escrita depois de A–D, descrevendo o sistema como ficou.

---

## F. Publicação e pacote

- Commits direto na `main`, cada um só depois de todas as suítes passarem: um
  com o trabalho pendente da etapa anterior (motor e análise) e um por parte
  (A–E). Depois `git push origin main`.
- Zip: `git archive --format=zip --prefix=orbis-web/ -o "~/Área de
  trabalho/orbis-web.zip" HEAD` — só o que está no repositório (sem `.env`,
  chaves, `.venv`, `node_modules`, PDFs e listas locais).
- Verificação: descompactar numa pasta temporária, conferir que não há `.env`
  nem `orbis-token`, e rodar as suítes do motor, do serviço e da interface
  contra essa cópia.

---

## Fora do escopo

- Busca e sessão pelo portal CAPES na interface.
- Análise das imagens pela IA (modelos com visão).
- Extração de Markdown sem o motor.
- Configurações por usuário (a instalação é pessoal).
