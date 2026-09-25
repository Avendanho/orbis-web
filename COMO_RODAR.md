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
 │   └─ requirements.txt
 └─ servico-python/         expõe o motor para o ORBIS
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

As chaves de API ficam em `motor/.env`, criado a partir de `motor/.env.example`
na primeira execução. Nenhuma é obrigatória.

### Node pelo nvm (Linux, opcional no macOS)

```bash
curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"
nvm install 22
```

O `start.py` carrega o nvm sozinho quando ele existe.

---

## Instalação manual (se preferir sem o start.py)

### ORBIS

```bash
corepack pnpm install
python start.py --so-orbis   # cria as tabelas do banco local e sobe a interface
```

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

### Onde ficam os dados

Por padrão na raiz do motor: `motor/pdfs/`, `motor/data/`, `motor/relatorio/`.
Para apontar um acervo que já existe:

```bash
export ORBIS_DATA_DIR=/caminho/do/acervo
```

Nada disso entra no controle de versão.

---

## Variáveis de ambiente

Todas opcionais. Sem elas o sistema funciona, só com menos recursos.

| Variável | Para quê | Sem ela |
|---|---|---|
| `UNPAYWALL_EMAIL` | Unpaywall, a fonte de maior rendimento | A fonte se omite |
| `NCBI_API_KEY`, `NCBI_EMAIL` | Eleva o teto de consultas do PubMed (e da Cochrane, que passa pelo PubMed) | Funciona, mais devagar |
| `EMBASE_API_KEY` (ou `ELSEVIER_API_KEY`), `EMBASE_INST_TOKEN` (ou `ELSEVIER_INST_TOKEN`) | Busca no Embase | A busca no Embase responde com erro de credencial; as outras bases seguem |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` | Triagem automática por IA | O botão não aparece; a importação manual continua |
| `ORBIS_ENGINE_URL` | Liga o ORBIS ao motor | O ORBIS ignora o motor |
| `ORBIS_DATA_DIR` | Onde o motor guarda PDFs e banco | Usa a raiz do motor |

As chaves do motor vão em `motor/.env`. As do ORBIS, no ambiente do Worker.
**Nenhuma delas entra no repositório.**

---

## Testes

```bash
# ORBIS — 9 suítes
for t in tests/*.mjs; do node "$t"; done

# verificação de tipos
pnpm exec tsc --noEmit

# motor — 262 testes
cd motor && .venv/bin/python -m pytest -q
```

Os testes do ORBIS rodam **sem `pnpm install`**: carregam o TypeScript direto.
A exceção é `worker-integration.mjs`, que precisa do build (`pnpm build`) e
leva alguns minutos.

Antes de publicar, conforme a regra 8 do projeto:

```bash
pnpm exec tsc --noEmit && pnpm build && node tests/worker-integration.mjs
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

Estas pastas somam mais de 3 GB e se regeneram sozinhas:

```bash
cd ~/Área\ de\ trabalho/orbis-web
rm -rf .sites-runtime node_modules dist .wrangler tsconfig.tsbuildinfo
rm -rf motor/.venv servico-python/.venv motor/.env
find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null

cd .. && zip -qr orbis-web.zip orbis-web
```

Sem elas, o projeto fica em **5,6 MB**.

A `.sites-runtime` (1,3 GB) é criada pelo `pnpm dev` e é a maior de todas —
não esqueça dela.

Os cinco `orbis-web-v1*.tar.gz` na raiz são versões antigas do ORBIS (3 MB).
Nada os usa; dá para apagar.

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
