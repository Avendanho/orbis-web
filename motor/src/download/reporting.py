"""Relatórios de execução e o código de saída do processo.

Separado do motor de busca de propósito: aqui não se baixa nada nem se decide
nada sobre um artigo — só se conta o que aconteceu e se escreve de um jeito
que uma pessoa consiga auditar depois.

O código de saída distingue os motivos de fracasso, porque "não achei cópia
aberta" e "a rede caiu" pedem ações diferentes de quem roda o comando.
"""
from __future__ import annotations

import shlex
import sys
from pathlib import Path

EXIT_SUCCESS = 0
EXIT_UNRESOLVED = 1    # nenhuma cópia aberta encontrada
EXIT_AUTH = 2          # reservado
EXIT_VALIDATION = 3    # baixou, mas não era o artigo pedido
EXIT_TRANSPORT = 4     # a cópia existe, o transporte falhou


def _default_format() -> str:
    try:
        return "json" if not sys.stdout.isatty() else "text"
    except Exception:
        return "json"


def _decide_exit(results: list[dict]) -> int:
    """Pick the most descriptive exit code from per-item outcomes."""
    any_validation = False
    any_transport = False
    any_unresolved = False
    any_failure = False
    for r in results:
        if r.get("success"):
            continue
        any_failure = True
        err = r.get("error") or {}
        code = err.get("code", "")
        if code == "validation_error":
            any_validation = True
        elif code == "not_found":
            any_unresolved = True
        elif code.startswith("download_") or code == "resolve_network_error":
            any_transport = True
        else:
            any_unresolved = True
    if not any_failure:
        return EXIT_SUCCESS
    # Validation errors win: a malformed DOI is a caller bug that won't fix
    # itself on retry, so surface it even when the batch also has transient
    # network or not-found failures the caller might otherwise blindly retry.
    if any_validation:
        return EXIT_VALIDATION
    if any_transport:
        return EXIT_TRANSPORT
    return EXIT_UNRESOLVED


def _next_hints(results: list[dict], args) -> list[str]:
    """Suggest follow-up commands for the failed subset.

    Hints are intended for an agent or human to copy-paste and run, so all
    user-controlled values (DOIs, --out path) are shell-quoted to prevent
    a maliciously crafted DOI from injecting commands.
    """
    failed = [r["doi"] for r in results if not r.get("success")]
    if not failed:
        return []
    out = shlex.quote(args.out)
    if len(failed) == 1:
        cmd = f"paper-fetch {shlex.quote(failed[0])} --out {out}"
        if args.dry_run:
            cmd += " --dry-run"
        return [cmd]
    # Multiple failures — feed them via stdin so each DOI is delimited by a
    # real newline rather than interpolated into the shell command.
    payload = shlex.quote("\n".join(failed) + "\n")
    cmd = f"printf %s {payload} | paper-fetch --batch - --out {out}"
    if args.dry_run:
        cmd += " --dry-run"
    return [cmd]


def generate_detailed_report(results: list[dict], dois: list[str]) -> str:
    """Generate a detailed report of the DOI processing results."""

    # Initialize counters
    total = len(dois)
    successes = 0
    request_denials = 0  # transport errors
    not_found = 0        # unresolved errors
    identity_confirmed = 0   # success AND bibliographic identity confirmed
    identity_rejected = 0    # a structurally valid PDF was downloaded but did not match the requested article

    # Source usage tracking
    source_success_count = {}
    source_attempt_count = {}

    # Detailed results per DOI
    doi_details = []

    for i, (doi, result) in enumerate(zip(dois, results)):
        success = result.get("success", False)
        error = result.get("error", {})
        error_code = error.get("code", "") if error else ""
        source = result.get("source", "unknown")
        pdf_url = result.get("pdf_url", "none")

        # Track source attempts
        for attempted_source in set(result.get("sources_tried") or [source]) | {source}:
            source_attempt_count[attempted_source] = source_attempt_count.get(attempted_source, 0) + 1

        if success:
            successes += 1
            # A "success" that never carries identity_validated is a legacy
            # cache hit predating this migration, not evidence the identity
            # gate was skipped for a fresh download — every current success
            # path sets this explicitly (see fetch.py's identity gate).
            if result.get("identity_validated", True):
                identity_confirmed += 1
            status = "sucesso"
            status_detail = f"Fonte: {source} | Identidade: {result.get('validation_method', 'n/d')}"
            if source not in source_success_count:
                source_success_count[source] = 0
            source_success_count[source] += 1
        else:
            # Classify failure type
            if error_code == "article_identity_not_confirmed":
                identity_rejected += 1
                status = "identidade rejeitada"
                rejections = result.get("identity_rejections") or []
                detected = rejections[-1].get("detected_doi") if rejections else None
                status_detail = (
                    f"PDF baixado não corresponde ao DOI solicitado"
                    + (f" (DOI encontrado no PDF: {detected})" if detected else "")
                )
            elif error_code.startswith("download_") or error_code == "resolve_network_error":
                request_denials += 1

                status = "falha de download/resolução"
                status_detail = f"Erro: {error_code}"
            else:
                not_found += 1
                status = "não encontrado"
                status_detail = f"Erro: {error_code}" if error_code else "Nenhuma fonte encontrou o PDF"

        doi_details.append({
            "index": i + 1,
            "doi": doi,
            "status": status,
            "status_detail": status_detail,
            "source": source,
            "pdf_url": pdf_url if pdf_url != "none" else "Não obtido"
        })

    # Build the report
    report_lines = []
    report_lines.append("=" * 60)
    report_lines.append("RELATÓRIO DETALHADO DE PROCESSAMENTO DE DOIS")
    report_lines.append("=" * 60)
    report_lines.append("")

    # Summary statistics
    report_lines.append("RESUMO GERAL:")
    report_lines.append(f"  Total de DOIs processados: {total}")
    report_lines.append(f"  Downloads concluídos (PDF válido + identidade confirmada): {successes}")
    report_lines.append(f"  Identidade rejeitada (PDF baixado, mas não é o artigo solicitado): {identity_rejected}")
    report_lines.append(f"  Requisições negadas (erros de transporte): {request_denials}")
    report_lines.append(f"  Não encontrados (sem cópia OA disponível): {not_found}")
    report_lines.append("")

    # Success rate — the headline metric is identity-confirmed downloads
    # over DOIs processed, NOT raw "a PDF was written to disk" over
    # processed, per the project's absolute rule that a valid-but-wrong PDF
    # is never a success.
    if total > 0:
        success_rate = (successes / total) * 100
        identity_rate = (identity_confirmed / total) * 100
        report_lines.append(f"Taxa de sucesso (downloads): {success_rate:.1f}%")
        report_lines.append(f"Taxa de identidade confirmada (métrica principal de qualidade): {identity_rate:.1f}%")
        report_lines.append("")

    # Source utilization
    report_lines.append("UTILIZAÇÃO DAS FONTES:")
    all_sources = set()
    for d in source_attempt_count.keys():
        if d is not None:
            all_sources.add(d)
    for d in source_success_count.keys():
        if d is not None:
            all_sources.add(d)
    for source in sorted(all_sources):
        attempted = source_attempt_count.get(source, 0)
        successful = source_success_count.get(source, 0)
        if attempted > 0:
            rate = (successful / attempted) * 100
            report_lines.append(f"  {source}: {successful}/{attempted} ({rate:.1f}%)")
        else:
            report_lines.append(f"  {source}: 0/0 (0.0%)")
    report_lines.append("")

    # Detailed DOI results
    report_lines.append("DETALHAMENTO POR DOI:")
    report_lines.append("-" * 60)
    for detail in doi_details:
        report_lines.append(f"{detail['index']:3d}. DOI: {detail['doi']}")
        report_lines.append(f"     Status: {detail['status']}")
        report_lines.append(f"     Detalhe: {detail['status_detail']}")
        report_lines.append(f"     Fonte: {detail['source']}")
        report_lines.append(f"     PDF URL: {detail['pdf_url']}")
        report_lines.append("")

    # Recommendations
    report_lines.append("RECOMENDAÇÕES:")
    report_lines.append("-" * 60)

    if identity_rejected > 0:
        report_lines.append(f"• {identity_rejected} DOIs tiveram um PDF baixado, porém rejeitado por não corresponder")
        report_lines.append("  bibliograficamente ao artigo solicitado (ver 'DOI encontrado no PDF' no detalhamento).")
        report_lines.append("  Isso NÃO é um download com sucesso — nenhum arquivo incorreto foi mantido.")

    if not_found > 0:
        report_lines.append(f"• {not_found} DOIs não tiveram cópia de acesso aberto encontrada.")
        report_lines.append("  Considere verificar acesso institucional ou usar serviços como")
        report_lines.append("  solicitação de cópia diretamente aos autores.")

    if request_denials > 0:
        report_lines.append(f"• {request_denials} DOIs falharam devido a erros de transporte.")
        report_lines.append("  Pode ser problemas temporários de rede ou bloqueio temporário.")
        report_lines.append("  Considere tentar novamente em momentos diferentes.")

    if successes > 0:
        report_lines.append(f"• {successes} DOIs foram baixados com sucesso.")
        report_lines.append("  Os PDFs estão disponíveis no diretório 'pdfs'.")

    if successes == 0 and request_denials == 0 and not_found > 0:
        report_lines.append("• Nenhum DOI foi baixado com sucesso.")
        report_lines.append("  Verifique se os DOIs são válidos e se há conectividade de rede.")
        report_lines.append("  Consulte um bibliotecário para opções de acesso institucional.")

    report_lines.append("")
    report_lines.append("=" * 60)
    report_lines.append("Relatório gerado automaticamente pelo paper-fetch")
    report_lines.append("=" * 60)

    return "\n".join(report_lines)


def _write_title_report(title_results: list[dict], titles: list[str], path: Path) -> None:
    """Write a report for articles discovered by title."""
    if path.is_dir():
        try:
            import shutil
            shutil.rmtree(path)
        except Exception:
            path = path.parent / f"{path.stem}.txt"
    lines = [
        "=" * 70,
        "RELATÓRIO DE ARTIGOS PESQUISADOS PELO NOME",
        "=" * 70,
        "",
        f"Total de títulos encontrados: {len(titles)}",
        f"Total de pesquisas realizadas: {len(title_results)}",
        f"Downloads concluídos: {sum(1 for r in title_results if r.get('success'))}",
        f"Falhas: {sum(1 for r in title_results if not r.get('success'))}",
        "",
        "DETALHAMENTO:",
        "-" * 70,
        "",
    ]

    for index, result in enumerate(title_results, start=1):
        title = result.get("searched_title", result.get("title", "Título desconhecido"))
        resolved_doi = result.get("resolved_doi") or result.get("doi") or "Não encontrado"
        success = result.get("success", False)

        lines.append(f"{index}. TÍTULO: {title}")
        lines.append(f"   DOI encontrado: {resolved_doi}")

        resolution = result.get("title_resolution") or {}
        if resolution:
            resolver = resolution.get("resolver", "desconhecido")
            resolved_title = resolution.get("resolved_title")
            score = resolution.get("match_score")
            lines.append(f"   Resolvedor: {resolver}")
            if resolved_title:
                lines.append(f"   Título localizado: {resolved_title}")
            if score is not None:
                lines.append(f"   Score: {score}")

        if success:
            lines.append("   Status: DOWNLOAD CONCLUÍDO")
            if result.get("file"):
                lines.append(f"   Arquivo: {result['file']}")
            if result.get("source"):
                lines.append(f"   Fonte: {result['source']}")
        else:
            error = result.get("error") or {}
            lines.append("   Status: NÃO ENCONTRADO")
            lines.append(f"   Erro: {error.get('code', 'desconhecido')}")
            if error.get("message"):
                lines.append(f"   Detalhe: {error['message']}")

        lines.append("")

    lines.append("=" * 70)
    try:
        path.write_text("\n".join(lines), encoding="utf-8")
    except Exception:
        pass
