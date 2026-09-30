# ORBIS

Plataforma para revisões sistemáticas e de escopo, na ordem em que elas
acontecem: planejamento do protocolo (PCC), identificação por DOI ou busca em
bases, triagem por título e resumo **antes** de qualquer download, obtenção do
texto completo só do que foi incluído (em Markdown, com as imagens na pasta
local), avaliação do texto completo, adjudicação e relatórios PRISMA — com
apoio de IA de nuvem ou local (Ollama) que nunca substitui a decisão humana.

Este repositório reúne duas partes:

- **Interface web** (`app/`, `lib/`) — React sobre Cloudflare Worker (vinext),
  com banco D1 e arquivos no R2. Corresponde à versão 40 publicada do ORBIS,
  acrescida da integração com o motor.
- **Motor Python** (`motor/`) — busca em 11 bases, recuperação de PDFs por
  dezenas de fontes com validação de identidade bibliográfica, extração de
  texto e triagem por IA. É exposto à interface por `servico-python/`.

O motor é opcional: sem ele a interface funciona, apenas sem a cadeia completa
de download e a leitura do texto dos PDFs. Chaves, IA e opções do motor se
ajustam pela tela **Configurações**.

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
docs/              como o sistema funciona, arquitetura, hospedagem, planos
start.py           sobe tudo
```

## Testes

```bash
for t in tests/*.mjs; do node "$t"; done     # interface (os *-integration exigem pnpm build)
pnpm exec tsc --noEmit                       # tipos
cd motor && .venv/bin/python -m pytest -q    # motor
cd servico-python && ../motor/.venv/bin/python -m pytest -q
```

## Documentação

- [docs/SISTEMA.md](docs/SISTEMA.md) — **comece por aqui**: o que o ORBIS faz, etapa por etapa, o papel da IA e onde ficam os dados
- [docs/ARQUITETURA.md](docs/ARQUITETURA.md) — para quem mexe no código: componentes, dados, fluxos, segurança, testes, como estender
- [COMO_RODAR.md](COMO_RODAR.md) — instalação, IA local, Configurações, variáveis, testes, solução de problemas
- [IMPLEMENTATION.md](IMPLEMENTATION.md) — limites conhecidos da interface
- [motor/README.md](motor/README.md) — o motor Python
- [motor/ANALISE.md](motor/ANALISE.md) — por que a recuperação de PDFs fica em ~50% e o que a leva adiante (acesso CAPES/CAFe)
- [docs/hospedagem-sites.md](docs/hospedagem-sites.md) — ciclo de vida na hospedagem Sites
- [docs/planos/](docs/planos/) — desenhos e planos de implementação (registro histórico)

Chaves de API ficam no banco local (tela Configurações), em `motor/.env` ou no
ambiente do Worker; nenhuma entra no repositório.
