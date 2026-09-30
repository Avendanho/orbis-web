#!/usr/bin/env python3
"""Diz quais rotas de recuperação estão de fato funcionando nesta máquina.

A taxa de sucesso depende mais da configuração do que do código: sem e-mail a
Unpaywall fica desligada, sem chave a OpenAlex para depois de ~1000 consultas
no dia, e sem acesso institucional nenhum artigo de assinatura sai. Este
comando confere cada chave e rota, uma a uma, e diz o que fazer.

Uso::

    .venv/bin/python scripts/diagnostico.py                 # chaves, APIs e acesso por IP
    .venv/bin/python scripts/diagnostico.py --sem-rede      # só a configuração
    .venv/bin/python scripts/diagnostico.py --testar-sessao 10.1007/s10719-009-9256-7
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

MOTOR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MOTOR / "src" / "download"))

import ambiente  # noqa: E402

CONFIG_ENV = ambiente.carregar()

import institutional  # noqa: E402
import sessao_navegador  # noqa: E402

# Artigo de assinatura (Springer, 2009) usado nas sondas de acesso.
DOI_FECHADO = "10.1007/s10719-009-9256-7"
PDF_FECHADO = f"https://link.springer.com/content/pdf/{DOI_FECHADO}.pdf"
DOI_FECHADO_ELSEVIER = "10.1016/j.jaac.2016.07.155"
UA = "orbis-diagnostico/1.0"

OK, FALHA, AUSENTE, INFO = "✔", "✘", "–", "ℹ"


def _env(nome: str) -> str:
    return os.environ.get(nome, "").strip()


def _estado_chave(nome: str) -> str:
    # Uma chave exportada no terminal vale mesmo com o placeholder no .env.
    if _env(nome):
        return "definida"
    return "placeholder" if nome in CONFIG_ENV["modelo"] else "ausente"


def _get(url: str, *, headers: dict | None = None, timeout: int = 20, limite: int = 4096):
    """(status, cabeçalhos, início do corpo) — nunca levanta."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read(limite)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), (e.read(limite) if e.fp else b"")
    except Exception as e:  # rede fora, DNS, timeout
        return None, {}, str(e).encode()


class Relatorio:
    def __init__(self) -> None:
        self.linhas: list[tuple[str, str, str]] = []
        self.acoes: list[tuple[int, str]] = []

    def item(self, marca: str, nome: str, detalhe: str, acao: str | None = None, ganho: int = 5) -> None:
        """``ganho`` ordena as ações: 1 é o que mais recupera artigos."""
        self.linhas.append((marca, nome, detalhe))
        if acao and all(a != acao for _, a in self.acoes):
            self.acoes.append((ganho, acao))

    def imprimir(self) -> None:
        largura = max(len(n) for _, n, _ in self.linhas)
        for marca, nome, detalhe in self.linhas:
            print(f" {marca} {nome:<{largura}}  {detalhe}")
        if self.acoes:
            print("\nO que fazer, em ordem de ganho:")
            for i, (_, acao) in enumerate(sorted(self.acoes, key=lambda a: a[0]), 1):
                print(f" {i}. {acao}")


def checar_configuracao(r: Relatorio) -> None:
    email = _estado_chave("UNPAYWALL_EMAIL")
    if email == "definida":
        r.item(OK, "Unpaywall (e-mail)", _env("UNPAYWALL_EMAIL"))
    else:
        r.item(FALHA, "Unpaywall (e-mail)", f"{email} — a Unpaywall fica DESLIGADA",
               "Preencha UNPAYWALL_EMAIL no motor/.env com o seu e-mail (a Unpaywall exige um real).", ganho=3)
    for nome, rotulo, acao, ganho in (
        ("ELSEVIER_API_KEY", "Elsevier (chave)",
         "Crie a chave gratuita em https://dev.elsevier.com — ela já entrega os artigos Elsevier em acesso aberto.", 2),
        ("OPENALEX_API_KEY", "OpenAlex (chave)",
         "Crie a chave gratuita da OpenAlex (https://openalex.org/settings/api) e preencha OPENALEX_API_KEY.", 4),
        ("NCBI_API_KEY", "NCBI (chave)",
         "Crie a chave gratuita do NCBI e preencha NCBI_API_KEY: o PMC, que mais entrega, passa de 3 a 10 pedidos/s.", 4),
        ("SEMANTIC_SCHOLAR_API_KEY", "Semantic Scholar (chave)",
         "Peça a chave gratuita do Semantic Scholar e preencha SEMANTIC_SCHOLAR_API_KEY.", 5),
        ("CORE_API_KEY", "CORE (chave)", "Registre-se na CORE (https://core.ac.uk/services/api) e preencha CORE_API_KEY.", 6),
    ):
        estado = _estado_chave(nome)
        r.item(OK if estado == "definida" else AUSENTE, rotulo, estado,
               None if estado == "definida" else acao, ganho=ganho)
    for nome, rotulo in (("ELSEVIER_INST_TOKEN", "Elsevier (insttoken)"), ("SPRINGER_API_KEY", "Springer (chave)"),
                         ("WILEY_TDM_TOKEN", "Wiley TDM (token)")):
        estado = _estado_chave(nome)
        r.item(OK if estado == "definida" else AUSENTE, rotulo, estado)

    if institutional.is_configured():
        rota = institutional.ezproxy_base() or institutional._proxy_label(institutional.proxy_url() or "")
        r.item(OK, "EZproxy / proxy (HTTP)", rota)
    else:
        r.item(AUSENTE, "EZproxy / proxy (HTTP)", "não configurado")

    s = sessao_navegador.situacao()
    if s["habilitada"] and s["login_feito"]:
        r.item(OK, "Sessão CAPES/EZproxy (navegador)", f"ligada · perfil {s['perfil']} · EZproxy {s['ezproxy'] or '—'}")
    elif s["login_feito"]:
        r.item(AUSENTE, "Sessão CAPES/EZproxy (navegador)", "login feito, mas desligada",
               "Ligue a sessão institucional: ORBIS_SESSAO_NAVEGADOR=1 no motor/.env.", ganho=1)
    else:
        r.item(FALHA, "Sessão CAPES/EZproxy (navegador)", "sem login — artigos de assinatura não saem",
               "Faça o login institucional uma vez: .venv/bin/python src/download/sessao_navegador.py login "
               "e depois ORBIS_SESSAO_NAVEGADOR=1 no motor/.env.", ganho=1)

    sombra = [n for n, var in (("Sci-Hub", "PAPER_FETCH_NO_SCIHUB"), ("LibGen", "PAPER_FETCH_NO_LIBGEN"))
              if not _env(var)]
    if sombra:
        r.item(INFO, "Fontes-sombra", f"{', '.join(sombra)} e Anna's Archive ligadas (cópias sem licença; "
               "desligue com PAPER_FETCH_NO_SCIHUB=1 / PAPER_FETCH_NO_LIBGEN=1 / PAPER_FETCH_SOURCES)")


def checar_rede(r: Relatorio) -> None:
    email = _env("UNPAYWALL_EMAIL")
    if email:
        st, _, corpo = _get(f"https://api.unpaywall.org/v2/10.1038/nrg2401?email={urllib.parse.quote(email)}")
        r.item(OK if st == 200 else FALHA, "Unpaywall (API)", f"HTTP {st}" + (" — e-mail recusado" if st == 422 else ""))

    params = {"filter": "doi:10.1038/nrg2401", "select": "id"}
    if _env("OPENALEX_API_KEY"):
        params["api_key"] = _env("OPENALEX_API_KEY")
    st, cab, _ = _get("https://api.openalex.org/works?" + urllib.parse.urlencode(params))
    cab = {k.lower(): v for k, v in cab.items()}
    restante = cab.get("x-ratelimit-remaining")
    limite = cab.get("x-ratelimit-limit")
    detalhe = f"HTTP {st}" + (f" · restam {restante} de {limite} consultas hoje" if restante else "")
    marca = OK if st == 200 else FALHA
    if st == 200 and restante and limite and int(restante) < 0.2 * int(limite):
        marca = FALHA
        detalhe += " — cota quase no fim"
    r.item(marca, "OpenAlex (API)", detalhe)

    cab_s2 = {"x-api-key": _env("SEMANTIC_SCHOLAR_API_KEY")} if _env("SEMANTIC_SCHOLAR_API_KEY") else {}
    st, _, _ = _get("https://api.semanticscholar.org/graph/v1/paper/DOI:10.1038/nrg2401?fields=title", headers=cab_s2)
    r.item(OK if st == 200 else FALHA, "Semantic Scholar (API)", f"HTTP {st}" + (" — pool anônimo estrangulado" if st == 429 else ""))

    if _env("CORE_API_KEY"):
        st, _, _ = _get("https://api.core.ac.uk/v3/search/works?q=" + urllib.parse.quote('doi:"10.1038/nrg2401"') + "&limit=1",
                        headers={"Authorization": f"Bearer {_env('CORE_API_KEY')}"})
        r.item(OK if st == 200 else FALHA, "CORE (API)", f"HTTP {st}" + (" — chave recusada" if st in (401, 403) else ""))

    if _env("ELSEVIER_API_KEY"):
        cab_els = {"X-ELS-APIKey": _env("ELSEVIER_API_KEY"), "Accept": "application/json"}
        if _env("ELSEVIER_INST_TOKEN"):
            cab_els["X-ELS-Insttoken"] = _env("ELSEVIER_INST_TOKEN")
        st, _, corpo = _get(f"https://api.elsevier.com/content/article/entitlement/doi/{DOI_FECHADO_ELSEVIER}", headers=cab_els)
        try:
            direito = json.loads(corpo)["entitlement-response"]["document-entitlement"]["entitled"]
        except Exception:
            direito = None
        if direito is True:
            r.item(OK, "Elsevier (assinatura)", "chave com direito a artigos de assinatura")
        else:
            r.item(INFO, "Elsevier (assinatura)", f"HTTP {st} · sem direito a assinatura (só acesso aberto)")

    st, cab, corpo = _get(PDF_FECHADO, headers={"User-Agent": "Mozilla/5.0"})
    if st == 200 and corpo[:5] == b"%PDF-":
        r.item(OK, "Acesso por IP desta rede", "a editora entregou um PDF de assinatura — rede institucional")
    else:
        r.item(AUSENTE, "Acesso por IP desta rede", "sem acesso a assinatura por IP (normal fora do campus/VPN)")

    if institutional.is_configured():
        dados, erro = institutional.fetch_pdf(PDF_FECHADO, timeout=25)
        r.item(OK if dados else FALHA, "EZproxy / proxy (teste)", "entregou o PDF de assinatura" if dados else f"falhou: {erro}")


def testar_sessao(r: Relatorio, doi: str) -> None:
    os.environ.setdefault("ORBIS_SESSAO_NAVEGADOR", "1")
    if not sessao_navegador.perfil_existe():
        r.item(FALHA, "Sessão (teste)", "sem login feito")
        return
    dados, erro = sessao_navegador.baixar_pdf(doi, prazo=60)
    sessao_navegador.encerrar()
    if dados:
        r.item(OK, "Sessão (teste)", f"{doi}: PDF de {len(dados) // 1024} KB")
    else:
        dica = {"sessao_expirada": " — refaça o login", "sem_acesso_institucional": " — a instituição não assina este título"}
        r.item(FALHA, "Sessão (teste)", f"{doi}: {erro}{dica.get(erro, '')}",
               "Refaça o login: .venv/bin/python src/download/sessao_navegador.py login" if erro == "sessao_expirada" else None)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sem-rede", action="store_true", help="só confere a configuração, sem chamar as APIs")
    ap.add_argument("--testar-sessao", metavar="DOI", help="tenta baixar este DOI pela sessão institucional")
    args = ap.parse_args()

    r = Relatorio()
    checar_configuracao(r)
    if not args.sem_rede:
        checar_rede(r)
    if args.testar_sessao:
        testar_sessao(r, args.testar_sessao)
    print("Diagnóstico do motor de recuperação\n")
    r.imprimir()
    return 0


if __name__ == "__main__":
    sys.exit(main())
