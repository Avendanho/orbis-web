# ORBIS Web — o que está implementado e seus limites

O funcionamento completo está em [docs/SISTEMA.md](docs/SISTEMA.md) (para quem
usa) e [docs/ARQUITETURA.md](docs/ARQUITETURA.md) (para quem mexe no código).
Este arquivo guarda o resumo do que existe e, principalmente, os limites que
continuam valendo.

## Implementado

- Projetos com equipe (coordenador, colaborador, revisor, somente leitura),
  protocolo versionado com aprovação humana e histórico de cada alteração.
- Identificação por DOI (500 por lote, retomável) ou busca em PubMed, LILACS,
  Cochrane e Embase; metadados e resumo de fontes públicas.
- Triagem de títulos e resumos **antes do download**, com sugestões de IA que
  só viram decisão quando aceitas; só os incluídos são baixados.
- Obtenção do texto completo pelo motor (cascata de fontes com validação de
  identidade pelo conteúdo do PDF, texto em Markdown, imagens na pasta local)
  ou pelo próprio Worker (PDF no R2).
- PDFs importados do computador, triagem no corpus, análise PCC com dois
  pareceres e adjudicação, revisão geral, PRISMA desde a identificação,
  relatórios HTML/Word/CSV.
- IA de nuvem (Anthropic, OpenAI, Gemini) ou local (Ollama), em lotes com
  progresso e pausa; intercâmbio manual por pacote JSON continua disponível.
- Tela Configurações (bases, IA, lote, extração, fontes do motor) com teste de
  cada credencial.
- Backup `.orbis` completo (inclui lote, triagem de títulos e resumos e textos
  extraídos) e restauração em projeto novo.

## Limites conhecidos

- **Tamanho.** Até 2.000 artigos no corpus e 1,8 milhão de caracteres no estado
  estruturado de um projeto; corpos maiores são recusados com os dados
  preservados. PDFs, textos, o lote de DOIs e a triagem de títulos e resumos
  ficam fora desse estado. 30 MB por PDF e 2 GB por projeto.
- **Lotes dependem da aba aberta.** O lote persiste no banco, mas é o navegador
  que pede cada consulta, cada download e cada lote de IA; fechar a aba
  interrompe o avanço (retomar continua de onde parou).
- **Identidade do PDF.** O motor confere a identidade pelo conteúdo; o download
  pelo Worker confere a assinatura e os metadados. Em ambos, confira a
  correspondência antes de avaliar. Não há antivírus nem OCR.
- **Recuperação.** Só metade de um acervo típico tem cópia aberta; o resto
  depende do acesso institucional configurado no `motor/.env`
  ([motor/ANALISE.md](motor/ANALISE.md)).
- **IA.** Resultado é rascunho; chamadas que falham ficam pendentes. Modelos
  locais pequenos erram mais — confira uma amostra antes de aceitar em bloco.
  A IA lê só o texto (não as imagens).
- **Backup.** Formato V1 sem compressão; a exportação de projetos grandes
  depende da memória do navegador (um pacote de 2 GB não foi ensaiado). O
  retorno ao HTML local do ORBIS original não interpreta todos os campos
  novos.
- **Exclusão** é definitiva, confirmada pelo nome digitado; não há lixeira.
- **Rede.** O download pelo Worker checa por DNS que o destino é público, mas
  não fixa o endereço entre a checagem e a conexão.
- **Cota.** Reservas de cota de envios interrompidos abruptamente podem
  precisar de reconciliação manual; D1 e R2 não são uma transação só.
- **Configurações** valem para a instalação inteira, não por pessoa. As chaves
  ficam em texto no disco local (banco e `motor/.env`), como no `.env`; a
  proteção é nunca devolvê-las ao navegador.
