import typer
import glob
import os
import sqlite3
import re
from typing import Optional
from pathlib import Path
from rich.console import Console
from config import settings
from database import init_db
from pdf_processor import process_pdf
from rich.progress import track
import llm_retry
import screening

# Caminho do protocolo num só lugar, para que os testes possam apontá-lo
# para um arquivo temporário em vez de sobrescrever o protocolo real.
PROTOCOL_PATH = Path(__file__).parent / "protocolo_triagem.txt"

app = typer.Typer(help="AnaliseIA - Triagem Científica de Artigos")
console = Console()

@app.command()
def scan(workers: int = 5):
    """Localiza PDFs e gera inventário (paralelo, `workers` processos)"""
    import concurrent.futures
    from pdf_processor import scan_one

    console.print("[bold blue]Iniciando scan de PDFs...[/bold blue]")
    pdfs = glob.glob(os.path.join(settings.pdf_dir, "**", "*.pdf"), recursive=True)
    console.print(f"Total de PDFs encontrados: {len(pdfs)}")

    init_db()
    if not pdfs:
        console.print("[bold green]Scan concluído![/bold green]")
        return

    # Processos, não threads: o PyMuPDF não é thread-safe (extrações
    # simultâneas em threads podem corromper estado ou derrubar o Python), e
    # cada processo consegue aplicar o timeout por PDF via SIGALRM.
    done = 0
    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(scan_one, p): p for p in pdfs}
        for future in concurrent.futures.as_completed(futures):
            pdf_path = futures[future]
            try:
                _, err = future.result()
            except Exception as e:  # ex.: BrokenProcessPool se um worker morrer
                err = f"{type(e).__name__}: {e}"
            if err:
                console.print(f"[yellow]⚠️ Erro ao processar {Path(pdf_path).name}: {err}[/yellow]")
            done += 1
            console.print(
                f"[dim]  [{done}/{len(pdfs)}] {Path(pdf_path).name}[/dim]",
                highlight=False,
            )

    console.print("[bold green]Scan concluído![/bold green]")



@app.command()
def analyze(workers: int = 10):
    """Executa a análise com modelo LLM usando múltiplas threads"""
    init_db()
    
    meta_dir = Path(settings.db_dir) / "extracted"
    if not meta_dir.exists():
        console.print("[red]Execute 'scan' primeiro.[/red]")
        raise typer.Exit()
        
    articles = list(meta_dir.glob("*/metadata.json"))
    console.print(f"[bold blue]Analisando {len(articles)} artigos usando {workers} threads...[/bold blue]")
    
    import json
    import hashlib
    import concurrent.futures
    from database import save_analysis, save_failure

    from llm_client import get_llm_client

    protocol_path = PROTOCOL_PATH
    protocolo_texto = protocol_path.read_text(encoding="utf-8") if protocol_path.exists() else "Você é um assistente de triagem."
    # Guardado com cada parecer: numa revisão sistemática é preciso poder dizer
    # qual versão do protocolo produziu cada decisão.
    protocol_hash = hashlib.md5(protocolo_texto.encode("utf-8")).hexdigest()[:12]

    # Detecta protocolo vazio/curto: sem critérios, o modelo não tem base para
    # decidir. Nesse caso a triagem deve ser conservadora (preferir REVISÃO
    # MANUAL a EXCLUIDO). Um protocolo real preenchido tem centenas de chars.
    MIN_PROTOCOL_CHARS = 400
    protocol_ok = len(protocolo_texto.strip()) >= MIN_PROTOCOL_CHARS
    if not protocol_ok:
        console.print(
            "[bold yellow]⚠️ ATENÇÃO: protocolo_triagem.txt está vazio ou muito curto "
            f"({len(protocolo_texto.strip())} caracteres).[/bold yellow]"
        )
        console.print(
            "[yellow]   Sem critérios definidos, nenhum artigo será EXCLUÍDO: "
            "todos irão para REVISÃO MANUAL. Preencha os critérios do protocolo.[/yellow]"
        )

    try:
        analyze_article, provider_name = get_llm_client()
        console.print(f"[bold green]🤖 Usando: {provider_name}[/bold green]")
    except Exception as e:
        console.print(f"[bold red]Erro ao inicializar provedor de IA: {e}[/bold red]")
        raise typer.Exit(1)
        
    def _avisar_falhas():
        """Diz quantos artigos não puderam ser triados, e por quê.

        Sem isto a execução termina com uma mensagem de sucesso mesmo quando
        metade do lote falhou, e a falha só apareceria ao ler o relatório.
        """
        from database import get_db
        conn = get_db()
        try:
            linhas = conn.execute(
                "SELECT failure_reason, count(*) c FROM articles "
                "WHERE status = 'FAILED' GROUP BY failure_reason ORDER BY c DESC"
            ).fetchall()
        except sqlite3.Error:
            return
        finally:
            conn.close()
        total = sum(r["c"] for r in linhas)
        if not total:
            return
        console.print(
            f"\n[bold yellow]⚠️ {total} artigo(s) não puderam ser triados "
            "(falha técnica, não decisão):[/bold yellow]"
        )
        legenda = {
            "extracao_falhou": "o texto do PDF não pôde ser extraído",
            "sem_texto": "o PDF não produziu texto algum",
            "texto_insuficiente": "texto curto demais para sustentar um parecer",
            "llm_indisponivel": "o provedor de IA não respondeu após as tentativas",
            "erro_inesperado": "erro não previsto durante a análise",
        }
        for r in linhas:
            motivo = r["failure_reason"] or "desconhecido"
            console.print(f"[yellow]   {r['c']:>4}  {legenda.get(motivo, motivo)}[/yellow]")
        console.print(
            "[dim]   Eles não entram nas contagens de triagem. "
            "Corrija a causa e rode 'analyze' de novo para reprocessá-los.[/dim]"
        )

    def process_article(meta_file):
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
                
            article_id = meta["article_id"]
            content_path = meta_file.parent / "content.md"
            
            text_content = ""
            if content_path.exists():
                with open(content_path, "r", encoding="utf-8") as f:
                    text_content = f.read()
            
            import hashlib
            task_hash = hashlib.md5(f"{meta.get('hash', '')}{protocolo_texto}".encode('utf-8')).hexdigest()
            
            from database import get_article
            cached = get_article(article_id)
            if cached and cached.get('hash') == task_hash and cached.get('status') == 'COMPLETED':
                try:
                    c_json = json.loads(cached.get('analysis_json', '{}'))
                    raw = c_json.get("raw_json", {})
                    if "confidence_score" in raw:
                        return  # Ignora artigo já processado com o mesmo texto e protocolo
                except Exception:
                    pass
            
            # Portão de qualidade: um PDF cuja extração falhou grava em
            # content.md a própria mensagem de erro. Mandar isso ao modelo
            # produziria um parecer sobre um artigo que ninguém leu, e esse
            # parecer entraria na contagem como triagem legítima. Falha de
            # extração é falha técnica, não decisão.
            screenable, recusa = screening.is_screenable(text_content, meta)
            if not screenable:
                save_failure(article_id, meta["filename"], task_hash, recusa,
                             provider=provider_name, protocol_hash=protocol_hash)
                return

            decision = "REVISÃO MANUAL"
            ex_code = None
            conf = None
            justification = "Falta de dados"
            raw_json = {}

            images_dir = content_path.parent / "images"
            image_paths = []
            if images_dir.exists():
                import glob
                image_paths = glob.glob(str(images_dir / "*.*"))
            
            # Lógica de decisão sã: rígida mas justa. Só EXCLUIR quando um
            # critério explícito do protocolo falhar; na dúvida, sem
            # evidência, ou com protocolo vazio → REVISÃO MANUAL.
            regras = (
                "REGRAS DE TRIAGEM (rígidas, porém JUSTAS):\n"
                "1. Aplique os critérios do protocolo com rigor.\n"
                "2. Só classifique como 'EXCLUIDO' quando um critério de EXCLUSÃO "
                "explícito do protocolo falhar de fato, citando o código do critério.\n"
                "3. Na dúvida, sem evidência suficiente, ou se o protocolo não define "
                "critérios claros, classifique como 'REVISÃO MANUAL'. NUNCA exclua "
                "por ausência de critérios ou por falta de informação.\n"
            )
            if not protocol_ok:
                regras += (
                    "4. O protocolo está vazio/incompleto: NÃO exclua nenhum artigo. "
                    "Use 'REVISÃO MANUAL' e explique que faltam critérios.\n"
                )
            user_prompt = (
                f"Texto do Artigo:\n\n{text_content}\n\n{regras}\n"
                "IMPORTANTE: Responda obrigatoriamente com um único objeto JSON válido "
                "(sem cercas de código), incluindo as chaves: 'parecer_final' "
                "('INCLUIDO', 'EXCLUIDO' ou 'REVISÃO MANUAL'), 'justificativa', "
                "'motivo_principal', 'confidence_score' (número de 0 a 100), "
                "'key_synthesis', 'project_value_added', 'extracted_snippets' e Q1..Qn."
            )

            # Uma chamada que caiu não é um parecer. `call_with_retry` insiste
            # no que pode melhorar (429, 5xx, queda de conexão) e desiste na
            # hora do que não melhora (chave inválida, prompt grande demais).
            # Quando desiste, levanta LlmCallFailed, que sobe até o `except`
            # de process_article e é gravado como FALHA — nunca como decisão.
            def _pedir_parecer():
                texto = analyze_article(protocolo_texto, user_prompt, image_paths)
                return screening.parse_llm_json(texto)

            try:
                result_json = llm_retry.call_with_retry(_pedir_parecer)
            except llm_retry.LlmCallFailed:
                raise
            except ValueError as exc:
                # JSON ilegível nas duas tentativas: é falha técnica também.
                raise llm_retry.LlmCallFailed(
                    f"Resposta do LLM não pôde ser interpretada: {exc}", attempts=1
                ) from exc

            raw_json = result_json
            decision = screening.decide(result_json, protocol_ok=protocol_ok)
            justification = result_json.get("justificativa", str(result_json))
            ex_code = result_json.get("motivo_principal", "-")
            if ex_code == "-":
                ex_code = None
            conf = screening.normalize_confidence(
                result_json.get("confidence_score", result_json.get("seguranca"))
            )

            final_analysis = {
                "decision": decision,
                "exclusion_code": ex_code,
                "confidence": conf,
                "justificativa": justification,
                "raw_json": raw_json
            }
            
            save_analysis(article_id, meta["filename"], task_hash, final_analysis,
                          provider=provider_name, protocol_hash=protocol_hash)

        except Exception as e:
            # Nada some. Antes esta linha só imprimia, e o artigo desaparecia do
            # CSV, do relatório e das contagens PRISMA — a soma continuava
            # parecendo certa com um item a menos. Agora fica gravado como
            # falha, visível e sem decisão associada.
            motivo = "llm_indisponivel" if isinstance(e, llm_retry.LlmCallFailed) else "erro_inesperado"
            console.print(f"[red]Falha em {meta_file.parent.name}: {e}[/red]")
            try:
                save_failure(
                    locals().get("article_id") or meta_file.parent.name,
                    locals().get("meta", {}).get("filename") or meta_file.parent.name,
                    locals().get("task_hash") or "",
                    motivo, detail=str(e),
                    provider=provider_name, protocol_hash=protocol_hash,
                )
            except Exception as db_exc:
                console.print(f"[red]  (não foi possível registrar a falha: {db_exc})[/red]")

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        list(track(executor.map(process_article, articles), total=len(articles), description="Analisando com IA..."))

    _avisar_falhas()
    console.print("[bold green]Análise concluída e dados salvos no banco![/bold green]")

@app.command()
def status():
    """Mostra o status do banco"""
    from database import get_db
    init_db()
    # Mesmo banco usado por scan/analyze/report (settings.db_dir/analysis.db);
    # antes apontava para <output_dir>/data/analysis.db, criando um banco vazio.
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("SELECT decision, count(*) as c FROM articles GROUP BY decision")
        rows = c.fetchall()
        if not rows:
            console.print("Banco vazio ou não inicializado.")
        for r in rows:
            console.print(f"{r[0]}: {r[1]}")
    except sqlite3.Error:
        console.print("Banco vazio ou não inicializado.")
    finally:
        conn.close()

@app.command()
def report():
    """Gera os relatórios (CSV, JSON, MD, e JSONs por pergunta)"""
    console.print("[bold green]Gerando relatórios detalhados...[/bold green]")
    reports_dir = Path(settings.output_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    
    import json
    import pandas as pd
    from database import get_db
    
    conn = get_db()
    todos = pd.read_sql_query("SELECT * FROM articles", conn)
    conn.close()

    if todos.empty:
        console.print("[yellow]Nenhum dado no banco para gerar relatório.[/yellow]")
        return

    # Artigos triados e artigos que falharam são populações distintas. Só os
    # primeiros entram nas contagens; os segundos entram numa seção própria,
    # para que uma falha técnica nunca seja lida como decisão de triagem.
    if "status" in todos.columns:
        df = todos[todos["status"] == "COMPLETED"].copy()
        falhas = todos[todos["status"] == "FAILED"].copy()
    else:
        df, falhas = todos.copy(), todos.iloc[0:0].copy()

    if df.empty:
        console.print(
            f"[yellow]Nenhum artigo foi triado com sucesso "
            f"({len(falhas)} falha(s) técnica(s)). Verifique o protocolo e as chaves de IA.[/yellow]"
        )

    def parse_json(json_str):
        try:
            return json.loads(json_str)
        except:
            return {}

    df['parsed'] = df['analysis_json'].apply(parse_json)
    df['justificativa'] = df['parsed'].apply(lambda x: x.get("justificativa", "Sem justificativa."))
    # None quando o modelo não deu um número. Antes, um rótulo como "BAIXO"
    # atravessava até o relatório e saía impresso como "BAIXO%".
    df['confidence_score'] = df['parsed'].apply(
        lambda x: screening.normalize_confidence(x.get("confidence"))
    )
    df['key_synthesis'] = df['parsed'].apply(lambda x: x.get("raw_json", {}).get("key_synthesis", "N/A"))
    df['project_value_added'] = df['parsed'].apply(lambda x: x.get("raw_json", {}).get("project_value_added", "N/A"))
    
    # ---------------------------------------------------------
    # Atualizar PRISMA
    # ---------------------------------------------------------
    try:
        from prisma_manager import PrismaManager
        prisma = PrismaManager(settings.output_dir)
        
        total_screened = len(df)
        n_included = len(df[df['decision'] == 'INCLUIDO'])
        n_excluded = len(df[df['decision'] == 'EXCLUIDO'])
        
        n_sought = total_screened
        n_not_retrieved = 0
        if "identification" in prisma.state:
            n_found = prisma.state["identification"].get("n_found", 0)
            n_dups = prisma.state["identification"].get("n_duplicates", 0)
            expected = n_found - n_dups
            if expected > 0:
                n_sought = expected
                n_not_retrieved = max(0, expected - total_screened)
                
        prisma.update_screening(n_sought, 0) # Assumimos que todos os encontrados foram triados por IA (screening=fulltext no nosso caso)
        prisma.update_retrieval(n_sought, n_not_retrieved)
        
        # Calcular razões de exclusão
        exclusion_reasons = {}
        for _, row in df[df['decision'] == 'EXCLUIDO'].iterrows():
            code = row.get('exclusion_code')
            if not code or code == "-": code = "Não especificado"
            exclusion_reasons[code] = exclusion_reasons.get(code, 0) + 1
            
        prisma.update_included(n_included, n_excluded, exclusion_reasons)
        prisma.export(reports_dir)
        console.print("[bold green]✅ Arquivos PRISMA 2020 gerados.[/bold green]")
    except Exception as e:
        console.print(f"[yellow]Aviso: Falha ao gerar PRISMA: {e}[/yellow]")
        
    # Salvar resultados brutos
    df.drop(columns=['parsed']).to_csv(reports_dir / "resultados.csv", index=False)
    
    # ---------------------------------------------------------
    # Gerar JSONs Extrativos por Pergunta
    # ---------------------------------------------------------
    # Derivadas do protocolo. A lista fixa em Q15 descartava, sem aviso, toda
    # pergunta além da décima quinta.
    protocol_path = PROTOCOL_PATH
    protocolo_texto = protocol_path.read_text(encoding="utf-8") if protocol_path.exists() else ""
    questions = screening.protocol_questions(protocolo_texto)

    for q in questions:
        q_data = {}
        for _, row in df.iterrows():
            parsed = row['parsed']
            # As respostas Q1..Qn e os snippets vêm do output bruto do LLM,
            # armazenado sob 'raw_json'.
            raw = parsed.get("raw_json", {}) if isinstance(parsed, dict) else {}
            if not isinstance(raw, dict):
                raw = {}
            answer = raw.get(q, parsed.get(q, "N/A") if isinstance(parsed, dict) else "N/A")
            snippets_all = raw.get("extracted_snippets", {})
            snippets = snippets_all.get(q, []) if isinstance(snippets_all, dict) else []
            if answer != "N/A" and answer != "NAP":
                q_data[row['article_id']] = {
                    "answer": answer,
                    "snippets": snippets
                }
        
        # Só salvar se houver dados úteis para a pergunta
        if q_data:
            with open(reports_dir / f"relatorio_{q}.json", "w", encoding="utf-8") as f:
                json.dump(q_data, f, indent=2, ensure_ascii=False)
    
    # ---------------------------------------------------------
    # Generate RELATORIO_FINAL.md com Data Charts
    # ---------------------------------------------------------
    with open(reports_dir / "RELATORIO_FINAL.md", "w", encoding="utf-8") as f:
        f.write("# 📊 Relatório Detalhado de Triagem por IA\n\n")
        
        counts = df['decision'].value_counts() if not df.empty else {}
        f.write("## 📈 Resumo Estatístico\n\n")
        f.write(f"- ✅ **INCLUÍDOS:** {counts.get('INCLUIDO', 0)}\n")
        f.write(f"- ❌ **EXCLUÍDOS:** {counts.get('EXCLUIDO', 0)}\n")
        f.write(f"- ⚠️ **REVISÃO MANUAL:** {counts.get('REVISÃO MANUAL', 0)}\n")
        f.write(f"- 🚫 **NÃO TRIADOS (falha técnica):** {len(falhas)}\n\n")
        f.write(f"*Triados: {len(df)} de {len(df) + len(falhas)} artigos.*\n\n")
        f.write("---\n\n")

        if len(falhas):
            legenda = {
                "extracao_falhou": "o texto do PDF não pôde ser extraído",
                "sem_texto": "o PDF não produziu texto algum",
                "texto_insuficiente": "texto curto demais para sustentar um parecer",
                "llm_indisponivel": "o provedor de IA não respondeu após as tentativas",
                "erro_inesperado": "erro não previsto durante a análise",
            }
            f.write("## 🚫 Artigos não triados\n\n")
            f.write(
                "Estes artigos **não** foram incluídos, excluídos nem enviados para revisão "
                "manual: a análise não pôde ser concluída. Não entram em nenhuma contagem "
                "acima. Corrija a causa e rode `analyze` novamente para reprocessá-los.\n\n"
            )
            f.write("| Arquivo | Motivo |\n|---|---|\n")
            for _, row in falhas.iterrows():
                motivo = row.get("failure_reason") or "desconhecido"
                f.write(f"| `{row['filename']}` | {legenda.get(motivo, motivo)} |\n")
            f.write("\n---\n\n")

        f.write("## 📄 Análise Detalhada dos Artigos\n\n")
        
        for _, row in df.iterrows():
            decision = row['decision']
            icon = "✅" if decision == "INCLUIDO" else "❌" if decision == "EXCLUIDO" else "⚠️"
            f.write(f"### {icon} [{decision}] ID: {row['article_id']}\n\n")
            conf = row['confidence_score']
            conf_txt = f"{conf}%" if isinstance(conf, (int, float)) else "não informada"
            f.write(f"**Arquivo:** `{row['filename']}` | **Confiança da IA:** {conf_txt}\n\n")
            
            f.write(f"**Justificativa:**\n> {row['justificativa']}\n\n")
            
            if row['exclusion_code'] and row['exclusion_code'] != "-":
                f.write(f"- **Motivo Principal (Código):** {row['exclusion_code']}\n")
                
            if row['key_synthesis'] and row['key_synthesis'] != "N/A":
                f.write(f"- **Síntese:** {row['key_synthesis']}\n")
                
            if row['project_value_added'] and row['project_value_added'] != "N/A":
                f.write(f"- **Agregação ao Projeto:** {row['project_value_added']}\n")
                
            f.write("\n---\n\n")
            
    console.print(f"[bold green]Concluído! Relatórios gerados em: {reports_dir}[/bold green]")

if __name__ == "__main__":
    app()
