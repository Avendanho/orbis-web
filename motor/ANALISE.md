# Recuperação de PDFs por DOI — análise e melhorias

Setembro de 2026. Motor em `motor/src/download/`, usado pelo ORBIS através de
`servico-python/`.

## Resumo

- **Medição** em 100 DOIs sorteados das duas listas reais do projeto (283
  DOIs), só fontes legais, com o mesmo prazo e paralelismo do ORBIS:
  **40% antes, 40% depois** — nenhum DOI ganho ou perdido; 8% mais rápido.
- **O limite é o acesso, não o código.** Segundo a OpenAlex, só **48 dos 100**
  têm alguma cópia legal em acesso aberto — e o motor já recuperava 40 deles
  (83%). Os outros 52 são de assinatura: nenhuma fonte aberta os tem.
  **80% só é alcançável com acesso institucional.**
- Por isso a principal entrega é uma **rota por sessão institucional**
  (Portal de Periódicos da CAPES via CAFe, ou EZproxy da biblioteca): o
  pesquisador faz o login uma vez num navegador e o motor reaproveita a
  sessão, com ritmo controlado. Ela não entrou na medição porque exige o
  login do pesquisador — é o primeiro dos [próximos passos](#próximos-passos).
- **Nenhuma chave de API está ativa.** O `motor/.env` é cópia literal do
  exemplo; a Unpaywall fica desligada e a OpenAlex roda na cota anônima.
- A medição expôs defeitos que tiravam artigos abertos da mão do motor —
  APIs estouradas em rajada que somem no meio da execução, material
  suplementar baixado no lugar do artigo, acesso institucional que não
  alcançava artigo fechado. Todos corrigidos e cobertos por testes.

## Como foi medido

`scripts/benchmark.py` roda cada DOI por `fetch.fetch` exatamente como o
serviço do ORBIS: prazo de 90 s por artigo, 4 artigos em paralelo, pasta vazia
(sem cache). Sucesso é o mesmo critério do ORBIS: PDF estruturalmente válido
**e** identidade bibliográfica confirmada.

- Amostra: 100 DOIs sorteados (semente 42) de `lista_doi_para_script.csv` e
  dos DOIs do `Relatório.txt` do projeto — 283 DOIs únicos no total.
- Fontes: todas as legais. Sci-Hub, LibGen e Anna's Archive ficam de fora
  (`--fontes legais`), assim como a raspagem do Google Scholar.
- `--teto-oa` anota cada DOI com a situação de acesso aberto da OpenAlex, o que
  separa "o motor errou" de "não existe cópia legal".
- A configuração é a da máquina: `.env` com o texto do exemplo, sem sessão
  institucional, fora de rede de campus.

## Como o sistema funciona

```
ORBIS (Worker) ──HTTP──> servico-python/main.py ──> baixar.py ──> fetch.fetch(doi)
CLI: src/download/run_parallel.py ─────────────────────────────────┘

fetch.fetch  =  recuperação por título  ∘  descoberta expandida  ∘  cascata principal
                (fetch_title_direct)       (11 repositórios em     (_fetch_original)
                                            paralelo)
```

`fetch.py` define `fetch` três vezes: cada definição embrulha a anterior.

**Cascata principal**, em ordem; a primeira fonte que entrega um PDF com
identidade confirmada encerra o artigo:

Unpaywall → PMC no AWS (`pmc_s3`, via conversor de IDs do NCBI) → Semantic
Scholar → OpenAlex → arXiv → Europe PMC / PMC / PubMed → bioRxiv/medRxiv →
ACL → CORE → **sessão institucional (nova)** → LibGen → Crossref → URL direta
da editora → XML de editora (Elsevier/Springer) → resolvedor DOI → Open Access
Button → Sci-Hub → OSTI → OpenAlex Content → Wayback → Anna's Archive.

**HTTP.** APIs de metadados por `urllib` (`fetch._get`) com retentativa e
backoff exponencial com jitter, respeito a `Retry-After` e cooldown por host
(`http_retry.py`). PDFs por `curl_cffi` com cabeçalhos de navegador
(`bypass403.py` — o nome é histórico; não há mais spoofing), com recurso a
`urllib`, leitura de `citation_pdf_url` em páginas de destino (`pdf_links.py`),
EZproxy/proxy (`institutional.py`) e navegador para repositórios
(`browser_fetch.py`).

**Validação.** `validate_pdf_data`: `%PDF`, ≥ 1 KB, ≤ 50 MB, gzip, páginas
legíveis pelo pypdf, `%%EOF`. Depois, o portão de identidade
(`identity.validate_article_identity`): DOI impresso no PDF, ou registro com o
DOI certo sem título contraditório, ou título + autor/ano/periódico. Material
suplementar e PDFs de outro artigo são apagados.

**Cache, concorrência, logs.** Arquivo lateral `.identity.json` por PDF e
índice SQLite (`run_parallel`), com revalidação antes de reusar. Prazo por
artigo por thread. Eventos NDJSON por fonte (`_progress`) e contagem por fonte
(`_stats_record`).

## Diagnóstico

### 1. O limite é o acesso aberto que existe

Situação de acesso aberto dos 283 DOIs do projeto (OpenAlex):

| | DOIs | com cópia aberta |
|---|---|---|
| `lista_doi_para_script.csv` | 217 | 90 (41%) |
| DOIs do `Relatório.txt` | 66 | 55 (83%) |
| **todos** | **283** | **145 (51%)** |

Os fechados se concentram em Springer (28 de 34), Elsevier (27 de 47), Wiley
(19 de 28), Mary Ann Liebert (10 de 15) e IUCr (8 de 8). A taxa histórica de
~50% é esse teto — e na lista principal do projeto o teto é 41%.

Na amostra medida: 48 com cópia aberta, 52 fechados; **0 dos 52 fechados**
foram recuperados por qualquer fonte legal.

### 2. A configuração não está ativa

- `motor/.env` é idêntico a `motor/.env.example`. O serviço (corretamente)
  ignora valores iguais aos do exemplo — com isso a **Unpaywall fica
  desligada** (exige e-mail) e nenhuma chave vale.
- A OpenAlex anônima tem cota de 1.000 créditos por dia por IP
  (`x-ratelimit-limit: 1000`) e tolera rajadas bem abaixo dos 10 req/s
  documentados para quem tem chave.
- O Semantic Scholar anônimo usa um pool compartilhado por todos: 28 respostas
  429 numa única execução de 100 DOIs.
- A linha de comando (`run_parallel.py`) não lia o `motor/.env` em nenhum
  caso.

### 3. O acesso institucional não alcançava artigo fechado

O suporte existia (proxy, EZproxy com usuário e senha, insttoken da Elsevier,
TDM da Wiley), mas:

- o EZproxy só entrava depois que uma URL candidata falhava — e, para artigo
  fechado, quase não há candidata: as fontes abertas não devolvem nada;
- as URLs diretas das editoras (~40 moldes por prefixo de DOI) só eram geradas
  com `PAPER_FETCH_INSTITUTIONAL=1`, uma segunda flag, fácil de esquecer;
- quando a editora devolvia a página do artigo em vez do PDF (o normal pelo
  proxy), o EZproxy desistia em vez de seguir o `citation_pdf_url`;
- o login da CAPES (CAFe) é SSO no navegador — não há usuário e senha que um
  cliente HTTP possa enviar. **Nenhuma rota servia ao acesso que o
  pesquisador brasileiro de fato tem.**

### 4. Os 8 artigos abertos que o motor perde

| DOI | situação | por que falha |
|---|---|---|
| 10.1016/j.stem.2025.09.001 | hybrid | ScienceDirect barra clientes automáticos (página intermediária) |
| 10.1016/j.lssr.2024.09.004 | bronze | idem |
| 10.1016/j.cherd.2026.06.053 | hybrid | idem |
| 10.1111/dme.12378_1 | bronze | Wiley responde 403 (desafio anti-robô) |
| 10.1196/annals.1362.024 | bronze | idem |
| 10.1089/scd.2023.0291 | hybrid | Liebert responde 403 |
| 10.1088/1758-5090/ae4ccd | hybrid | IOP bloqueia |
| 10.1016/0168-1656(94)90144-9 | green | repositório da EPFL exige verificação humana |

Um navegador real sem login também é barrado em todos os oito (testado). Os
três da Elsevier devem sair pela API oficial com uma chave gratuita — a
Elsevier serve artigos abertos a qualquer chave (não verificado aqui, por
falta de chave); os demais, pela sessão institucional. Não há correção de código legítima para os outros: são
recusas explícitas dos sites.

### 5. APIs estouradas em rajada somem no meio da execução

Com 4 artigos em paralelo e as buscas por título em mais 6 threads, as APIs de
metadados recebiam rajadas; respondiam 429, e o cooldown por host
(30 s → 120 s → 600 s) tirava a fonte do ar **para todos os artigos
seguintes**, sem nenhum aviso — o artigo aparecia como "nenhuma fonte tem PDF".
Não havia limite de ritmo por domínio.

O caso mais grave é o conversor de IDs do NCBI, porta de entrada da fonte que
mais entrega (`pmc_s3`, 29 dos 40 sucessos): uma resposta 429 era lida como
"este artigo não tem PMCID". Ele era chamado duas vezes por DOI, e o NCBI
limita 3 req/s por IP **somando** todos os seus hosts. Numa execução
intermediária, com ritmo por host mas não por grupo, isso custou um artigo
que a linha de base recuperava (10.1186/scrt399) e fez `pmc_s3` cair de 26
para 8 sucessos.

### 6. Material suplementar baixado no lugar do artigo

A Springer devolve a clientes automáticos a página do artigo em vez do PDF; o
`citation_pdf_url` dessa página aponta para a mesma URL, já visitada, e o
próximo link era o `MOESM1_ESM.pdf` — o suplemento. O portão de identidade o
rejeitava, mas o candidato estava gasto.

### 7. Filtro de fontes ignorado na descoberta expandida

`--sources` / `PAPER_FETCH_SOURCES` limitava a cascata principal, mas bastava
nomear uma fonte expandida para rodarem **todas** as 13 — inclusive a
raspagem do Google Scholar.

### 8. Fontes-sombra

Sci-Hub, LibGen e Anna's Archive vêm ligadas por padrão, e a LibGen roda
antes das rotas legais de editora. Elas distribuem cópias sem licença dos
detentores de direitos, o que conflita com a exigência de respeitar direitos
autorais. **Por decisão do responsável, continuam como estão**; a medição as
exclui, o diagnóstico as sinaliza, e a nova sessão institucional entra antes
da LibGen, para que a rota legal tenha precedência.

### 9. Pontos únicos de falha e gargalos

- `fetch.py` tinha 5.200 linhas e três `fetch` sobrepostos por redefinição;
  ordem e orçamento de tempo das fontes ficam implícitos no corpo de uma função
  de 900 linhas.
- Uma fonte responde por 3/4 dos sucessos (`pmc_s3`), e ela depende de um
  único conversor de IDs.
- Artigos sem saída consomem até o prazo inteiro (média de 27 s por DOI,
  contra 3 s nos sucessos).

## O que foi feito

| Mudança | Onde | Efeito | Testes |
|---|---|---|---|
| Sessão institucional no navegador (CAPES/CAFe ou EZproxy): login uma vez, depois uma thread dona do Chromium, um artigo por vez, 6 s de intervalo, teto de 150 por execução, detecção de sessão expirada, retomada sem reiniciar após novo login | `sessao_navegador.py`, fonte `sessao_institucional` em `fetch.py` | rota legal para os artigos de assinatura | `test_sessao_navegador.py`, `test_rotas_institucionais.py` |
| EZproxy/proxy configurado já liga as URLs diretas das editoras | `fetch.py` | o EZproxy passa a ter o que reescrever | `test_rotas_institucionais.py` |
| EZproxy segue o `citation_pdf_url` da página autenticada | `institutional.py` | o EZproxy entrega o PDF em vez de desistir | idem |
| Ritmo por host nas APIs, com grupo único para o NCBI e ritmo mais lento sem chave | `http_retry.py`, `fetch._get` | menos 429 e menos fontes em cooldown | `test_ritmo_e_ambiente.py` |
| Conversor de IDs do NCBI: uma consulta por DOI; falha não vira "sem PMCID" | `sources_pubmed.py` | protege a rota que mais entrega | idem |
| Material suplementar nunca é candidato | `pdf_links.py`, `fetch._try_candidate` | candidatos não se perdem em suplementos | `test_links_suplementares.py` |
| `sources` vale também na descoberta expandida | `fetch.py` | o filtro de fontes funciona | `test_rotas_institucionais.py` |
| `.env` do motor carregado pela CLI, ignorando placeholders | `ambiente.py`, `run_parallel.py`, `fetch.py` | a CLI usa as chaves configuradas | `test_ritmo_e_ambiente.py` |
| Mensagem do ORBIS quando a sessão expira | `servico-python/baixar.py` | o pesquisador sabe como renovar | `test_baixar.py` |
| Diagnóstico da configuração | `scripts/diagnostico.py` | diz o que está ativo e o que fazer, em ordem de ganho | — |
| Benchmark com teto OA e falhas transitórias por host | `scripts/benchmark.py` | mede antes/depois | — |

## O que não foi feito, e por quê

- **Reescrever a cascata como pipeline de etapas.** A cascata já é em etapas
  com fallback. Reescrever 5.200 linhas cobertas por 289 testes traria risco
  alto e nenhum ganho de taxa: a medição mostra que a perda está no acesso,
  não na orquestração. A refatoração que vale a pena é incremental (abaixo).
- **`tenacity`.** `http_retry.py` já faz backoff exponencial com jitter,
  `Retry-After` e cooldown por host; trocar a biblioteca não muda resultado.
- **Rotação de User-Agent, `undetected-chromedriver`, stealth.** Os bloqueios
  que restam (ScienceDirect, Wiley, Liebert, IOP, EPFL) são recusas
  explícitas a clientes automáticos. Contorná-las é evasão de controle de
  acesso; o caminho legítimo para esses artigos é API oficial (chave
  Elsevier) ou a sessão institucional do próprio pesquisador. O navegador da
  sessão não usa disfarce nenhum.
- **Cache de metadados.** O PDF validado já é reutilizado (com revalidação), e
  o conversor de IDs agora tem cache; o restante das consultas se repete
  pouco dentro de uma execução.
- **Logs JSON novos.** Os eventos NDJSON por fonte já existiam; o benchmark
  agrega por fonte, por categoria de falha e por host.

## Resultado

| | antes | depois |
|---|---|---|
| taxa de sucesso (fontes legais, sem login) | 40% (40/100) | **40% (40/100)** |
| abertos recuperados | 40/48 | 40/48 |
| fechados recuperados | 0/52 | 0/52 |
| tempo médio por DOI | 27,0 s | 24,8 s |
| duração da execução (4 em paralelo) | 682 s | 634 s |

**Nesta máquina — sem login institucional e sem nenhuma chave — a taxa não
mudou, e não havia como mudar**: o motor já recuperava 40 dos 48 artigos com
cópia aberta, e os 8 restantes são recusas explícitas dos sites (seção 4). O
que as correções de código entregam aqui é estabilidade: uma execução
intermediária, com o ritmo por host ainda sem o grupo do NCBI, perdeu um
artigo que a linha de base recuperava, porque o PMC saía do ar no meio; na
versão final, `pmc_s3` volta aos 26 sucessos e o NCBI não entra mais em
cooldown — seus 429 são absorvidos pelas retentativas.

Mesmo assim, as APIs anônimas continuam no limite: na execução final, o
Semantic Scholar respondeu 429 40 vezes, a OpenAlex 15 (e entrou em cooldown
15 vezes, a 2 req/s), o Crossref 14 (sem e-mail ele atende pelo pool público,
mais restrito). Só as chaves resolvem isso — é o passo 2 abaixo.

**Meta de 80%: não atingida nesta máquina**, e não pode ser atingida
só com fontes abertas neste acervo — o teto é 48%. Com a sessão institucional,
cada artigo fechado coberto pelo acervo da CAPES entra na conta: atingir 80%
exige recuperar de 32 a 40 dos 52 fechados da amostra (62–77%) — 32 se a
sessão também destravar os 8 abertos barrados, 40 se não. Elsevier, Springer
Nature e Wiley somam 35 dos 52 fechados (67%) e estão no Portal de
Periódicos, mas a cobertura real (anos, coleções) depende da instituição e só
a medição com login diz.

## Próximos passos

1. **Fazer o login institucional e medir.** É o que decide a meta:

   ```bash
   cd motor
   .venv/bin/python src/download/sessao_navegador.py login
   # ORBIS_SESSAO_NAVEGADOR=1 no motor/.env
   .venv/bin/python scripts/diagnostico.py --testar-sessao 10.1007/s10719-009-9256-7
   .venv/bin/python scripts/benchmark.py --arquivo lista_doi_para_script.csv --amostra 100 --teto-oa
   ```

   Com o intervalo de 6 s, 100 artigos levam uns 20 a 40 minutos a mais;
   não reduza o intervalo — é o que protege o acesso da instituição.
2. **Preencher as chaves gratuitas** (`scripts/diagnostico.py` lista em ordem
   de ganho): chave Elsevier (deve destravar 3 dos 8 abertos perdidos), e-mail da
   Unpaywall, chaves da OpenAlex e do Semantic Scholar, `NCBI_API_KEY`.
3. **Decidir sobre as fontes-sombra.** Se o projeto for publicado ou usado
   por terceiros, desligá-las por padrão (`PAPER_FETCH_NO_SCIHUB=1`,
   `PAPER_FETCH_NO_LIBGEN=1`, e tirar `annas_archive` de
   `PAPER_FETCH_SOURCES`) evita distribuir cópias sem licença.
4. **Refatoração incremental de `fetch.py`.** Transformar cada bloco da cascata
   em uma função `etapa(doi, contexto) -> candidatos`, numa lista ordenada
   declarada num só lugar, com orçamento de tempo por etapa. Permite reordenar
   (por exemplo, pôr a sessão institucional antes quando a OpenAlex já disse
   que o artigo é fechado) e testar cada etapa isolada.
5. **Encerrar cedo artigos sabidamente fechados** quando não há rota
   institucional: hoje eles gastam o prazo inteiro percorrendo repositórios.
