# Busca em todas as bases e Configurações — design

Data: 2026-09-29 · Status: aguardando revisão

## Objetivo

Na etapa de busca (Artigo Aberto), o pesquisador digita uma expressão, escolhe
**Todas as bases** e os DOIs de todas as bases disponíveis entram no lote, sem
repetição. As chaves de cada base e o login CAPES dos downloads ficam numa tela
**Configurações**: o pesquisador só preenche, e a próxima busca já usa.

**Sucesso:** com o ORBIS subido pelo `start.py`, a pessoa cola uma chave em
Configurações, clica **Testar**, volta ao projeto, busca em **Todas as bases** e
vê os DOIs no lote com o resumo por base — sem abrir arquivo nenhum e sem
reiniciar nada.

## Decisões já tomadas

- **Não se automatiza o buscador do Portal de Periódicos CAPES.** Ele barra
  acesso por robô; contorná-lo é evasão de controle de acesso. O portal é uma
  porta para bases que têm API oficial, e é por elas que a busca é feita. O
  acesso CAPES entra nos **downloads**, pela sessão do próprio pesquisador
  (`motor/src/download/sessao_navegador.py`, já existente).
- **Abordagem A — busca pelo motor.** O motor já tem 11 conectores com a mesma
  interface (`motor/src/search/bases.py`). Com o motor no ar, toda busca passa
  por ele; sem o motor, as 4 bases do ORBIS (PubMed, LILACS, Cochrane, Embase)
  seguem funcionando como hoje.
- **Escopo reduzido de Configurações:** só os cartões **Bases de busca** e
  **Acesso CAPES**. IA de triagem, lote e o restante de
  `docs/planos/configuracoes-design.md` ficam para depois.
- **Web of Science fica de fora:** a API exige assinatura institucional da
  própria API, não só da base.

## O que muda para quem usa

### Etapa Artigo Aberto

- O seletor de bases passa a oferecer **Todas as bases**, as 4 atuais e as do
  motor: Europe PMC, OpenAlex, Semantic Scholar, Scopus, IEEE Xplore, CORE,
  Oasisbr e BASE.
- **Todas as bases** = todas menos a Cochrane: as revisões CDSR já vêm dentro
  do PubMed, e consultá-la de novo só gastaria cota.
- Base que exige chave e não tem aparece como *"Scopus — configure a chave"*,
  com atalho para Configurações. Continua selecionável: a busca a relata como
  pulada.
- Campo **Limite por base** (1–2000, padrão 500).
- Com **Todas as bases**, uma dica orienta a escrever a expressão em booleano
  simples (`"machine learning" AND diagnosis`); os conectores adaptam a sintaxe
  de cada base (etiquetas do PubMed são removidas para as outras).
- O aviso de resultado lista cada base: quantos DOIs trouxe, se foi pulada ou
  falhou e por quê, e quantos DOIs repetidos foram descartados.

### Configurações (item novo na barra lateral, grupo Área de trabalho)

**Cartão Bases de busca.** Um campo por chave, com ajuda e link de onde obter:

| Chave | Serve para | Obrigatória para |
|---|---|---|
| `NCBI_API_KEY`, `NCBI_EMAIL` | PubMed, Cochrane (mais consultas por segundo) | — |
| `SCOPUS_API_KEY` (aceita `ELSEVIER_API_KEY`) | Scopus | Scopus |
| `ELSEVIER_INST_TOKEN` | acesso institucional à Scopus fora da rede do campus | — |
| `EMBASE_API_KEY`, `EMBASE_INST_TOKEN` | Embase | Embase |
| `IEEE_API_KEY` | IEEE Xplore | IEEE Xplore |
| `CORE_API_KEY` | CORE | CORE |
| `OPENALEX_API_KEY` | OpenAlex sem a cota anônima (~1000 consultas/dia) | — |
| `SEMANTIC_SCHOLAR_API_KEY` | Semantic Scholar sem o pool anônimo | — |

Cada base mostra o selo *ativa*, *ativa — cota anônima limitada* ou
*sem chave*, e um botão **Testar**.

**Cartão Acesso CAPES** (downloads, não busca):

- Situação: *sem login*, *login feito em DD/MM*, *sessão expirada — faça o
  login de novo*, *janela de login aberta* ou *navegador indisponível: motivo*.
- **Fazer login CAPES:** abre no computador do pesquisador uma janela do
  Chromium no Portal de Periódicos. A tela orienta: entrar, abrir um artigo de
  assinatura e fechar a janela. Ao fechar, o motor liga a sessão nos downloads
  sozinho (`ORBIS_SESSAO_NAVEGADOR=1`) e a retoma sem reiniciar.
- Campo **EZproxy da CAPES** (`ORBIS_SESSAO_EZPROXY`, opcional), com a
  explicação de como achar o `ezNN` na barra de endereço.
- Chave **Usar a sessão nos downloads** (`ORBIS_SESSAO_NAVEGADOR`).
- **Testar com um DOI** (padrão `10.1007/s10719-009-9256-7`).
- Limites exibidos, não editáveis na tela: 6 s entre artigos, 150 por execução.
- Desabilitado, com explicação, quando o ORBIS aponta para um motor que não
  está em `localhost`/`127.0.0.1`: a janela abriria em outra máquina.

Chave salva nunca volta inteira ao navegador: `••••` + 4 últimos caracteres
(só `••••` se tiver menos de 12), com **Trocar** e **Remover**. Com o motor fora
do ar, os dois cartões aparecem desabilitados com *"o motor não está no ar —
inicie pelo start.py"*.

## Arquitetura

```
Navegador ──▶ ORBIS (Worker) ──python-bridge (servidor)──▶ motor (servico-python)
  busca        /api/projects/:id/search ─────▶ POST /buscar ─▶ motor/src/search/bases.py
  Configurações /api/settings ───────────────▶ /config, /sessao/* ─▶ motor/.env, sessao_navegador.py
```

O navegador nunca fala com o motor: toda chamada passa pelo servidor do ORBIS,
que é quem tem o token.

### Motor — `servico-python/`

**`busca.py`** — roda as bases pedidas em paralelo (uma thread por base) com
prazo total. Para cada base devolve
`{base, rotulo, situacao, total, dois, sem_doi, motivo, segundos}`, com
`situacao` em `ok | pulada | erro | tempo_esgotado`. Base sem a chave
obrigatória é `pulada` sem chamar o conector. O limite vale por busca: cada
thread fixa o seu antes de chamar o conector (ver `query_utils` abaixo). Uma
base que estoura o prazo é relatada como `tempo_esgotado`; a thread termina em
segundo plano e o resultado dela é descartado.

**`config_env.py`** — fonte única dos itens configuráveis:

- `ITENS`: `key, grupo ('bases'|'capes'), rotulo, ajuda, link, tipo
  ('secret'|'email'|'url'|'boolean')`.
- `REQUISITOS`: base → chaves obrigatórias (`SCOPUS`: `SCOPUS_API_KEY` ou
  `ELSEVIER_API_KEY`; `EMBASE`: as duas; `IEEE`, `CORE`: a sua) e chaves que só
  melhoram a cota (`OPENALEX`, `SEMANTICSCHOLAR`).
- `ler()`: cada item com `preenchido`, `valor_mascarado` e `origem`
  (`arquivo`, `terminal`, `modelo` — igual ao `.env.example` —, `vazio`), mais
  `terminal_sobrepoe` quando o terminal também define a chave.
- `gravar(mudancas)`: valida **tudo antes de gravar qualquer coisa**; `null`
  remove. Reescreve `motor/.env` preservando comentários e ordem: substitui
  `KEY=`, descomenta `# KEY=` ou acrescenta no fim. Atualiza `os.environ` na
  hora. Recusa chave fora de `ITENS` e valor com `\r`, `\n` ou `\0`. Grava em
  arquivo temporário e troca no fim.
- `token()`: lê `$(ORBIS_DATA_DIR ou motor)/data/orbis-token`, criando-o na
  primeira vez (`secrets.token_urlsafe(32)`, permissão 600).

**`sessao.py`** — login e situação da sessão:

- `iniciar_login()`: fecha o navegador de download do próprio serviço
  (`sessao_navegador.encerrar()`, libera o perfil) e abre
  `sys.executable motor/src/download/sessao_navegador.py login` como
  subprocesso. Uma janela por vez: com uma aberta, responde 409. Uma thread
  espera o processo terminar; se os cookies do perfil foram gravados depois da
  abertura da janela (houve login), grava `ORBIS_SESSAO_NAVEGADOR=1` por
  `config_env.gravar`. Sem cookies novos, a situação diz que a janela foi
  fechada sem login.
- `situacao()`: `sessao_navegador.situacao()` + `janela_aberta`,
  `login_em` (data dos cookies do perfil) e o erro do último login, se houve.
- `testar(doi)`: `sessao_navegador.baixar_pdf` com prazo de 60 s.

**Rotas novas em `main.py`**

| Rota | Token | Entrada → saída |
|---|---|---|
| `POST /buscar` | não | `{expressao, bases[], limite=500 (1–2000), prazo=120 (10–300)}` → `{expressao, bases:[…]}` |
| `GET /config` | sim | → `{itens:[…], bases:[{base, rotulo, precisa, situacao}]}` |
| `PUT /config` | sim | `{mudancas:{KEY: valor\|null}}` → `{salvos:[…], avisos:[…]}` |
| `POST /config/testar` | sim | `{base}` → `{ok, detalhe}` (busca real com limite 1, prazo 20 s) |
| `GET /sessao` | sim | → situação da sessão |
| `POST /sessao/login` | sim | → `{aberta:true}` ou 409 |
| `POST /sessao/testar` | sim | `{doi}` → `{ok, detalhe}` |

`/buscar` não exige token pelo mesmo motivo que `/baixar` não exige: só lê. As
rotas que gravam o `.env` ou abrem janela exigem, porque o serviço aceita CORS
de qualquer origem — sem o token, qualquer página aberta no navegador poderia
gravar, por exemplo, um proxy no `.env`. O token vai no cabeçalho
`X-Orbis-Token` e é comparado com `hmac.compare_digest`.

### Motor — `motor/src/search/query_utils.py`

`max_results()` passa a respeitar um limite local da thread
(`limite_da_busca(n)`, context manager), antes da variável
`SEARCH_MAX_RESULTS`. Sem isso, duas buscas simultâneas com limites diferentes
se misturariam. Conectores não mudam.

### `start.py`

Lê (criando, se preciso) o mesmo `data/orbis-token` e o passa ao ORBIS como
`ORBIS_ENGINE_TOKEN`. Como o arquivo é estável, um motor que ficou no ar de
uma execução anterior continua aceitando o token.

### ORBIS

- **`lib/python-bridge.ts`**: `searchViaEngine`, `engineConfig`,
  `saveEngineConfig`, `testEngineBase`, `engineSession`, `startEngineLogin`,
  `testEngineSession`; as que exigem token levam `X-Orbis-Token` de
  `ORBIS_ENGINE_TOKEN`. `isLocalEngine(env)` diz se o motor está em
  `localhost`/`127.0.0.1`/`::1`.
- **`lib/sources/bases.ts`**: `BASES` ganha as bases do motor e `todas`, cada
  uma com `motor` (chave do motor) e `local` (se o ORBIS a atende sem motor).
  Continua sem imports de rede.
- **`lib/multi-search.ts`** (sem imports): `juntar(bases)` → `{dois, porBase,
  repetidos}` com DOIs normalizados como `doisFrom` já faz; `resumo(...)` → o
  texto do aviso.
- **`app/api/projects/[id]/search/route.ts`**: `action:'database'` aceita
  `base:'todas'` e as bases do motor, e `limit`. Motor no ar → `/buscar`
  (Cochrane: `cochraneTerm` aplicado no ORBIS e enviado como `PUBMED`). Motor
  fora → bases locais; `todas` vira PubMed, LILACS e Embase, com aviso das que
  faltaram.
- **`app/api/settings/route.ts`**: `GET` → `{motor:{online, local}, config,
  sessao}`; `PUT {mudancas}`; `POST {acao:'testar'|'login'|'testarSessao',
  base?, doi?}`. Exige login (`identity`), como as outras rotas; qualquer
  conta logada nesta instalação pode alterar — a instalação é pessoal, como em
  `configuracoes-design.md`.
- **`app/settings-panel.tsx`**: os dois cartões, desenhados a partir de
  `config.itens`; atualiza a situação da sessão a cada 3 s enquanto a janela de
  login estiver aberta.
- **`app/workspace.tsx`**: item **Configurações** no grupo Área de trabalho
  (abre sem projeto); na etapa de busca, as opções novas, o limite, os selos de
  chave e a dica de sintaxe.

## Erros

- **Uma base que falha não derruba a busca.** A busca só falha inteira (502)
  se nenhuma base respondeu, com o motivo de cada uma. Sem DOI nenhum: 404 com
  o resumo por base.
- **Base sem chave** é `pulada`, não erro.
- **Prazo total estourado:** o que chegou entra no lote; o resto é
  `tempo_esgotado`.
- **Configurações:** validação → 400 com o nome do campo, nada gravado; motor
  fora → 503 explicando; token ausente ou errado → 403 com *"inicie pelo
  start.py"*.
- **Login:** Playwright ausente, perfil ocupado ou processo que termina com
  erro → o motivo aparece na situação da sessão.
- **Nenhuma chave aparece em log, resposta ou mensagem de erro.** Mensagens de
  teste traduzem o status (401/403 → chave recusada; 429 → limite de consultas;
  rede → sem conexão).

## Testes

**Motor** (`servico-python/tests/`, pytest):

- `test_busca.py`: bases em paralelo com conectores falsos; limite por busca
  isolado entre duas buscas simultâneas; base sem chave pulada sem chamar o
  conector; erro de uma base isolado; prazo total com base lenta.
- `test_config_env.py`: preserva comentários e ordem; descomenta `# KEY=`;
  acrescenta; `null` remove; recusa quebra de linha e chave desconhecida sem
  gravar nada; atualiza `os.environ`; mascaramento; origem `modelo`/`terminal`.
- `test_rotas_config.py`: 403 sem token e com token errado; GET/PUT/testar com
  token; `/buscar` sem token.
- `test_sessao.py`: login abre o subprocesso uma vez (409 na segunda); ao
  terminar com login feito liga `ORBIS_SESSAO_NAVEGADOR`; falha ao abrir vira
  situação legível. Subprocesso e navegador simulados.
- `motor/src/search/tests`: `limite_da_busca` por thread.

**ORBIS** (`tests/*.mjs`):

- `tests/multi-search.mjs`: `juntar` descarta repetidos e normaliza DOI;
  `resumo` com base pulada, com erro e com tempo esgotado.
- `tests/python-bridge.mjs`: chamadas novas levam `X-Orbis-Token`; motor fora
  → `null`; `isLocalEngine`.
- `tests/bases.mjs`: `BASES` inclui `todas` e as do motor, com `local` certo.
- `pnpm exec tsc --noEmit`.

**Verificação real:** subir pelo `start.py`; salvar uma chave por
`PUT /api/settings` e conferir o `motor/.env`; `POST /config/testar` numa base
sem chave obrigatória; buscar em **Todas as bases** com uma expressão real num
projeto de teste e conferir os DOIs no lote e o resumo por base. A janela de
login real só abre quando o pesquisador clicar.

## Fora do escopo

- Automatizar o buscador do Portal de Periódicos CAPES.
- Web of Science.
- Os demais blocos de `configuracoes-design.md` (IA, lote, chaves de
  download do motor). Este desenho não os impede: os itens novos entram em
  `config_env.ITENS`, e os que são do ORBIS podem ganhar a tabela `settings`
  prevista lá.
- Criptografia das chaves: continuam em texto no `motor/.env` local, como hoje.
