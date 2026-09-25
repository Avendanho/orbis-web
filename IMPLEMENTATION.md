# ORBIS Web — piloto privado

Implementado: projetos isolados por proprietário, protocolo versionado e aprovação humana, busca DOI persistida (500 por lote), incorporação explícita, PDFs R2 privados (30 MB/arquivo, 2 GB/projeto), tentativas/motivos, triagem positiva, pareceres identificados, adjudicação, revisão final, contagens determinísticas, backup .orbis com extensão web, restauração em projeto novo, histórico, exclusão confirmada e apoio de IA por intercâmbio manual.

Validações: TypeScript e build; testes de regras/backup/corrupção; Worker compilado exercitado no Miniflare com D1 e R2 temporários, autenticação simulada nos cabeçalhos de teste, isolamento entre duas identidades, conflitos de versão, importação retomável, arquivos, lote de 500 registros e fluxo metodológico. A identidade da produção é fornecida pelo dispatcher Sites. Apenas a tela de entrada foi conferida no navegador de prévia: autenticação hospedada não é simulada nesse ambiente.

Limites deste piloto:
- Conta única por projeto. Pareceres são transcritos pelo proprietário com nomes declarados; não equivalem a revisores autenticados ou revisão cega. Compartilhamento e contas colaborativas ainda não estão implementados.
- Lotes retomáveis persistem no banco, mas o navegador solicita cada tarefa; fechar a aba interrompe o avanço depois das requisições já enviadas.
- Um PDF armazenado por artigo; validar assinatura não garante correspondência científica. O usuário deve conferir a identidade. Não há antivírus, OCR ou extração de texto integral na web nesta versão.
- Até 2.000 registros por projeto e 1,8 milhão de caracteres no estado estruturado; corpos maiores são recusados com preservação dos dados. PDFs não integram esse estado.
- Backup V1 sem compressão, verificação CRC e hash legado; arquivos recebem SHA-256 ao armazenar. Extensão orbis_web preserva o estado web. Legado conserva decisões/histórico originais e apresenta PCC como rascunho, sem aprovações automáticas. Retorno ao HTML local não interpreta todos os campos novos.
- Exportação de grandes backups depende da memória disponível no navegador; não foi ensaiado um pacote de 2 GB.
- PRISMA inicia no corpus incorporado. Não há inventário de registros descartados antes da incorporação nem contagem metodológica de duplicatas externas. Não chamar isso de PRISMA publicado completo sem conferir essas entradas.
- IA conectada não configurada; exportar/importar sugestões manualmente não transmite documentos automaticamente nem altera decisões.
- Exclusão é definitiva com nome digitado; não há lixeira automática ou retenção de 30 dias.
- DNS público e checagem de cada redirecionamento reduzem SSRF; DNS nativo e HTTP não oferecem pinning de endereço nesta implementação.
- Reservas de quota para uploads interrompidos abruptamente podem precisar de reconciliação administrativa. Não alegar armazenamento distribuído transacional entre D1 e R2.

Nenhum corpus embutido ou projeto real do usuário foi migrado. Dados de testes ficam apenas no banco temporário dos testes e não compõem o artefato de publicação.

Teste de fontes externas no Worker local em 22/09: inconclusivo por falha de DNS do ambiente; o sistema retornou busca incompleta e bloqueou incorporação sem metadados. Não contabilizar esse ensaio como download real aprovado.

## Atualização — análises de IA integradas
- Triagem e PCC aceitam ORBIS_AI_RESULTS_V1 e ORBIS_CHATGPT_RESULTS_V1 do sistema original, por arquivo ou texto JSON. As análises são persistidas por artigo, fornecedor/modelo e execução; não substituem decisões humanas.
- Coluna IA, filtro de concordância/divergência/contexto, ficha por critério, evidências, indicação de duplicatas, remoção por artigo/execução/fornecedor, associação manual de pendências e relatório de importação.
- Exportação de pedido estruturado e, para PCC, ZIP com PDFs disponíveis. Relatórios HTML imprimíveis/PDF pelo navegador, DOCX e CSV. Backup preserva análises web e as representa também no campo analisesIA do formato local.
- Critérios ou fonte alterados tornam a análise histórica. Resultados antigos sem contexto verificável continuam visíveis, fora da concordância atual. Correspondências aproximadas não são feitas silenciosamente; o usuário associa os pendentes.
- Não há inferência ou chamada de IA automática: o intercâmbio permanece manual. Propostas de planejamento em texto continuam distintas de resultados de avaliação por artigo. O projeto ainda usa limite de 1,8 MB de estado; importações que excedam esse limite são recusadas atomicamente.
- Testes cobrem importação, contexto, divergência, associação, exclusões preservando decisões humanas, persistência D1 e restauração com análises. Não se reivindica validação visual autenticada do site publicado.

## Correções — PCC, triagem e DOI
- A leitura de resultados de IA aceita respostas PCC com criterios estruturados ou marcações simples, blocos JSON com texto em volta, envoltórios de resultados e lotes de até 30 avaliações por envio para a API. O relatório mostra itens não associados e permite associação explícita. Contexto ausente permanece histórico e é sinalizado.
- DOI consultado não entra no corpus sem PDF validado: a ação de incorporação baixa o PDF, confere a assinatura, reserva a cota, armazena o arquivo e só então confirma o registro. Erros da fonte são exibidos em cada DOI. Resultados anteriores sem PDF continuam disponíveis para reconsulta e não são removidos automaticamente.
- O planejamento original foi adaptado em duas fases: objetivo/pergunta/PCC/inclusão/exclusão; perguntas positivas de triagem com origem, orientações e exemplos. Há histórico, propostas da IA por pacote JSON, aprovação item a item e laboratório de até 8 artigos, sem modificar decisões humanas. As orientações aprovadas aparecem na avaliação dos artigos. O intercâmbio de IA é manual, sem chamadas automáticas.
- Testes sintéticos cobriram recusa de incorporação sem PDF e ausência de registro no corpus, importação PCC original e aprovação persistida das duas fases. A obtenção externa completa e a interface autenticada publicada ainda dependem de conferência com um arquivo e um DOI reais na conta do usuário.
