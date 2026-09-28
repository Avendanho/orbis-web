# ORBIS

Plataforma para revisões sistemáticas e de escopo: planejamento do protocolo
(PCC), entrada de artigos por DOI, busca em bases ou PDFs do computador,
triagem por título e resumo, avaliação do texto completo, adjudicação e
relatórios PRISMA — com apoio de IA que nunca substitui a decisão humana.

Este repositório reúne duas partes:

- **Interface web** (`app/`, `lib/`) — React sobre Cloudflare Worker (vinext),
  com banco D1 e arquivos no R2. Corresponde à versão 40 publicada do ORBIS,
  acrescida da integração com o motor.
- **Motor Python** (`motor/`) — busca em 11 bases, recuperação de PDFs por
  dezenas de fontes com validação de identidade bibliográfica, extração de
  texto e triagem por IA. É exposto à interface por `servico-python/`.

O motor é opcional: sem ele a interface funciona, apenas sem a cadeia completa
de download e a leitura do texto dos PDFs.

## Partida rápida

Requisitos: Node 22.13+ e Python 3.10+.

```bash
python3 start.py          # Windows: py start.py
```

Na primeira execução o script instala as dependências, cria o banco local e
os arquivos de configuração a partir dos exemplos, e sobe:

- ORBIS em <http://localhost:5173>
- motor em <http://127.0.0.1:8900/saude>

Instalação manual, variáveis de ambiente e uso do motor pela linha de comando
estão em [COMO_RODAR.md](COMO_RODAR.md).

## Estrutura

```
app/               telas e rotas da API (Worker)
lib/               regras de domínio, fontes bibliográficas, IA, backup
components/ui/     componentes shadcn
db/  drizzle/      esquema e migrações do D1
tests/             testes da interface (node tests/<arquivo>.mjs)
motor/             motor Python (ver motor/README.md)
servico-python/    API HTTP que liga o motor à interface
scripts/  build/   ferramentas de build e hospedagem
docs/              notas de hospedagem e planos pendentes
start.py           sobe tudo
```

## Testes

```bash
for t in tests/*.mjs; do node "$t"; done     # interface (worker-integration exige pnpm build)
pnpm exec tsc --noEmit                       # tipos
cd motor && .venv/bin/python -m pytest -q    # motor
cd servico-python && .venv/bin/python -m pytest -q
```

## Documentação

- [COMO_RODAR.md](COMO_RODAR.md) — instalação, variáveis, testes, solução de problemas
- [IMPLEMENTATION.md](IMPLEMENTATION.md) — o que a interface faz e seus limites conhecidos
- [motor/README.md](motor/README.md) — o motor Python
- [docs/hospedagem-sites.md](docs/hospedagem-sites.md) — ciclo de vida na hospedagem Sites
- [docs/planos/](docs/planos/) — seção de Configurações (planejada, ainda não implementada)

Chaves de API ficam em `motor/.env` e no ambiente do Worker; nenhuma entra no
repositório.
