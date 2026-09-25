# Seção de Configurações — design

Data: 2026-09-24 · Status: aguardando revisão

## Objetivo

Uma seção **Configurações** na interface do ORBIS onde quem usa a instalação
ajusta tudo o que é modular e varia de pessoa para pessoa — credenciais das
bases, IA de triagem, ritmo do lote, chaves e opções do motor — sem editar
variável de ambiente nem `motor/.env`.

**Sucesso:** um pesquisador que acabou de instalar o ORBIS consegue, só pela
tela, cadastrar suas chaves, testar cada uma e ver o efeito na próxima busca ou
download, sem abrir arquivo nenhum.

## Decisões já tomadas

- **Instalação local por pessoa.** As configurações valem para a instalação
  inteira; não há separação por login.
- **Cobre ORBIS e motor.** A tela também edita as chaves e opções do motor.
- **Abordagem A:** configurações do ORBIS no banco local (D1); as do motor
  ficam no `motor/.env`, que o próprio motor lê e grava por uma rota nova.
  Recusadas: tudo no `.env` via motor (o ORBIS passaria a depender do motor
  para funcionar) e tudo no navegador (o servidor não enxerga, e se perde ao
  trocar de navegador).

## Achado que entra no escopo

O serviço de download (`servico-python/`) **nunca carrega `motor/.env`**: só
`motor/src/search/` chama `load_dotenv`. O processo do motor enxerga apenas o
ambiente herdado do terminal que rodou o `start.py`. Hoje, portanto, as chaves
de Unpaywall, Elsevier, Gemini etc. escritas em `motor/.env` não chegam aos
downloads. A rota de configuração só faz sentido se o motor ler o arquivo, então
a correção faz parte deste trabalho.

## O que a tela configura

Um item **Configurações** na barra lateral, fora dos projetos. Cinco blocos:

| Bloco | Itens | Onde vale |
|---|---|---|
| Bases de busca | NCBI chave e e-mail; Embase/Elsevier chave e token institucional; base padrão; limite de resultados (1–500) | ORBIS (Elsevier também no motor) |
| IA de triagem | Chaves Anthropic, OpenAI, Gemini; provedor preferido (automático/Anthropic/OpenAI/Gemini); modelo de cada provedor | ORBIS e motor |
| Lote de consultas | Consultas simultâneas (1–4, hoje fixo em 2); modo de download padrão para projetos novos (baixar/analisar) | ORBIS |
| Motor | Chaves do `motor/.env` (Unpaywall, Crossref, OpenAlex, Semantic Scholar, Springer, CORE, Wiley, Scopus, IEEE, EZproxy, proxy) e opções liga/desliga (`PAPER_FETCH_*`) | Motor |
| Diagnóstico | Botão **Testar** por credencial, fazendo uma chamada real mínima | — |

`ORBIS_ENGINE_URL` e `ORBIS_DATA_DIR` **não** entram: o `start.py` precisa
deles antes de a tela existir.

## Arquitetura

```
Navegador ──/api/settings──▶ ORBIS (Worker)
                               ├─ tabela settings (D1)   ← chaves do ORBIS
                               └─ python-bridge ──/config──▶ motor
                                                             └─ motor/.env
```

### 1. Catálogo único — `lib/settings-catalog.ts`

Uma lista declarativa, e a única fonte da verdade sobre o que é configurável.
Cada item traz: `key` (o mesmo nome da variável de ambiente, ex.
`NCBI_API_KEY`), `grupo`, `rotulo`, `ajuda`, `tipo` (`secret`, `text`,
`email`, `url`, `number`, `select`, `boolean`), limites e opções, `padrao`, e
`destino`: `orbis`, `motor` ou `ambos`.

A interface desenha os campos a partir dela; o servidor valida por ela. Não há
lista paralela no código de tela.

### 2. Armazenamento e leitura — `lib/settings.ts`

- Migração `drizzle/0003_settings.sql`:
  `settings(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated TEXT NOT NULL)`.
  O `start.py` já aplica migrações novas sozinho.
- `loadSettings()` resolve cada chave nesta ordem: **valor salvo na tela →
  variável de ambiente → padrão do catálogo**. Quem já configurou pelo ambiente
  não perde nada.
- `saveSettings(mudancas)` valida cada item pelo catálogo; `null` apaga (volta
  ao ambiente/padrão). Chave fora do catálogo é recusada.
- `publicView()` é o que vai para o navegador: segredos nunca saem inteiros, só
  `••••` + os 4 últimos caracteres, e cada item informa a **origem** (`tela`,
  `ambiente`, `padrão`) para a pessoa entender por que uma chave "já está lá".

### 3. API — `app/api/settings/route.ts`

- `GET` → visão pública do ORBIS + visão do motor (ou `motor: {online:false}`).
- `PUT {mudancas:{KEY: valor|null}}` → separa por destino, grava no D1, envia a
  parte do motor para ele. `ambos` vai aos dois. Resposta diz o que foi salvo em
  cada lado e quais chaves pedem reinício do motor.
- `POST {acao:'testar', alvo}` → chamada real mínima: NCBI (esearch de 1
  registro), Embase (1 registro), LILACS (1 registro, sem chave), Unpaywall (1
  DOI conhecido), cada provedor de IA (prompt de uma palavra). Resposta:
  `{ok, detalhe}` em português, nunca com o valor da chave.
- Exige login, como as outras rotas.

### 4. Motor — `servico-python/main.py` e novo `servico-python/config_env.py`

- Na partida: `load_dotenv(motor/.env, override=False)` — corrige o achado
  acima. O ambiente do terminal continua prevalecendo sobre o arquivo.
- `GET /config` → para cada chave da lista permitida: preenchida ou não, e o
  valor mascarado.
- `PUT /config` → grava no `motor/.env` **preservando comentários e a ordem**:
  substitui a linha `KEY=`, ou descomenta a linha `# KEY=` do modelo, ou acrescenta no
  fim. Atualiza `os.environ` na hora. Devolve `reiniciar: [...]` para chaves
  que o código lê só na importação (ex.: `UNPAYWALL_EMAIL`, `CORE_API_KEY` em
  `fetch.py`); as demais valem imediatamente.
- A lista de chaves permitidas vive no motor (`config_env.py`). Um teste
  confere que ela bate com os itens `motor`/`ambos` do catálogo do ORBIS.
- **Proteção:** o motor aceita CORS de qualquer origem, então sem mais nada
  qualquer página aberta no navegador poderia gravar, por exemplo, um
  `HTTP_PROXY` no motor local. `/config` passa a exigir o cabeçalho
  `X-Orbis-Token`, com um segredo que o `start.py` gera a cada execução e
  entrega ao ORBIS e ao motor (`ORBIS_ENGINE_TOKEN`). O ORBIS chama o motor pelo
  servidor, nunca pelo navegador.

### 5. Quem passa a ler as configurações

| Hoje | Depois |
|---|---|
| Busca lê `NCBI_*`, `EMBASE_*`/`ELSEVIER_*` do ambiente | `loadSettings()`; base padrão e limite da tela |
| `pickProvider(globalThis)` escolhe pela primeira chave existente, modelo fixo | `pickProvider(settings)` respeita provedor preferido e modelo; "automático" mantém a regra de hoje |
| `article-resolver` lê `UNPAYWALL_EMAIL` do ambiente | `loadSettings()` |
| `workspace.tsx` roda 2 consultas simultâneas fixas | Valor da tela (1–4) |
| Projeto novo nasce com o modo de download padrão do código | Valor da tela |

### 6. Tela — `app/settings-panel.tsx`

- Item na barra lateral; abre sem projeto selecionado.
- Um cartão por bloco, desenhado a partir do catálogo. **Salvar** por cartão.
- Segredo preenchido aparece mascarado, com **Trocar** e **Remover**; o campo
  nunca é preenchido com o valor real.
- Etiqueta de origem ao lado de cada item (`tela` / `ambiente` / `padrão`).
- Bloco Motor desabilitado com "motor fora do ar" quando o motor não responde.
- Aviso "reinicie o ORBIS (start.py) para aplicar" quando a resposta pede
  reinício.

## Erros

- Validação: `400` com o nome do campo e o motivo; nada é gravado.
- Motor fora do ar num `PUT` com itens `ambos`: a parte do ORBIS é salva, a
  resposta diz explicitamente que o motor **não** recebeu e o que ficou
  pendente. Sem sucesso silencioso pela metade.
- Teste com falha mostra a mensagem do serviço traduzida (401 → "chave
  recusada", 429 → "limite de consultas", rede → "sem conexão").
- Valores de configuração nunca aparecem em log nem em mensagem de erro.

## Testes

- `tests/settings.mjs`: validação pelo catálogo, ordem de resolução
  (tela → ambiente → padrão), mascaramento, recusa de chave desconhecida,
  `pickProvider` com preferência e modelo.
- `servico-python/tests/test_config_env.py`: reescrita do `.env` preservando
  comentários, descomentar linha do modelo, acrescentar, apagar, recusa sem
  token, lista de reinício.
- Teste de paridade: chaves do motor no catálogo = lista permitida do motor.
- Verificação real: subir pelo `start.py`, salvar uma chave pela API, rodar os
  botões **Testar**, confirmar que o `motor/.env` mudou e que uma busca PubMed
  usa a chave salva.

## Fora do escopo

- Configurações por usuário (a instalação é pessoal).
- Criptografia das chaves no banco: ficam como já ficam hoje no `.env`, em
  texto no disco local. A proteção aqui é nunca devolvê-las ao navegador.
- Editar `ORBIS_ENGINE_URL`/`ORBIS_DATA_DIR` pela tela.
