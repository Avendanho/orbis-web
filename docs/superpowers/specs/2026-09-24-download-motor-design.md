# Download de artigos pelo motor — desenho

Data: 2026-09-24
Situação: aprovado em conversa, aguardando revisão desta spec

## Objetivo

Trazer para o ORBIS a recuperação de PDFs do `/projeto` (cadeia completa de
fontes do `motor/src/download/fetch.py`) e deixar o pesquisador escolher, por
projeto, entre dois modos:

| Modo | PDF | Texto | Onde o PDF fica |
|---|---|---|---|
| **Analisar e baixar todos** | baixado, validado | extraído para a análise | pasta local `ORBIS_DATA_DIR/pdfs/<projeto>/` + `Relatório.txt` |
| **Só analisar no sistema** | baixado, validado, **descartado** | extraído para a análise | em lugar nenhum (pasta temporária apagada) |

Nos dois modos o PDF **não** é gravado no R2.

## Decisões do pesquisador

- Modo "só analisar": o PDF é usado e descartado; nada fica no computador nem
  na cota do projeto (o texto extraído, sim — ver Parte 3).
- Modo "baixar": PDFs vão para uma pasta no computador, como no `/projeto`.
- Abordagem: o navegador comanda o lote, o motor baixa um artigo por chamada
  (padrão `runBatch` já existente).

## Premissas

- Os dois modos só aparecem com o motor no ar (`/saude` informa
  `download_completo`). Sem motor, o botão atual (Worker → R2) segue igual —
  é o que funciona no site publicado.
- A regra "nada entra no corpus sem PDF" passa a ser "nada entra sem PDF
  **validado**" (identidade conferida), e não mais "sem PDF guardado no R2".

## Parte 1 — Motor (`servico-python/main.py`)

### `POST /baixar`

Entrada:

```json
{"doi":"10.x/y","titulo":"","autor":"","ano":"","periodico":"",
 "projeto":"<id>","modo":"baixar|analisar","prazo":90}
```

Saída (200):

```json
{"ok":true,"fonte":"unpaywall","fontes_tentadas":["..."],
 "arquivo":"Autor_2021_JACS_Titulo.pdf",
 "identidade":{"ok":true,"metodo":"...","score":0.97,"detalhe":"..."},
 "texto":"...","paginas":12,"chars":48210,"texto_truncado":false}
```

`arquivo` só no modo `baixar`. `ok:false` + `erro` quando nenhuma fonte
entregou PDF ou a identidade reprovou (HTTP 200: é resultado, não falha do
serviço). Falhas do próprio serviço usam HTTP de erro (507 disco, 503 módulo
ausente, 400 entrada inválida).

Passos:

1. Validar `projeto` (só `[A-Za-z0-9-]`, evita escapar da pasta) e `modo`.
2. `fetch.set_item_deadline(prazo)`; chamar `fetch.fetch(doi, pasta,
   dry_run=False, overwrite=False, timeout=...)`; limpar o prazo no `finally`.
3. Pasta: modo `baixar` → `ORBIS_DATA_DIR/pdfs/<projeto>/`; modo `analisar` →
   `tempfile.TemporaryDirectory()` apagado ao fim, mesmo com erro.
4. Identidade: `identity.extract_pdf_identity(bytes)` +
   `validate_article_identity(...)`. Reprovada → apagar o PDF **nos dois modos**
   e devolver `ok:false` com o motivo.
5. Texto: PyMuPDF, limite de 200 000 caracteres (`texto_truncado`).
6. Modo `baixar`: acrescentar linha ao `pdfs/<projeto>/Relatório.txt`
   (data, DOI, situação, fonte, arquivo) sob um `threading.Lock`.
7. Concorrência: `threading.BoundedSemaphore(4)` em volta da chamada.

### `/saude`

Acrescenta o recurso `download_completo` quando `fetch` e PyMuPDF importam, e o
campo `pasta_pdfs` com o caminho absoluto de `ORBIS_DATA_DIR/pdfs`.

## Parte 2 — Fluxo no ORBIS

### Escolha do modo

Na etapa de busca (`app/workspace.tsx`), ao lado de "Baixar e incorporar PDFs
encontrados", um seletor aparece **só com o motor no ar**:

- **Analisar e baixar todos os PDFs** — "PDFs salvos em `<pasta_pdfs>/<projeto>`"
- **Só analisar no sistema** — "o PDF é lido e descartado; nada é salvo no seu computador"

O modo fica em `state.settings.downloadMode` (`'baixar'|'analisar'`, padrão
`'baixar'`), gravado por `PATCH action:'settings'`.

### Fase 1 — buscar (paralelo, 4 por vez)

- Nova rota `POST /api/projects/[id]/motor` `{doi, modo}`; papéis `owner`/`editor`.
- Lê metadados da linha em `search_items`, chama o motor via nova função
  `downloadViaEngine` em `lib/python-bridge.ts` (timeout = prazo + margem).
- Sucesso: grava o texto no R2 em `<projeto>/texto/<hash-do-doi>.txt`
  (reservando cota como o PDF faz hoje) e guarda em `search_items.result` o
  campo `motor = {ok, modo, fonte, arquivo, identidade, texto:{key,chars,paginas,truncado}}`.
- Falha: `search_items.error` com o motivo; nada no R2.
- Motor fora do ar (bridge devolve `null`) → HTTP 503 específico; o cliente
  pausa o lote.
- O loop do cliente reaproveita `runBatch` (pausar/retomar), com 4 trabalhadores.
- Entram na fila todos os DOIs com metadados encontrados (`result.found`), não
  só os que têm `pdfUrls` do Worker.

### Fase 2 — incorporar (sequencial)

- `incorporateWithPdf` ganha o caminho "motor": se `result.motor.ok`, cria o
  artigo sem baixar de novo e sem gravar PDF no R2. O artigo recebe:
  - `identity` com `method:'conteudo_pdf'` e o veredito do motor;
  - `source = {modo, fonte, arquivoLocal?}`;
  - `texto = {key, chars, paginas, truncado, origem:'motor'}`.
- `pdf_attempts` registra `saved` com "PDF validado pelo motor (modo …)".

### Cartão do artigo (`app/corpus-article-card.tsx`)

- modo baixar: "PDF na pasta local: `<arquivo>`"
- modo analisar: "PDF lido e descartado; texto disponível para a análise"
- sem texto: "sem texto extraído — a PCC automática responderá null"

## Parte 3 — Texto na análise

- **Triagem**: inalterada (título e resumo, como manda o protocolo).
- **PCC automática** (`lib/ai-runner.ts`): `runAITriage` recebe um leitor de
  texto opcional; para itens com `a.texto`, acrescenta `texto_completo`
  (até 60 000 caracteres) e `texto_truncado`. Lote PCC cai de 5 para 2.
  A instrução da PCC em `makeAIPackage` passa a dizer: use `texto_completo`
  quando presente; sem ele, `null`.
- **PCC manual** (ZIP em `app/ai-controls.tsx`): artigo com `a.texto` e sem PDF
  no R2 entra como `textos/<arquivo>.txt` e deixa de contar como ausente. O
  download passa pela rota nova `GET /api/projects/[id]/texto?article=`.
- **Backup `.orbis`** (`lib/backup.ts`): inclui os `.txt` e os restaura.
- IA continua gerando só rascunho via `importAI`; falha técnica não vira parecer.

## Parte 4 — Erros

| Situação | Resultado |
|---|---|
| Nenhuma fonte entregou PDF | "não localizado" + `fontes_tentadas`; fora do corpus; reconsultável |
| PDF de outro artigo | recusado e apagado (inclusive da pasta local); motivo da identidade |
| PDF sem texto extraível | entra no corpus (identidade válida) com aviso "sem texto" |
| Prazo de 90 s | "pendente — tempo esgotado"; retomável |
| Motor cai no meio | lote pausa sozinho; concluídos preservados |
| Pasta sem permissão / disco cheio | 507; lote pausa |
| Cota de 2 GB ao gravar `.txt` | mensagem atual de cota; PDF local mantido |

## Testes

- `servico-python/tests/test_baixar.py` (pytest, `fetch` e rede simulados):
  modo baixar grava PDF + linha no relatório; modo analisar não deixa arquivo
  (nem com erro); identidade reprovada apaga nos dois modos; prazo repassado;
  `projeto` inválido recusado.
- `tests/motor-download.mjs`: incorporação motor não grava PDF no R2 e grava
  `.txt`; DOI sem sucesso do motor não entra; PCC inclui `texto_completo`,
  trunca em 60 000 e marca `texto_truncado`; triagem não recebe texto; ZIP
  manual inclui `.txt`.
- `tests/worker-integration.mjs`: lote de 2 DOIs contra motor falso em porta
  local, nos dois modos.
- Verificação real: 3 DOIs verdadeiros via `start.py` (pasta, corpus, PCC
  automática). Resultado relatado como ocorrer.

## Fora do escopo

- Upload dos PDFs locais para o R2.
- Busca por título sem DOI (`fetch_title_direct`).
- OCR de PDFs escaneados.
- Mudar o paralelismo para o `run_parallel.py`.
