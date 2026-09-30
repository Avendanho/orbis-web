# ORBIS — como rodar

Projeto único: a interface web (ORBIS) e o motor de revisão sistemática
(Python) na mesma pasta.

```
orbis-web/
 ├─ start.py                sobe tudo  ←  comece por aqui
 ├─ app/  lib/  tests/      ORBIS — a interface (React + Cloudflare Worker)
 ├─ motor/                  motor de revisão sistemática (Python)
 │   ├─ src/search/           busca em 11 bases
 │   ├─ src/download/         recuperação de PDFs e validação de identidade
 │   ├─ src/analysis/         extração de texto, triagem por IA, PRISMA
 │   ├─ scripts/              utilitários avulsos
 │   └─ requirements.txt
 ├─ servico-python/         expõe o motor para o ORBIS
 └─ docs/                   SISTEMA.md, ARQUITETURA.md, hospedagem, planos
```

O motor **não tem interface própria**. Ele é um mecanismo; quem mostra as
telas é o ORBIS.

---

## Partida rápida

Funciona em **Windows, macOS e Linux**. Você precisa de duas coisas instaladas:

| | Windows | macOS | Linux |
|---|---|---|---|
| **Node 22 ou mais novo** | instalador LTS em <https://nodejs.org> | <https://nodejs.org> ou `brew install node@22` | nvm (abaixo) ou o pacote da distribuição |
| **Python 3.10 ou mais novo** | <https://python.org> — marque *Add python.exe to PATH* | <https://python.org> ou `brew install python` | já vem; no Ubuntu/Debian instale também `python3-venv` |

Depois, dentro da pasta do projeto:

| Sistema | Com dois cliques | Pelo terminal |
|---|---|---|
| Windows | `start.bat` | `py start.py` |
| macOS | `start.command` (na 1ª vez: botão direito → Abrir) | `python3 start.py` |
| Linux | — | `python3 start.py` |

Na primeira vez ele instala o que falta (alguns minutos, precisa de internet)
e cria o banco local. Depois:

- ORBIS: <http://localhost:5173> — abre sozinho no navegador
- Motor: <http://127.0.0.1:8900/saude>

`Ctrl+C` encerra os dois.

```bash
python start.py --so-orbis    # só a interface
python start.py --so-motor    # só o motor
```

Se o Python falhar, o ORBIS sobe assim mesmo — sem as capacidades que dependem
dele (baixar pela cadeia completa de fontes, ler o texto de dentro do PDF e
conferir identidade pelo conteúdo).

Nenhuma chave de API é obrigatória. Cadastre as que tiver pela tela
**Configurações** (barra lateral): ela grava as do ORBIS no banco local e as do
motor em `motor/.env` (criado a partir de `motor/.env.example` na primeira
execução), e tem um botão **Testar** para cada uma.

### Node pelo nvm (Linux, opcional no macOS)

```bash
curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"
nvm install 22
```

O `start.py` carrega o nvm sozinho quando ele existe.

---

## IA local (Ollama)

A triagem e a análise PCC podem usar um modelo que roda na sua máquina, sem
mandar os textos para a nuvem:

1. Instale o [Ollama](https://ollama.com) e deixe-o aberto.
2. Baixe o modelo padrão (≈ 9 GB; precisa de uma GPU com 16 GB para rodar
   bem):

   ```bash
   ollama pull qwen3:14b
   ```

3. Em **Configurações → IA**, clique **Testar Ollama (local)**. Com o
   provedor em *Automático* (o padrão), o ORBIS usa o modelo local sempre que
   o Ollama responde, e cai na primeira chave de nuvem quando não responde.

Outro modelo instalado aparece na lista **Modelo local**. **Contexto** limita
quanto texto completo cabe por artigo na PCC (maior = mais memória da GPU);
**Prazo** é o tempo máximo de uma chamada. Conte com 15 a 25 segundos por
registro na triagem com o `qwen3:14b`.

---

## Instalação manual (se preferir sem o start.py)

### ORBIS

```bash
corepack pnpm install
cp .openai/hosting.example.json .openai/hosting.json
python start.py --so-orbis   # cria as tabelas do banco local e sobe a interface
```

O `.openai/hosting.json` declara os bindings do D1 e do R2 e, na versão
hospedada, o identificador do projeto; por isso só o exemplo vai para o
repositório.

Rodar `corepack pnpm dev` direto também funciona, mas só depois que o banco
local tiver as tabelas — sem elas a API responde "no such table: projects".

### Motor

```bash
cd motor
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # preencha as chaves que tiver
```

Python 3.10+. Nenhuma chave é obrigatória: a rota do PMC/AWS funciona sem
credencial nenhuma.

### Serviço que liga os dois

```bash
cd servico-python
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -r ../motor/requirements.txt
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8900
```

Conferir:

```bash
curl -s http://127.0.0.1:8900/saude | python3 -m json.tool
```

Deve listar `recuperacao`, `identidade`, `pmc` e `texto_do_pdf`. O serviço acha
o motor sozinho em `../motor/src/download`.

---

## Usar o motor direto, pela linha de comando

```bash
cd motor

# buscar em 11 bases (não interativo; as bases vão por argumento)
.venv/bin/python src/search/headless_runner.py --bases PubMed,EuropePMC,OpenAlex

# baixar os PDFs de uma lista de DOIs
.venv/bin/python src/download/run_parallel.py --file "DOI's.txt" --out ~/meus-pdfs --workers 8

# extrair o texto dos PDFs
cd src/analysis
../../.venv/bin/python main.py scan

# triagem por IA segundo o protocolo
../../.venv/bin/python main.py analyze

# relatórios e PRISMA
../../.venv/bin/python main.py report

# auditar a procedência do acervo
cd ../download && ../../.venv/bin/python audit_corpus.py
```

> **Atenção ao `--out`.** Um caminho relativo é resolvido a partir da pasta do
> script (`motor/src/download/`), não de onde você chamou o comando — então
> `--out pdfs` grava em `motor/src/download/pdfs/`. **Use caminho absoluto** e
> não há dúvida.

### Taxa de recuperação de PDFs: o que muda o resultado

A taxa depende mais da configuração do que do código. Numa amostra de 100
DOIs reais de uma revisão, só 48 tinham **alguma** cópia legal em acesso
aberto — e o motor recuperou 40 delas. Os outros 52 são de assinatura: só
saem com o acesso que a sua instituição já paga. Detalhes em
[motor/ANALISE.md](motor/ANALISE.md).

**1. Veja o que está funcionando:**

```bash
cd motor
.venv/bin/python scripts/diagnostico.py
```

Ele confere cada chave (e se ainda está com o texto do exemplo), a cota da
OpenAlex, se a rede tem acesso por IP às editoras e a sessão institucional, e
lista o que fazer em ordem de ganho.

**2. Acesso institucional pela CAPES (CAFe) ou pelo EZproxy da biblioteca.**
É o que alcança os artigos de assinatura. O login é o seu, feito uma vez num
navegador que o motor depois reaproveita:

```bash
cd motor
.venv/bin/python src/download/sessao_navegador.py login
```

Abre um Chromium. Entre no Portal de Periódicos da CAPES → *Acesso CAFe* →
sua instituição (ou no EZproxy da biblioteca), abra um artigo de assinatura
para conferir que o acesso funciona e **feche a janela**. Depois, no
`motor/.env`:

```
ORBIS_SESSAO_NAVEGADOR=1
# Se, com o login feito, o endereço dos artigos ficar como
# www-sciencedirect-com.ezNN.periodicos.capes.gov.br, informe o EZproxy:
# ORBIS_SESSAO_EZPROXY=https://ezNN.periodicos.capes.gov.br
```

Confira com um DOI de assinatura:

```bash
.venv/bin/python scripts/diagnostico.py --testar-sessao 10.1007/s10719-009-9256-7
```

A sessão baixa **um artigo por vez, com 6 s de intervalo e no máximo 150 por
execução** (`ORBIS_SESSAO_INTERVALO`, `ORBIS_SESSAO_MAX_ARTIGOS`). As
licenças das editoras proíbem download sistemático, e um robô rápido demais
faz a editora bloquear a instituição inteira — não afrouxe esses limites.
Quando a sessão expira, o motor avisa e para de usá-la; rode o `login` de
novo e ele volta a usá-la sozinho, sem reiniciar.

**3. Meça:**

```bash
.venv/bin/python scripts/benchmark.py --arquivo lista.csv --amostra 100 --teto-oa --saida relatorio/bench-1
# depois de mudar a configuração, compare:
.venv/bin/python scripts/benchmark.py --arquivo lista.csv --amostra 100 --teto-oa \
    --saida relatorio/bench-2 --comparar relatorio/bench-1/resultados.json
```

O benchmark usa só fontes legais (`--fontes todas` inclui as fontes-sombra,
que não contam para a meta) e, com `--teto-oa`, separa "o motor errou" de
"não existe cópia legal".

### Onde ficam os dados

Por padrão na raiz do motor: `motor/pdfs/`, `motor/data/`, `motor/relatorio/`.
No modo "baixar", cada artigo fica em `motor/pdfs/<projeto>/` como PDF, `.md`
(o texto em Markdown) e `<nome>_imagens/`. O token da tela de Configurações
fica em `motor/data/orbis-token`.
Para apontar um acervo que já existe:

```bash
export ORBIS_DATA_DIR=/caminho/do/acervo
```

Nada disso entra no controle de versão.

---

## Variáveis de ambiente

> A forma recomendada agora é a tela **Configurações** (barra lateral). Ela
> grava as chaves do ORBIS no banco local e as do motor em `motor/.env`, e
> mostra de onde vem cada valor. As variáveis abaixo continuam valendo como
> padrão: o que for salvo na tela tem prioridade.
>
> O `start.py` cria uma vez o token `motor/data/orbis-token` (ou
> `$ORBIS_DATA_DIR/data/orbis-token`) e o entrega ao ORBIS e ao motor como
> `ORBIS_ENGINE_TOKEN`. É ele que autoriza a tela a gravar no `motor/.env`.

Todas opcionais. Sem elas o sistema funciona, só com menos recursos.

| Variável | Para quê | Sem ela |
|---|---|---|
| `UNPAYWALL_EMAIL` | Unpaywall, a fonte de maior rendimento — precisa ser um e-mail seu, não o do exemplo | A fonte se omite |
| `OPENALEX_API_KEY` | OpenAlex sem o teto anônimo (~1000 consultas/dia por IP) | Para de responder no meio de acervos grandes |
| `ELSEVIER_API_KEY` | Artigos Elsevier em acesso aberto pela API oficial (a página da ScienceDirect barra robôs) | Esses artigos falham com bloqueio |
| `ORBIS_SESSAO_NAVEGADOR`, `ORBIS_SESSAO_EZPROXY` | Sessão institucional CAPES/CAFe ou EZproxy (ver acima) | Artigos de assinatura não saem |
| `NCBI_API_KEY`, `NCBI_EMAIL` | Eleva o teto de consultas do PubMed (e da Cochrane, que passa pelo PubMed) | Funciona, mais devagar |
| `EMBASE_API_KEY` (ou `ELSEVIER_API_KEY`), `EMBASE_INST_TOKEN` (ou `ELSEVIER_INST_TOKEN`) | Busca no Embase | A busca no Embase responde com erro de credencial; as outras bases seguem |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY`, `ORBIS_IA_MODELO_*` | IA de nuvem e o modelo de cada provedor | Só a IA local (se o Ollama estiver aberto) ou a importação manual |
| `ORBIS_IA_PROVEDOR` | `automatico` (padrão), `local`, `anthropic`, `openai` ou `gemini` | Automático |
| `OLLAMA_URL`, `OLLAMA_MODELO`, `OLLAMA_CONTEXTO`, `OLLAMA_PRAZO`, `ORBIS_IA_LOTE` | IA local e tamanho do lote de IA | `http://localhost:11434`, `qwen3:14b`, 16384, 600 s, 10 |
| `ORBIS_EXTRAIR_MARKDOWN`, `ORBIS_SALVAR_IMAGENS`, `ORBIS_EXTRACAO_PRAZO` | Texto em Markdown e imagens ao lado do PDF (motor) | Ligados; prazo de 90 s |
| `ORBIS_ENGINE_URL` | Liga o ORBIS ao motor | O ORBIS ignora o motor |
| `ORBIS_DATA_DIR` | Onde o motor guarda PDFs e banco | Usa a raiz do motor |

Pela tela, tudo vai ao lugar certo. À mão: as do motor em `motor/.env`; as do
ORBIS, no ambiente do Worker (`.dev.vars`). **Nenhuma delas entra no
repositório.**

---

## Testes

```bash
# ORBIS — 21 suítes
for t in tests/*.mjs; do node "$t"; done

# verificação de tipos
pnpm exec tsc --noEmit

# motor — 336 testes (e 67 no servico-python)
cd motor && .venv/bin/python -m pytest -q
```

Os testes do ORBIS rodam **sem `pnpm install`**: carregam o TypeScript direto.
As exceções são as `*-integration.mjs`, que precisam do build (`pnpm build`)
e levam alguns minutos.

Antes de publicar, conforme a regra 8 do projeto:

```bash
pnpm exec tsc --noEmit && pnpm build && for t in tests/*-integration.mjs; do node "$t" || break; done
```

---

## O que já foi resolvido (para não tropeçar de novo)

**`corepack: comando não encontrado`** — o Node 18 do apt não inclui o
corepack. A solução é o Node 22 pelo nvm, acima.

**`ENOSPC: System limit for number of file watchers reached`** — o Vite tentava
vigiar `motor/.venv/`, que tem dezenas de milhares de arquivos, e derrubava o
servidor na partida. O `vite.config.ts` agora exclui `motor/`,
`servico-python/`, `.venv/` e `__pycache__/` do watcher. Se voltar a aparecer
com outra pasta, o ajuste é no mesmo lugar.

**A interface própria do motor foi removida.** Sumiram `motor/frontend/`,
`motor/backend.py`, `motor/start.sh`, `motor/start.bat`,
`motor/src/download/web_ui.py` e `motor/src/search/cli_menu.py`. Duas coisas
foram preservadas na mudança: `display_results_summary` passou do menu para o
`headless_runner.py`, e as funções de motor de `src/search/main.py` ficaram,
sem o fluxo interativo.

---

## Antes de zipar

O pacote sai do próprio repositório, então leva só o que está versionado —
sem `.env`, chaves, token, `node_modules`, ambientes Python, PDFs nem listas
locais:

```bash
cd ~/Área\ de\ trabalho/orbis-web
git archive --format=zip --prefix=orbis-web/ -o ~/Área\ de\ trabalho/orbis-web.zip HEAD
```

Confira antes que o trabalho está commitado (`git status`): o que não estiver
no último commit não entra.

---

## Na outra máquina

```bash
unzip orbis-web.zip && cd orbis-web

# Node 22, se ainda não tiver
curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm install 22

python3 start.py
```

O `start.py` cuida do resto: confere o Node, instala as dependências web,
cria o ambiente Python, copia o `.env` do exemplo e sobe os dois serviços.
