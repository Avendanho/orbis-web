# Motor de revisão sistemática

Mecanismo Python usado pelo ORBIS. **Não tem interface própria** — quem mostra
as telas é o ORBIS, na pasta acima.

```
src/search/      busca em 11 bases (PubMed, Embase, LILACS, Europe PMC,
                 Scopus, OpenAlex, Semantic Scholar, IEEE, CORE, Oasisbr, BASE)
src/download/    recuperação de PDFs, validação de identidade bibliográfica
src/analysis/    extração de texto, triagem por IA, relatórios PRISMA
```

## Como rodar

O caminho normal é pelo ORBIS:

```bash
cd .. && python3 start.py
```

Direto, pela linha de comando:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # preencha as chaves que tiver

.venv/bin/python src/search/headless_runner.py --bases PubMed,EuropePMC,OpenAlex
.venv/bin/python src/download/run_parallel.py --file "DOI's.txt" --out ~/meus-pdfs
cd src/analysis && ../../.venv/bin/python main.py scan
```

> `--out` relativo é resolvido a partir da pasta do script, não de onde você
> chamou. Use caminho absoluto.

## Taxa de recuperação

Metade de um acervo típico é de assinatura e não tem cópia aberta em lugar
nenhum; o que decide a taxa é a configuração. Diagnóstico e medição em
[ANALISE.md](ANALISE.md).

```bash
.venv/bin/python scripts/diagnostico.py                       # o que está funcionando e o que fazer
.venv/bin/python src/download/sessao_navegador.py login       # acesso CAPES/CAFe ou EZproxy, uma vez
.venv/bin/python scripts/benchmark.py --arquivo lista.csv --amostra 100 --teto-oa
```

Instruções completas, incluindo variáveis de ambiente e a ligação com o ORBIS:
**[../COMO_RODAR.md](../COMO_RODAR.md)**.

## Testes

```bash
.venv/bin/python -m pytest -q      # 336 testes
```

## Dados

Por padrão em `pdfs/`, `data/` e `relatorio/`, aqui nesta pasta. Para apontar
outro acervo: `export ORBIS_DATA_DIR=/caminho`.
