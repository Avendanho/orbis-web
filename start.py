#!/usr/bin/env python3
"""Sobe o ORBIS e, junto, o motor de recuperação — em Windows, macOS ou Linux.

O que este script faz, em ordem:

1. confere o Node (o ORBIS exige 22.13 ou mais novo) e instala as dependências
   web se faltarem;
2. cria ou atualiza as tabelas do banco local a partir de `drizzle/`;
3. prepara o ambiente Python do motor, se ainda não existir;
4. sobe o serviço do motor em 127.0.0.1:8900;
5. sobe o ORBIS e abre o navegador.

O motor é opcional por desenho: se o Python falhar, o ORBIS sobe assim mesmo,
só sem as capacidades que dependem de biblioteca nativa (baixar pela cadeia
completa de fontes, ler o texto de dentro do PDF e conferir identidade pelo
conteúdo).

    python start.py              sobe os dois   (Windows: py start.py)
    python start.py --so-orbis   sobe só a interface
    python start.py --so-motor   sobe só o serviço do motor
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
MOTOR = RAIZ / "motor"
SERVICO = RAIZ / "servico-python"
PORTA_MOTOR = 8900
PORTA_ORBIS = 5173
NODE_MINIMO = (22, 13)
PYTHON_MINIMO = (3, 10)
WINDOWS = os.name == "nt"

processos: list[subprocess.Popen] = []

if WINDOWS:
    os.system("")  # liga as cores ANSI no console do Windows
# O corepack pergunta antes de baixar o pnpm; sem terminal interativo, travaria.
os.environ.setdefault("COREPACK_ENABLE_DOWNLOAD_PROMPT", "0")
os.environ.setdefault("WRANGLER_SEND_METRICS", "false")


def titulo(texto: str) -> None:
    print(f"\n\033[1;36m{texto}\033[0m")


def aviso(texto: str) -> None:
    print(f"  \033[33m{texto}\033[0m")


def erro(texto: str) -> None:
    print(f"  \033[31m{texto}\033[0m")


def ok(texto: str) -> None:
    print(f"  \033[32m{texto}\033[0m")


def _nvm() -> Path | None:
    nvm = Path.home() / ".nvm" / "nvm.sh"
    return nvm if not WINDOWS and nvm.exists() else None


def _node(args: list[str]) -> list[str]:
    """Monta o comando de um executável do Node (node, corepack, pnpm, npx).

    Com nvm (Linux/macOS), o Node certo só existe dentro de um shell que
    carregou `nvm.sh` — daí o rodeio. Sem nvm, e sempre no Windows, usa o que
    está no PATH (no Windows, `shutil.which` acha o `.cmd` do corepack/pnpm).
    """
    nvm = _nvm()
    if nvm:
        return ["bash", "-lc", f'export NVM_DIR="$HOME/.nvm"; . "{nvm}"; ' + " ".join(shlex.quote(a) for a in args)]
    return [shutil.which(args[0]) or args[0], *args[1:]]


def _funciona(args: list[str]) -> bool:
    try:
        return subprocess.run(_node(args), capture_output=True, timeout=120).returncode == 0
    except Exception:
        return False


_pnpm_cache: list[str] | None = None


def _pnpm(args: list[str]) -> list[str]:
    """pnpm na versão do package.json: pelo corepack quando há, senão o do PATH, senão via npx."""
    global _pnpm_cache
    if _pnpm_cache is None:
        if _funciona(["corepack", "pnpm", "--version"]):
            _pnpm_cache = ["corepack", "pnpm"]
        elif _funciona(["pnpm", "--version"]):
            _pnpm_cache = ["pnpm"]
        else:
            _pnpm_cache = ["npx", "--yes", "pnpm@11.25.0"]
    return _node([*_pnpm_cache, *args])


def _popen(cmd: list[str], **kw) -> subprocess.Popen:
    # Grupo próprio: ao encerrar, derruba o processo e tudo que ele abriu
    # (pnpm → node → vite; python → uvicorn).
    if WINDOWS:
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    p = subprocess.Popen(cmd, **kw)
    processos.append(p)
    return p


def _parar(p: subprocess.Popen) -> None:
    if p.poll() is not None:
        return
    try:
        if WINDOWS:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        else:
            os.killpg(p.pid, signal.SIGTERM)
    except Exception:
        p.terminate()


def _aguardar_para_sempre() -> None:
    while True:
        time.sleep(1)


def versao_node() -> tuple[int, ...] | None:
    try:
        r = subprocess.run(_node(["node", "--version"]), capture_output=True, text=True, timeout=60)
        bruto = (r.stdout or "").strip().lstrip("v")
        return tuple(int(x) for x in bruto.split(".")[:2]) if bruto else None
    except Exception:
        return None


def _como_instalar_node() -> None:
    if WINDOWS:
        aviso("Instale o Node 22 (LTS) em https://nodejs.org e abra um novo terminal.")
    elif sys.platform == "darwin":
        aviso("Instale o Node 22 em https://nodejs.org (ou: brew install node@22).")
    else:
        aviso("Instale com:  curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash")
        aviso("depois:       exec $SHELL && nvm install 22")


def preparar_orbis() -> bool:
    titulo("ORBIS (interface)")
    v = versao_node()
    if not v:
        erro("Node não encontrado.")
        _como_instalar_node()
        return False
    if v < NODE_MINIMO:
        erro(f"Node {'.'.join(map(str, v))} é antigo demais; o projeto exige {'.'.join(map(str, NODE_MINIMO))}+.")
        _como_instalar_node()
        return False
    ok(f"Node {'.'.join(map(str, v))}")

    if not (RAIZ / "node_modules").is_dir():
        aviso("Instalando dependências web (uma vez só; pode levar alguns minutos)…")
        if subprocess.run(_pnpm(["install"]), cwd=RAIZ).returncode != 0:
            erro("pnpm install falhou.")
            return False
    ok("dependências web prontas")
    return preparar_banco()


def _d1(extra: list[str], config: Path) -> subprocess.CompletedProcess:
    wrangler = RAIZ / "node_modules" / "wrangler" / "bin" / "wrangler.js"
    return subprocess.run(
        _node(["node", str(wrangler), "d1", "execute", "site-creator-d1", "--local",
               "--persist-to", str(RAIZ / ".wrangler" / "state"), "-c", str(config), *extra]),
        cwd=RAIZ, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def preparar_banco() -> bool:
    """Cria as tabelas do banco local e aplica as migrações novas de `drizzle/`.

    O servidor de desenvolvimento usa um banco D1 local (em `.wrangler/state`)
    e não aplica migrações sozinho: sem isto, uma instalação nova responde
    "no such table: projects". A tabela `orbis_migrations` lembra o que já foi
    aplicado, então rodar de novo é seguro.
    """
    config = RAIZ / ".wrangler" / "orbis-d1.json"
    config.parent.mkdir(exist_ok=True)
    # Mesmo nome e id que o vite.config.ts usa: é o mesmo arquivo de banco.
    config.write_text(json.dumps({
        "name": "orbis-local", "compatibility_date": "2026-05-15",
        "d1_databases": [{"binding": "DB", "database_name": "site-creator-d1",
                          "database_id": "00000000-0000-4000-8000-000000000000"}],
    }), encoding="utf-8")

    def consulta(sql: str) -> list[dict]:
        r = _d1(["--json", "--command", sql], config)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip()[-800:])
        return json.loads(r.stdout)[0].get("results", [])

    try:
        tabelas = {t["name"] for t in consulta("SELECT name FROM sqlite_master WHERE type='table'")}
        arquivos = sorted((RAIZ / "drizzle").glob("*.sql"))
        if "orbis_migrations" not in tabelas:
            consulta("CREATE TABLE orbis_migrations(name TEXT PRIMARY KEY, applied TEXT NOT NULL)")
            # Banco criado antes deste controle: as tabelas já estão lá.
            if "projects" in tabelas:
                for f in arquivos:
                    consulta(f"INSERT INTO orbis_migrations VALUES('{f.name}', datetime('now'))")
        feitas = {t["name"] for t in consulta("SELECT name FROM orbis_migrations")}
        novas = [f for f in arquivos if f.name not in feitas]
        for f in novas:
            r = _d1(["--file", str(f)], config)
            if r.returncode != 0:
                raise RuntimeError(f"{f.name}: " + (r.stderr or r.stdout).strip()[-800:])
            consulta(f"INSERT INTO orbis_migrations VALUES('{f.name}', datetime('now'))")
    except Exception as exc:
        erro(f"não foi possível preparar o banco local: {exc}")
        return False
    ok(f"banco local pronto{f' ({len(novas)} migração(ões) aplicada(s))' if novas else ''}")
    return True


def preparar_motor() -> Path | None:
    titulo("Motor (Python)")
    if sys.version_info[:2] < PYTHON_MINIMO:
        erro(f"Python {sys.version.split()[0]} é antigo demais; o motor exige {'.'.join(map(str, PYTHON_MINIMO))}+.")
        return None
    venv = MOTOR / ".venv"
    python = venv / ("Scripts/python.exe" if WINDOWS else "bin/python")
    if not python.exists():
        aviso("Criando o ambiente do motor (uma vez só)…")
        if subprocess.run([sys.executable, "-m", "venv", str(venv)]).returncode != 0:
            erro("não foi possível criar o ambiente virtual.")
            if not WINDOWS and sys.platform != "darwin":
                aviso("No Ubuntu/Debian: sudo apt install python3-venv")
            return None
        aviso("Instalando dependências do motor (pode levar alguns minutos)…")
        pip = [str(python), "-m", "pip", "install", "-q"]
        subprocess.run([*pip, "--upgrade", "pip"])
        if subprocess.run([*pip, "-r", str(MOTOR / "requirements.txt")]).returncode != 0:
            erro("a instalação das dependências falhou.")
            shutil.rmtree(venv, ignore_errors=True)  # na próxima vez, tenta do zero
            return None
    ok("ambiente do motor pronto")

    if not (MOTOR / ".env").exists() and (MOTOR / ".env.example").exists():
        shutil.copy(MOTOR / ".env.example", MOTOR / ".env")
        aviso("criei motor/.env a partir do exemplo — preencha as chaves que tiver")
    return python


def esperar(url: str, segundos: int = 40) -> bool:
    limite = time.time() + segundos
    while time.time() < limite:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


def subir_motor(python: Path) -> bool:
    titulo(f"Subindo o motor em http://127.0.0.1:{PORTA_MOTOR}")
    ambiente = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"}
    _popen(
        [str(python), "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(PORTA_MOTOR), "--log-level", "warning"],
        cwd=SERVICO, env=ambiente,
    )
    if esperar(f"http://127.0.0.1:{PORTA_MOTOR}/saude"):
        ok("motor no ar")
        return True
    aviso("o motor não respondeu; o ORBIS segue sem ele")
    return False


def subir_orbis(com_motor: bool) -> None:
    titulo(f"Subindo o ORBIS em http://localhost:{PORTA_ORBIS}")
    ambiente = {**os.environ}
    if com_motor:
        ambiente["ORBIS_ENGINE_URL"] = f"http://127.0.0.1:{PORTA_MOTOR}"
    _popen(_pnpm(["dev"]), cwd=RAIZ, env=ambiente)

    def abrir():
        if esperar(f"http://localhost:{PORTA_ORBIS}", 120):
            webbrowser.open(f"http://localhost:{PORTA_ORBIS}")
    threading.Thread(target=abrir, daemon=True).start()


def encerrar(*_):
    print("\n\033[33mEncerrando…\033[0m")
    for p in processos:
        _parar(p)
    for p in processos:
        try:
            p.wait(timeout=10)
        except Exception:
            p.kill()
    sys.exit(0)


def main() -> int:
    ap = argparse.ArgumentParser(description="Sobe o ORBIS e o motor de recuperação.")
    ap.add_argument("--so-orbis", action="store_true", help="sobe apenas a interface")
    ap.add_argument("--so-motor", action="store_true", help="sobe apenas o serviço do motor")
    args = ap.parse_args()

    signal.signal(signal.SIGINT, encerrar)
    signal.signal(signal.SIGTERM, encerrar)

    print("\n\033[1;36m═══ ORBIS — revisão sistemática ═══\033[0m")

    try:
        com_motor = False
        if not args.so_orbis:
            python = preparar_motor()
            if python:
                com_motor = subir_motor(python)
            if args.so_motor:
                if not com_motor:
                    return 1
                print("\nCtrl+C para encerrar.")
                _aguardar_para_sempre()

        if not preparar_orbis():
            if com_motor:
                aviso(f"o motor continua em http://127.0.0.1:{PORTA_MOTOR}; Ctrl+C encerra")
                _aguardar_para_sempre()
            return 1

        subir_orbis(com_motor)
        print(f"\n  ORBIS:  http://localhost:{PORTA_ORBIS}")
        if com_motor:
            print(f"  Motor:  http://127.0.0.1:{PORTA_MOTOR}/saude")
        print("\nCtrl+C para encerrar.\n")

        while True:
            for p in processos:
                if p.poll() is not None:
                    erro("um dos serviços encerrou.")
                    encerrar()
            time.sleep(1)
    except KeyboardInterrupt:
        encerrar()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
