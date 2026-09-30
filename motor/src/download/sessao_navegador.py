"""Sessão institucional no navegador: o acesso que a CAPES (CAFe) ou a biblioteca já dá.

Para que serve
--------------
Metade do acervo de uma revisão típica é de assinatura (Elsevier, Springer,
Wiley…) e não tem cópia aberta em lugar nenhum. O pesquisador de instituição
brasileira tem acesso a boa parte disso pelo Portal de Periódicos da CAPES,
com login CAFe — só que esse login é SSO no navegador, sem usuário e senha que
um cliente HTTP consiga enviar. Este módulo usa exatamente a sessão que o
pesquisador abriu:

1. ``python sessao_navegador.py login`` abre um Chromium visível com um perfil
   próprio (uma pasta). O pesquisador entra pela CAPES/CAFe ou pelo EZproxy da
   biblioteca e fecha a janela. Os cookies ficam no perfil.
2. No download, um DOI que as fontes abertas não resolveram é aberto nesse
   mesmo perfil (sem janela), passando pelo EZproxy quando configurado, e o
   PDF que a página da editora oferece é baixado com a sessão do pesquisador.

Limites deliberados
-------------------
* **Só com opt-in** (``ORBIS_SESSAO_NAVEGADOR=1``) e com login feito.
* **Um artigo por vez, com intervalo** (``ORBIS_SESSAO_INTERVALO``, padrão 6 s)
  **e teto por execução** (``ORBIS_SESSAO_MAX_ARTIGOS``, padrão 150). As
  licenças proíbem download sistemático; um robô rápido demais faz a editora
  bloquear a instituição inteira, não só quem abusou.
* **Nenhum disfarce.** É um Chromium comum com a sessão do próprio usuário;
  sem stealth, sem troca de identidade. O que a editora recusa fica recusado.
* **Sessão expirada para tudo.** Ao cair numa tela de login, a rota se desliga
  até o fim do processo e avisa como renovar, em vez de gastar o prazo de cada
  artigo seguinte na mesma tela.

A API síncrona do Playwright só pode ser usada pela thread que a criou, e o
serviço baixa vários artigos em paralelo: por isso uma thread dedicada é dona
do navegador e as demais só enfileiram pedidos e esperam a resposta.
"""
from __future__ import annotations

import os
import queue
import re
import sys
import threading
import time
import urllib.parse
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FuturoEsgotado
from pathlib import Path

MAX_PDF_SIZE = 50 * 1024 * 1024
RAIZ_MOTOR = Path(__file__).resolve().parents[2]
PORTAL_CAPES = "https://www.periodicos.capes.gov.br/"

# Endereços de tela de login: provedores de identidade (CAFe/Shibboleth/SAML),
# o formulário do EZproxy e afins.
_TELA_LOGIN_RE = re.compile(
    r"(/login\b|/signin\b|shibboleth|/idp/|/saml|wayf|\bcafe\b|\.rnp\.br|openathens|/sso\b|/auth/realms/)",
    re.IGNORECASE,
)
# Mensagens que o EZproxy e as editoras mostram a quem não tem acesso.
_SEM_ACESSO_RE = re.compile(
    r"(purchase (this )?(article|access)|buy (this )?article|get access|rent this article|"
    r"access through your institution|log in to (check )?access|subscribe to (this )?journal)",
    re.IGNORECASE,
)

_estado = {"expirada": False, "indisponivel": "", "artigos": 0, "desde": 0.0}
_estado_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

def habilitada() -> bool:
    return os.environ.get("ORBIS_SESSAO_NAVEGADOR", "").strip().lower() in {"1", "true", "yes", "on", "sim"}


def pasta_perfil() -> Path:
    explicito = os.environ.get("ORBIS_PERFIL_NAVEGADOR", "").strip()
    if explicito:
        return Path(explicito).expanduser()
    base = Path(os.environ.get("ORBIS_DATA_DIR") or RAIZ_MOTOR)
    return base / "data" / "perfil-navegador"


def perfil_existe() -> bool:
    """True depois do primeiro ``login`` (o Chromium cria a pasta ``Default``)."""
    return (pasta_perfil() / "Default").is_dir()


def ezproxy_base() -> str | None:
    """EZproxy por onde a sessão entra — o da CAPES/CAFe ou o da biblioteca."""
    base = (os.environ.get("ORBIS_SESSAO_EZPROXY", "").strip()
            or os.environ.get("EZPROXY_BASE_URL", "").strip()).rstrip("/")
    return base or None


def intervalo() -> float:
    try:
        return max(0.0, float(os.environ.get("ORBIS_SESSAO_INTERVALO", "6")))
    except ValueError:
        return 6.0


def teto() -> int:
    try:
        return max(0, int(os.environ.get("ORBIS_SESSAO_MAX_ARTIGOS", "150")))
    except ValueError:
        return 150


def _cookies_mtime() -> float:
    """Última gravação dos cookies do perfil (o Chromium muda o lugar entre versões)."""
    for rel in ("Default/Network/Cookies", "Default/Cookies"):
        try:
            return (pasta_perfil() / rel).stat().st_mtime
        except OSError:
            continue
    return 0.0


def _login_renovado() -> bool:
    """Depois de expirar, um novo ``login`` grava cookies: a rota volta sem reiniciar o motor."""
    if not (_estado["expirada"] or _estado["indisponivel"]) or not _estado.get("desde"):
        return False
    if _cookies_mtime() <= _estado["desde"]:
        return False
    with _estado_lock:
        _estado.update({"expirada": False, "indisponivel": "", "desde": 0.0})
    return True


def disponivel() -> bool:
    """A rota pode ser tentada agora? (habilitada, com login, sem ter expirado, abaixo do teto)."""
    if not (habilitada() and perfil_existe()):
        return False
    _login_renovado()
    return not _estado["expirada"] and not _estado["indisponivel"] and _estado["artigos"] < teto()


def situacao() -> dict:
    """Resumo para diagnóstico e para as mensagens de falha."""
    return {
        "habilitada": habilitada(),
        "perfil": str(pasta_perfil()),
        "login_feito": perfil_existe(),
        "ezproxy": ezproxy_base(),
        "expirada": _estado["expirada"],
        "indisponivel": _estado["indisponivel"],
        "artigos_nesta_execucao": _estado["artigos"],
        "teto": teto(),
    }


def reiniciar_estado() -> None:
    """Usado pelos testes e ao renovar o login no mesmo processo."""
    with _estado_lock:
        _estado.update({"expirada": False, "indisponivel": "", "artigos": 0, "desde": 0.0})


# ---------------------------------------------------------------------------
# Endereço de entrada
# ---------------------------------------------------------------------------

def resolver_doi(doi: str, *, timeout: int = 10) -> str | None:
    """URL da editora para o DOI, pela API de handles do doi.org.

    Resolver antes de abrir o navegador permite mandar ao EZproxy o endereço da
    editora — nem todo EZproxy tem o doi.org na lista de hosts atendidos.
    """
    import runtime

    try:
        dados = runtime._get_json(
            f"https://doi.org/api/handles/{urllib.parse.quote(doi, safe='/')}?type=URL", timeout=timeout)
    except Exception:
        return None
    for valor in dados.get("values") or []:
        if valor.get("type") == "URL":
            url = (valor.get("data") or {}).get("value")
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                return url
    return None


def url_entrada(alvo: str) -> str:
    base = ezproxy_base()
    if not base:
        return alvo
    return f"{base}/login?url={urllib.parse.quote(alvo, safe='')}"


# ---------------------------------------------------------------------------
# O trabalho dentro do navegador (roda na thread dona do Playwright)
# ---------------------------------------------------------------------------

def _eh_pdf(dados: bytes | None) -> bool:
    return bool(dados) and dados[:5] == b"%PDF-" and len(dados) <= MAX_PDF_SIZE


def _parece_tela_de_login(url: str, senha_visivel: bool) -> bool:
    partes = urllib.parse.urlparse(url)
    return senha_visivel or bool(_TELA_LOGIN_RE.search(f"{partes.netloc}{partes.path}"))


def _baixar_no_navegador(contexto, alvo: str, doi: str | None, limite: float) -> tuple[bytes | None, str | None]:
    """Abre ``alvo`` com a sessão e devolve o PDF do artigo. (dados, erro)."""
    from pdf_links import extract_pdf_links

    def resta_ms() -> int:
        return max(1000, int((limite - time.monotonic()) * 1000))

    pdfs: list[bytes] = []
    downloads: list = []
    pagina = contexto.new_page()

    def ao_responder(resposta) -> None:
        try:
            tipo = (resposta.headers.get("content-type") or "").lower()
            if "pdf" in tipo or "octet-stream" in tipo:
                corpo = resposta.body()
                if _eh_pdf(corpo):
                    pdfs.append(corpo)
        except Exception:
            pass

    def ao_baixar(download) -> None:
        downloads.append(download)

    pagina.on("response", ao_responder)
    pagina.on("download", ao_baixar)

    def colher() -> bytes | None:
        if pdfs:
            return pdfs[0]
        for dl in downloads:
            try:
                caminho = dl.path()
                dados = Path(caminho).read_bytes() if caminho else b""
            except Exception:
                continue
            if _eh_pdf(dados):
                return dados
        return None

    def navegar(url: str) -> None:
        try:
            pagina.goto(url, wait_until="domcontentloaded", timeout=resta_ms())
        except Exception:
            # Um PDF servido como download aborta a navegação; o arquivo chega
            # pelo evento "download" mesmo assim.
            pass

    try:
        navegar(url_entrada(alvo))
        if (dados := colher()) is not None:
            return dados, None
        # Páginas de editora que terminam de montar o link em JavaScript.
        try:
            pagina.wait_for_load_state("networkidle", timeout=min(8000, resta_ms()))
        except Exception:
            pass
        if (dados := colher()) is not None:
            return dados, None

        html = pagina.content()
        candidatos = extract_pdf_links(html, pagina.url, doi=doi)[:4]
        if not candidatos:
            # Só conta campo de senha VISÍVEL: Wiley e outras trazem um
            # formulário de login escondido no cabeçalho de toda página de
            # artigo, e tomá-lo por sessão expirada desligaria a rota inteira.
            try:
                senha_visivel = pagina.locator("input[type=password]:visible").count() > 0
            except Exception:
                senha_visivel = False
            if _parece_tela_de_login(pagina.url, senha_visivel):
                return None, "sessao_expirada"
        for candidato in candidatos:
            if time.monotonic() >= limite:
                return None, "prazo_esgotado"
            # O cliente de requisições do contexto usa os mesmos cookies da sessão.
            try:
                resposta = contexto.request.get(candidato, timeout=resta_ms())
                corpo = resposta.body() if resposta.status == 200 else b""
            except Exception:
                corpo = b""
            if _eh_pdf(corpo):
                return corpo, None
            # Algumas editoras (ScienceDirect) passam por uma página intermediária
            # que redireciona ao PDF em JavaScript: só a navegação chega lá.
            navegar(candidato)
            try:
                pagina.wait_for_load_state("networkidle", timeout=min(6000, resta_ms()))
            except Exception:
                pass
            if (dados := colher()) is not None:
                return dados, None
        if _SEM_ACESSO_RE.search(html):
            return None, "sem_acesso_institucional"
        return None, "sem_pdf_na_pagina" if not candidatos else "pdf_nao_entregue"
    finally:
        try:
            pagina.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# A thread dona do navegador
# ---------------------------------------------------------------------------

def _abrir_contexto(playwright):
    """Contexto persistente e sem janela sobre o perfil do login."""
    return playwright.chromium.launch_persistent_context(
        str(pasta_perfil()),
        headless=os.environ.get("ORBIS_SESSAO_VISIVEL", "").strip() not in {"1", "true", "sim"},
        accept_downloads=True,
    )


def _iniciar_playwright():
    from playwright.sync_api import sync_playwright

    return sync_playwright().start()


class _Dono(threading.Thread):
    def __init__(self) -> None:
        super().__init__(name="orbis-sessao-navegador", daemon=True)
        self.fila: queue.Queue = queue.Queue()
        self._ultimo_artigo = 0.0
        # Marcado sob _dono_lock junto com a drenagem da fila: quem enfileira
        # sob o mesmo lock nunca deixa um pedido numa thread que já acabou.
        self.encerrado = False

    def run(self) -> None:
        playwright = contexto = None
        try:
            playwright = _iniciar_playwright()
            contexto = _abrir_contexto(playwright)
        except Exception as exc:
            motivo = str(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__
            if "already in use" in motivo.lower() or "singleton" in motivo.lower():
                motivo = "perfil em uso por outro navegador (feche a janela do login)"
            _estado["indisponivel"] = motivo
            _estado["desde"] = time.time()
        try:
            # Sem navegador a thread termina: a próxima tentativa, depois de um
            # login novo, cria outra em vez de herdar esta sem contexto.
            while contexto is not None:
                pedido = self.fila.get()
                if pedido is None:
                    break
                self._atender(contexto, *pedido)
        finally:
            for obj in (contexto, playwright):
                try:
                    (obj.stop if hasattr(obj, "stop") else obj.close)()
                except Exception:
                    pass
            with _dono_lock:
                self.encerrado = True
                self._drenar("navegador_indisponivel" if contexto is None else "sessao_encerrada")

    def _drenar(self, motivo: str) -> None:
        """Responde quem ficou na fila, para ninguém esperar até o fim do prazo."""
        while True:
            try:
                pedido = self.fila.get_nowait()
            except queue.Empty:
                return
            if pedido is not None:
                pedido[3].set_result((None, motivo))

    def _atender(self, contexto, alvo: str, doi: str | None, limite: float, futuro: Future) -> None:
        espera = self._ultimo_artigo + intervalo() - time.monotonic()
        if espera > 0:
            time.sleep(min(espera, max(0.0, limite - time.monotonic())))
        if time.monotonic() >= limite - 2 or _estado["expirada"]:
            futuro.set_result((None, "sessao_expirada" if _estado["expirada"] else "prazo_esgotado_na_fila"))
            return
        # O teto conta páginas abertas na editora, não pedidos: um pedido que
        # venceu na fila não pesou para ninguém.
        with _estado_lock:
            if _estado["artigos"] >= teto():
                futuro.set_result((None, "teto_da_sessao_atingido"))
                return
            _estado["artigos"] += 1
        self._ultimo_artigo = time.monotonic()
        try:
            resultado = _baixar_no_navegador(contexto, alvo, doi, limite)
        except Exception as exc:
            resultado = (None, f"erro_navegador:{str(exc)[:120]}")
        futuro.set_result(resultado)


_dono: _Dono | None = None
_dono_lock = threading.Lock()


def _enfileirar(pedido: tuple) -> None:
    global _dono
    with _dono_lock:
        if _dono is None or _dono.encerrado:
            _dono = _Dono()
            _dono.fila.put(pedido)
            _dono.start()
        else:
            _dono.fila.put(pedido)


def encerrar() -> None:
    """Fecha o navegador. O join fica fora do lock: a thread precisa dele para terminar."""
    global _dono
    with _dono_lock:
        dono, _dono = _dono, None
    if dono is not None and dono.is_alive():
        dono.fila.put(None)
        dono.join(timeout=10)


def baixar_pdf(doi: str, *, prazo: float, alvo: str | None = None) -> tuple[bytes | None, str | None]:
    """PDF do artigo pela sessão institucional. (dados, erro).

    ``prazo`` são os segundos que o chamador ainda tem para este artigo; um
    pedido que espera na fila além disso é descartado sem abrir página.
    """
    if not disponivel():
        return None, "sessao_indisponivel"
    alvo = alvo or resolver_doi(doi, timeout=min(10, max(2, int(prazo / 4)))) or f"https://doi.org/{doi}"
    limite = time.monotonic() + prazo
    futuro: Future = Future()
    _enfileirar((alvo, doi, limite, futuro))
    try:
        dados, erro = futuro.result(timeout=prazo + 5)
    except FuturoEsgotado:
        return None, "prazo_esgotado"
    if erro == "sessao_expirada":
        with _estado_lock:
            primeira = not _estado["expirada"]
            _estado["expirada"] = True
            _estado["desde"] = time.time()
        if primeira:
            print("[sessao] A sessão institucional expirou (caiu numa tela de login). "
                  "Renove com: python src/download/sessao_navegador.py login — "
                  "o motor volta a usá-la sozinho, sem reiniciar.", file=sys.stderr, flush=True)
            # Fecha o navegador para liberar o perfil: com ele aberto, o login
            # não consegue usar a mesma pasta.
            threading.Thread(target=encerrar, name="orbis-sessao-encerrar", daemon=True).start()
    return dados, erro


# ---------------------------------------------------------------------------
# Linha de comando: login e teste
# ---------------------------------------------------------------------------

def login(url_inicial: str | None = None) -> int:
    """Abre um navegador visível no perfil da sessão e espera o usuário fechá-lo."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright não está instalado: pip install playwright && playwright install chromium")
        return 1
    destino = url_inicial or (f"{ezproxy_base()}/login" if ezproxy_base() else PORTAL_CAPES)
    pasta_perfil().mkdir(parents=True, exist_ok=True)
    print(f"Perfil: {pasta_perfil()}")
    print(f"Abrindo {destino}")
    print("Faça o login (CAPES → Acesso CAFe, ou o EZproxy da biblioteca), confira que um artigo "
          "de assinatura abre, e FECHE A JANELA para gravar a sessão.")
    with sync_playwright() as pw:
        contexto = pw.chromium.launch_persistent_context(str(pasta_perfil()), headless=False)
        pagina = contexto.pages[0] if contexto.pages else contexto.new_page()
        pagina.goto(destino)
        contexto.wait_for_event("close", timeout=0)
    print("Sessão gravada. Ative no .env com ORBIS_SESSAO_NAVEGADOR=1.")
    return 0


def testar(doi: str, prazo: float = 60.0) -> int:
    os.environ.setdefault("ORBIS_SESSAO_NAVEGADOR", "1")
    if not perfil_existe():
        print("Nenhum login feito ainda. Rode primeiro: python sessao_navegador.py login")
        return 1
    inicio = time.monotonic()
    dados, erro = baixar_pdf(doi, prazo=prazo)
    encerrar()
    if dados:
        print(f"OK: PDF de {len(dados) // 1024} KB em {time.monotonic() - inicio:.1f} s")
        return 0
    print(f"Falhou: {erro} ({time.monotonic() - inicio:.1f} s)")
    return 2


def main(argv: list[str] | None = None) -> int:
    import argparse

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ambiente

    ambiente.carregar()
    ap = argparse.ArgumentParser(description="Sessão institucional (CAPES/CAFe ou EZproxy) no navegador.")
    sub = ap.add_subparsers(dest="comando", required=True)
    p_login = sub.add_parser("login", help="abre o navegador para você entrar; feche a janela ao terminar")
    p_login.add_argument("--url", help="página inicial (padrão: EZproxy configurado ou portal da CAPES)")
    p_testar = sub.add_parser("testar", help="tenta baixar um DOI pela sessão")
    p_testar.add_argument("doi")
    p_testar.add_argument("--prazo", type=float, default=60.0)
    args = ap.parse_args(argv)
    if args.comando == "login":
        return login(args.url)
    return testar(args.doi, args.prazo)


if __name__ == "__main__":
    sys.exit(main())
