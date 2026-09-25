# Plano V3 — Integridade da triagem por IA

Branch: `testes`

## Diagnóstico

A etapa de download tem 13 arquivos de teste. A etapa que decide **INCLUÍDO /
EXCLUÍDO / REVISÃO MANUAL** tem zero. É a de maior consequência: um erro ali
não derruba nada, ele entra silenciosamente no resultado da revisão
sistemática.

Cinco defeitos encontrados na leitura do código, todos verificados:

### 1. O sinal de falha de extração é calculado e descartado

`pdf_processor.py` grava `text_quality` (`HIGH` / `LOW` / `ERROR`) no
`metadata.json`. `grep -rn text_quality src/analysis` mostra duas escritas e
**nenhuma leitura**.

Quando a extração falha ou estoura o timeout, `content.md` recebe o texto do
erro — `"Erro: Timeout ao processar o PDF."`, `"Não foi possível extrair o
texto do PDF."`. Em `main.analyze`, o teste é `if text_content.strip():`, que é
verdadeiro para essas strings. O LLM recebe a mensagem de erro no lugar do
artigo e devolve um parecer.

Resultado: **um veredito de triagem sobre um artigo que nunca foi lido.**

### 2. Falha de API é registrada como decisão

```python
except Exception as e:
    decision = "REVISÃO MANUAL"
    justification = f"Erro na API do LLM: {str(e)}"
```

Não há retry com espera. Com 10 workers contra o Gemini, um 429 é esperado, e
cada um vira `REVISÃO MANUAL`. O PRISMA conta esses artigos como triados.

Uma chamada que falhou não é uma decisão de triagem. São coisas de natureza
diferente e precisam de registros diferentes.

### 3. Artigo que estoura exceção desaparece

```python
except Exception as e:
    console.print(f"[red]Error in process_article: {e}[/red]")
```

Sem gravação no banco. O artigo some do `resultados.csv`, do
`RELATORIO_FINAL.md` e das contagens PRISMA. Num fluxo cujo produto é uma
contagem auditável, perder um item em silêncio é o pior modo de falhar.

### 4. Perguntas fixas em 15

`questions = [f"Q{i}" for i in range(1, 16)]`. Um protocolo com Q16 perde a
pergunta sem aviso.

### 5. `confidence` com dois tipos

`conf` é `int` (0–100) no caminho normal e a string `"BAIXO"` quando não há
texto. A coluna é TEXT e o relatório escreve `{confidence_score}%`, imprimindo
`BAIXO%`.

## O que vai ser feito

Princípio único: **falha técnica e decisão científica são registros
diferentes.** O schema já tem as duas colunas (`status` e `decision`); hoje
`status` é sempre `COMPLETED`.

| Mudança | Onde |
|---|---|
| Módulo novo, sem I/O, com a lógica de triagem isolada e testável | `src/analysis/screening.py` |
| Portão de qualidade: texto não-triável nunca vai ao LLM | `screening.py` + `main.py` |
| Retry com espera exponencial e jitter para o LLM | `src/analysis/llm_retry.py` |
| Falha técnica grava `status='FAILED'` e `decision=NULL` | `main.py`, `database.py` |
| Relatório e PRISMA contam só `status='COMPLETED'`; falhas aparecem em seção própria | `main.py` |
| Perguntas derivadas do protocolo | `screening.py` |
| `confidence` normalizado para `int` ou `None` | `screening.py` |
| Procedência por veredito (provider, modelo, hash do protocolo) | `database.py`, `main.py` |
| Bateria de testes da etapa de análise | `src/analysis/tests/` |

## Fora de escopo, de propósito

- **Refatorar `fetch.py` (7.566 linhas).** É o maior problema estrutural do
  repositório e merece uma leva só dele, com os testes de recuperação verdes a
  cada passo. Misturar isso com mudanças de integridade da triagem tornaria
  as duas coisas difíceis de revisar.
- **Imports implícitos** (`from config import settings`). Funcionam pelo
  lançador atual; trocar por pacote real mexe em `backend.py` e `start.py`.

## Verificação

Cada defeito ganha um teste que falha antes da correção. Nenhuma alteração
entra sem a suíte inteira verde (download, busca e análise).
