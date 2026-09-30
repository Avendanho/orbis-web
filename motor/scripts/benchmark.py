#!/usr/bin/env python3
"""Mede a taxa de recuperação de PDFs por DOI do motor, do jeito que o ORBIS usa.

Cada DOI passa por ``fetch.fetch`` com o mesmo orçamento que o serviço dá a um
artigo (90 s), quatro artigos por vez, numa pasta vazia — sem cache, então o
número medido é o de uma primeira execução. Sucesso aqui é o mesmo que no
ORBIS: PDF estruturalmente válido E com a identidade bibliográfica conferida.

Uso::

    .venv/bin/python scripts/benchmark.py --arquivo lista.csv --amostra 100 \\
        --teto-oa --saida relatorio/benchmark-antes
    .venv/bin/python scripts/benchmark.py --arquivo lista.csv --amostra 100 \\
        --teto-oa --saida relatorio/benchmark-depois \\
        --comparar relatorio/benchmark-antes/resultados.json

Fontes: por padrão só as legais. ``--fontes todas`` inclui Sci-Hub, LibGen e
Anna's Archive, que distribuem cópias sem licença e não contam para a meta.

``--teto-oa`` consulta a OpenAlex para saber quais DOIs têm alguma cópia em
acesso aberto. É o que separa "o pipeline errou" de "não existe cópia legal".
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import logging
import random
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

MOTOR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MOTOR / "src" / "download"))

import ambiente  # noqa: E402

CONFIG_ENV = ambiente.carregar()

import fetch  # noqa: E402
import http_retry  # noqa: E402

# DOIs conhecidos, de editoras e situações de acesso variadas, para quando
# nenhum arquivo é passado. Não substituem uma amostra do acervo real.
EMBUTIDOS = [
    "10.1371/journal.pone.0115069",   # PLOS (gold)
    "10.3389/fnins.2021.653120",      # Frontiers (gold)
    "10.3390/jcm11216493",            # MDPI (gold)
    "10.1186/2040-2392-2-13",         # BMC (gold)
    "10.1038/ncomms6748",             # Nature Communications (gold)
    "10.1101/2023.06.29.547069",      # bioRxiv (preprint)
    "10.48550/arxiv.1706.03762",      # arXiv
    "10.1016/j.cell.2020.02.052",     # Elsevier, bronze (COVID)
    "10.1056/nejmoa2002032",          # NEJM, bronze (COVID)
    "10.1126/science.aaa8415",        # Science, green
    "10.1038/nrg2401",                # Nature Reviews (fechado)
    "10.1016/j.jaac.2016.07.155",     # Elsevier (fechado)
    "10.1111/bjd.14578",              # Wiley (fechado)
    "10.1007/s10719-009-9256-7",      # Springer (fechado)
]

# Distribuem cópias sem licença do detentor dos direitos.
FONTES_SOMBRA = {"scihub", "libgen", "annas_archive"}
# Raspagem que os termos de uso do serviço proíbem.
FONTES_TOS = {"google_scholar"}


def fontes_legais() -> list[str]:
    """Todas as fontes que o fetch conhece, menos as que não contam para a meta."""
    nomes = set(re.findall(r'_can_try\("([a-z_0-9]+)"\)', Path(fetch.__file__).read_text(encoding="utf-8")))
    nomes |= set(getattr(fetch, "EXPANDED_SOURCE_NAMES", {}))
    # Buscas por título da recuperação v4 (fetch_title_direct).
    nomes |= {"springer", "wos", "scopus", "ieee"}
    return sorted(nomes - FONTES_SOMBRA - FONTES_TOS)


def ler_dois(arquivo: Path | None) -> list[str]:
    if arquivo is None:
        return list(EMBUTIDOS)
    texto = arquivo.read_text(encoding="utf-8-sig")
    dois: list[str] = []
    if arquivo.suffix.lower() == ".csv":
        leitor = csv.DictReader(texto.splitlines())
        coluna = next((c for c in (leitor.fieldnames or []) if c.strip().lower() == "doi"), None)
        for linha in leitor:
            valores = [linha[coluna]] if coluna else list(linha.values())
            dois.extend(v for v in valores if v and v.strip().startswith("10."))
    else:
        dois = [l.strip() for l in texto.splitlines() if l.strip().startswith("10.")]
    vistos: dict[str, None] = {}
    for d in dois:
        vistos.setdefault(fetch.normalize_doi(d.strip()), None)
    return list(vistos)


def teto_openalex(dois: list[str]) -> dict[str, dict]:
    """Situação de acesso aberto e tipo de cada DOI segundo a OpenAlex."""
    info: dict[str, dict] = {}
    for i in range(0, len(dois), 40):
        lote = dois[i:i + 40]
        url = ("https://api.openalex.org/works?per-page=50&select=doi,type,open_access&filter="
               + urllib.parse.quote("doi:" + "|".join(lote)))
        try:
            req = urllib.request.Request(url, headers={"User-Agent": fetch.UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                dados = json.load(r)
        except Exception as exc:  # o teto é informativo; sem ele o benchmark segue
            print(f"[teto-oa] lote {i // 40 + 1} falhou: {exc}", file=sys.stderr)
            continue
        for w in dados.get("results", []):
            doi = fetch.normalize_doi((w.get("doi") or "").replace("https://doi.org/", ""))
            info[doi] = {"oa_status": (w.get("open_access") or {}).get("oa_status"), "tipo": w.get("type")}
    return info


def classificar(r: dict) -> str:
    """Motivo da falha em poucas categorias, a partir do resultado do fetch."""
    if r.get("success"):
        return "ok"
    codigo = str((r.get("error") or {}).get("code") or "")
    if codigo == "article_identity_not_confirmed":
        return "identidade_rejeitada"
    if codigo == "not_found":
        return "nenhuma_fonte_tem_pdf"
    if codigo == "resolve_network_error":
        return "metadados_indisponiveis"
    if codigo in ("download_item_deadline", "download_timeout") or "deadline" in codigo:
        return "tempo_esgotado"
    if re.fullmatch(r"download_http_40[13]", codigo):
        return "bloqueio_401_403"
    if codigo == "download_not_a_pdf":
        return "pagina_em_vez_de_pdf"
    if codigo.startswith("download_"):
        return "falha_de_download"
    return codigo or "desconhecido"


_falhas_por_host: collections.Counter = collections.Counter()
_cooldowns_por_host: collections.Counter = collections.Counter()


def instrumentar_falhas_transitorias() -> None:
    """Conta 429/5xx/timeouts e cooldowns por host sem mudar o comportamento.

    Uma API que entra em cooldown no meio da execução some para todos os
    artigos seguintes; sem esta contagem isso passa por "fonte não tem PDF".
    """
    original = http_retry.note_failure

    def contando(url, *, status=None, retry_after=None):
        cooldown = original(url, status=status, retry_after=retry_after)
        host = http_retry.host_of(url)
        _falhas_por_host[f"{host} {status or 'rede/timeout'}"] += 1
        if cooldown:
            _cooldowns_por_host[host] += 1
        return cooldown

    http_retry.note_failure = contando


def rodar_um(doi: str, pasta: Path, *, prazo: float, timeout: int, fontes: list[str] | None) -> dict:
    fetch.set_item_deadline(prazo)
    inicio = time.monotonic()
    try:
        r = fetch.fetch(doi, pasta, dry_run=False, overwrite=False, timeout=min(timeout, int(prazo)), sources=fontes)
    except Exception as exc:  # um DOI que derruba o fetch é falha, não fim do benchmark
        r = {"doi": doi, "success": False, "error": {"code": "excecao", "message": repr(exc)}}
    finally:
        fetch.set_item_deadline(None)
    segundos = time.monotonic() - inicio
    erros_download = [f"{e.get('source')}:{e.get('reason')}" for e in (r.get("download_attempts") or [])]
    rejeicoes = [f"{e.get('source')}:{e.get('reason')}" for e in (r.get("identity_rejections") or [])]
    return {
        "doi": doi,
        "ok": bool(r.get("success")),
        "fonte": r.get("source") if r.get("success") else None,
        "segundos": round(segundos, 1),
        "categoria": classificar(r),
        "codigo": (r.get("error") or {}).get("code"),
        "mensagem": (r.get("error") or {}).get("message"),
        "metodo_identidade": r.get("validation_method"),
        "fontes_tentadas": r.get("sources_tried") or [],
        "erros_download": erros_download[:12],
        "rejeicoes_identidade": rejeicoes[:6],
    }


def resumir(resultados: list[dict], teto: dict[str, dict]) -> dict:
    n = len(resultados)
    ok = sum(r["ok"] for r in resultados)
    resumo: dict = {
        "total": n,
        "sucessos": ok,
        "taxa": round(ok / n, 4) if n else 0.0,
        "tempo_medio_s": round(sum(r["segundos"] for r in resultados) / n, 1) if n else 0.0,
        "tempo_medio_sucesso_s": round(
            sum(r["segundos"] for r in resultados if r["ok"]) / max(1, ok), 1),
        "por_fonte": dict(collections.Counter(r["fonte"] for r in resultados if r["ok"]).most_common()),
        "falhas_por_categoria": dict(collections.Counter(
            r["categoria"] for r in resultados if not r["ok"]).most_common()),
    }
    if teto:
        com_oa = [r for r in resultados if (teto.get(r["doi"]) or {}).get("oa_status") not in (None, "closed")]
        fechados = [r for r in resultados if (teto.get(r["doi"]) or {}).get("oa_status") == "closed"]
        resumo["teto_oa"] = {
            "com_copia_aberta": len(com_oa),
            "fechados": len(fechados),
            "sem_informacao": n - len(com_oa) - len(fechados),
            "teto_legal_estimado": round(len(com_oa) / n, 4) if n else 0.0,
            "recuperados_entre_abertos": sum(r["ok"] for r in com_oa),
            "recuperados_entre_fechados": sum(r["ok"] for r in fechados),
            "abertos_perdidos": sorted(r["doi"] for r in com_oa if not r["ok"]),
        }
    return resumo


def relatorio_md(resumo: dict, resultados: list[dict], teto: dict, config: dict, anterior: dict | None) -> str:
    pct = lambda x: f"{100 * x:.1f}%"  # noqa: E731
    linhas = [
        f"# Benchmark de recuperação — {config['quando']}",
        "",
        f"- DOIs: **{resumo['total']}** (arquivo: `{config['arquivo']}`, amostra: {config['amostra']}, semente {config['semente']})",
        f"- Fontes: **{config['fontes']}** · prazo por artigo {config['prazo']} s · {config['workers']} em paralelo",
        f"- **Taxa de sucesso: {pct(resumo['taxa'])}** ({resumo['sucessos']}/{resumo['total']})",
        f"- Tempo médio por DOI: {resumo['tempo_medio_s']} s (sucessos: {resumo['tempo_medio_sucesso_s']} s)",
        "",
    ]
    if anterior:
        ra = anterior["resumo"]
        linhas += [
            "## Antes × depois", "",
            "| | antes | depois |", "|---|---|---|",
            f"| taxa de sucesso | {pct(ra['taxa'])} ({ra['sucessos']}/{ra['total']}) | {pct(resumo['taxa'])} ({resumo['sucessos']}/{resumo['total']}) |",
            f"| tempo médio por DOI | {ra['tempo_medio_s']} s | {resumo['tempo_medio_s']} s |",
        ]
        if "teto_oa" in ra and "teto_oa" in resumo:
            a, d = ra["teto_oa"], resumo["teto_oa"]
            linhas.append(
                f"| abertos recuperados | {a['recuperados_entre_abertos']}/{a['com_copia_aberta']} "
                f"| {d['recuperados_entre_abertos']}/{d['com_copia_aberta']} |")
        antes = {r["doi"]: r for r in anterior["resultados"]}
        ganhos = [r["doi"] for r in resultados if r["ok"] and not (antes.get(r["doi"]) or {}).get("ok", True)]
        perdas = [r["doi"] for r in resultados if not r["ok"] and (antes.get(r["doi"]) or {}).get("ok")]
        linhas += ["", f"Ganhos ({len(ganhos)}): {', '.join(ganhos) or '—'}",
                   "", f"Perdas ({len(perdas)}): {', '.join(perdas) or '—'}", ""]
    if "teto_oa" in resumo:
        t = resumo["teto_oa"]
        linhas += [
            "## Teto legal (OpenAlex)", "",
            f"- Com alguma cópia em acesso aberto: **{t['com_copia_aberta']}** "
            f"(teto estimado {pct(t['teto_legal_estimado'])}); fechados: {t['fechados']}; sem informação: {t['sem_informacao']}",
            f"- Recuperados entre os abertos: **{t['recuperados_entre_abertos']}/{t['com_copia_aberta']}**",
            f"- Recuperados entre os fechados: {t['recuperados_entre_fechados']}/{t['fechados']}",
            "",
        ]
    linhas += ["## Sucessos por fonte", "", "| fonte | artigos |", "|---|---|"]
    linhas += [f"| {f} | {n} |" for f, n in resumo["por_fonte"].items()]
    linhas += ["", "## Falhas por categoria", "", "| categoria | artigos |", "|---|---|"]
    linhas += [f"| {c} | {n} |" for c, n in resumo["falhas_por_categoria"].items()]
    if resumo.get("falhas_transitorias"):
        linhas += ["", "## Falhas transitórias por host (429, 5xx, rede)", "",
                   "| host e status | vezes |", "|---|---|"]
        linhas += [f"| {k} | {n} |" for k, n in resumo["falhas_transitorias"].items()]
        if resumo.get("cooldowns"):
            linhas += ["", "Hosts postos em cooldown: "
                       + ", ".join(f"{h} ({n}×)" for h, n in resumo["cooldowns"].items())]
    linhas += ["", "## Falhas, uma a uma", "",
               "| DOI | OA (OpenAlex) | categoria | s | erros de download / rejeições |", "|---|---|---|---|---|"]
    for r in sorted((r for r in resultados if not r["ok"]), key=lambda r: r["categoria"]):
        oa = (teto.get(r["doi"]) or {}).get("oa_status") or "?"
        detalhe = "; ".join(r["erros_download"][:4] + r["rejeicoes_identidade"][:2]) or "—"
        linhas.append(f"| {r['doi']} | {oa} | {r['categoria']} | {r['segundos']} | {detalhe} |")
    return "\n".join(linhas) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--arquivo", type=Path, help="CSV (coluna 'doi') ou TXT (um DOI por linha)")
    ap.add_argument("--amostra", type=int, default=0, help="sorteia N DOIs do arquivo (0 = todos)")
    ap.add_argument("--semente", type=int, default=42)
    ap.add_argument("--fontes", choices=("legais", "todas"), default="legais")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--prazo", type=float, default=90.0, help="segundos por artigo (o ORBIS usa 90)")
    ap.add_argument("--timeout", type=int, default=30, help="timeout por requisição")
    ap.add_argument("--teto-oa", action="store_true", help="anota cada DOI com a situação OA da OpenAlex")
    ap.add_argument("--saida", type=Path, default=MOTOR / "relatorio" / f"benchmark-{datetime.now():%Y%m%d-%H%M}")
    ap.add_argument("--comparar", type=Path, help="resultados.json de uma execução anterior")
    ap.add_argument("--pasta-pdfs", type=Path, help="guarda os PDFs aqui (padrão: pasta temporária, apagada)")
    args = ap.parse_args()

    dois = ler_dois(args.arquivo)
    if args.amostra and args.amostra < len(dois):
        dois = sorted(random.Random(args.semente).sample(dois, args.amostra))
    if not dois:
        print("Nenhum DOI para medir.", file=sys.stderr)
        return 2
    fontes = fontes_legais() if args.fontes == "legais" else None
    fetch._format = "silent"
    instrumentar_falhas_transitorias()
    if CONFIG_ENV["modelo"]:
        print("Aviso: estas chaves do motor/.env ainda têm o texto do exemplo e foram ignoradas: "
              + ", ".join(CONFIG_ENV["modelo"]), file=sys.stderr)
    # Avisos de fonte tipográfica do pypdf, um por PDF lido: só poluem a saída.
    logging.getLogger("pypdf").setLevel(logging.ERROR)

    teto = teto_openalex(dois) if args.teto_oa else {}
    args.saida.mkdir(parents=True, exist_ok=True)
    tmp = None if args.pasta_pdfs else tempfile.TemporaryDirectory(prefix="orbis-bench-")
    pasta_base = args.pasta_pdfs or Path(tmp.name)

    print(f"{len(dois)} DOIs · fontes {args.fontes} · prazo {args.prazo:.0f}s · {args.workers} workers", file=sys.stderr)
    resultados: list[dict] = []
    inicio = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        # Uma pasta por DOI, como o serviço faz: nomes de arquivo gerados podem colidir.
        futuros = {
            pool.submit(rodar_um, d, pasta_base / re.sub(r"[^A-Za-z0-9._-]", "_", d),
                        prazo=args.prazo, timeout=args.timeout, fontes=fontes): d
            for d in dois
        }
        for i, fut in enumerate(as_completed(futuros), 1):
            r = fut.result()
            resultados.append(r)
            marca = "✔" if r["ok"] else "✘"
            print(f"[{i:>3}/{len(dois)}] {marca} {r['doi']:<45} {r['fonte'] or r['categoria']:<24} {r['segundos']:>5.1f}s",
                  file=sys.stderr, flush=True)
    if tmp:
        tmp.cleanup()

    resultados.sort(key=lambda r: r["doi"])
    for r in resultados:
        r.update({k: v for k, v in (teto.get(r["doi"]) or {}).items()})
    resumo = resumir(resultados, teto)
    resumo["duracao_total_s"] = round(time.monotonic() - inicio, 1)
    resumo["falhas_transitorias"] = dict(_falhas_por_host.most_common(15))
    resumo["cooldowns"] = dict(_cooldowns_por_host.most_common())
    config = {
        "quando": datetime.now().isoformat(timespec="seconds"),
        "arquivo": str(args.arquivo or "lista embutida"),
        "amostra": args.amostra or "todos",
        "semente": args.semente,
        "fontes": args.fontes,
        "prazo": args.prazo,
        "workers": args.workers,
    }
    anterior = json.loads(args.comparar.read_text(encoding="utf-8")) if args.comparar else None
    (args.saida / "resultados.json").write_text(
        json.dumps({"config": config, "resumo": resumo, "resultados": resultados}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    (args.saida / "relatorio.md").write_text(relatorio_md(resumo, resultados, teto, config, anterior), encoding="utf-8")
    print(f"\nTaxa de sucesso: {100 * resumo['taxa']:.1f}% ({resumo['sucessos']}/{resumo['total']}) "
          f"· relatório em {args.saida / 'relatorio.md'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
