#!/usr/bin/env python3
"""
Busca título, autores, resumo e status de acesso aberto para uma lista de DOIs,
usando as APIs gratuitas do CrossRef e da Unpaywall.

USO:
    pip install requests
    python buscar_metadados_doi.py lista_doi_para_script.csv saida.csv seu-email@exemplo.com

O CSV de entrada precisa ter duas colunas: "arquivo" e "doi".
O e-mail é exigido pela Unpaywall (uso educado/identificação da API, é gratuito e não recebe spam).
"""

import sys
import csv
import time
import re
import requests

CROSSREF_URL = "https://api.crossref.org/works/{doi}"
UNPAYWALL_URL = "https://api.unpaywall.org/v2/{doi}"


def limpar_abstract(raw):
    """CrossRef às vezes devolve o abstract com tags JATS (<jats:p>...). Remove as tags."""
    if not raw:
        return ""
    texto = re.sub(r"<[^>]+>", " ", raw)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def buscar_crossref(doi):
    try:
        r = requests.get(CROSSREF_URL.format(doi=doi), timeout=15,
                          headers={"User-Agent": "mailto:script@example.com"})
        if r.status_code != 200:
            return None
        msg = r.json().get("message", {})
        titulo = msg.get("title", [""])[0] if msg.get("title") else ""
        autores_list = msg.get("author", [])
        autores = ", ".join(
            f"{a.get('given','')} {a.get('family','')}".strip()
            for a in autores_list
        )
        periodico = msg.get("container-title", [""])[0] if msg.get("container-title") else ""
        ano = ""
        for campo in ("published-print", "published-online", "published", "issued"):
            if campo in msg and "date-parts" in msg[campo]:
                partes = msg[campo]["date-parts"][0]
                if partes:
                    ano = str(partes[0])
                    break
        resumo = limpar_abstract(msg.get("abstract", ""))
        tipo = msg.get("type", "")
        return {
            "titulo_en": titulo,
            "autores": autores,
            "periodico": periodico,
            "ano": ano,
            "resumo": resumo,
            "tipo_crossref": tipo,
        }
    except Exception as e:
        print(f"  [crossref erro] {doi}: {e}")
        return None


def buscar_unpaywall(doi, email):
    try:
        r = requests.get(UNPAYWALL_URL.format(doi=doi), params={"email": email}, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        is_oa = data.get("is_oa", False)
        best = data.get("best_oa_location") or {}
        pdf_url = best.get("url_for_pdf") or best.get("url") or ""
        return {"acesso_aberto": is_oa, "link_pdf": pdf_url}
    except Exception as e:
        print(f"  [unpaywall erro] {doi}: {e}")
        return None


def main():
    if len(sys.argv) < 4:
        print("Uso: python buscar_metadados_doi.py entrada.csv saida.csv seu-email@exemplo.com")
        sys.exit(1)

    caminho_entrada, caminho_saida, email = sys.argv[1], sys.argv[2], sys.argv[3]

    with open(caminho_entrada, encoding="utf-8") as f:
        linhas = list(csv.DictReader(f))

    print(f"{len(linhas)} DOIs para processar...")

    resultados = []
    for i, linha in enumerate(linhas, start=1):
        arquivo = linha["arquivo"]
        doi = linha["doi"].strip()
        print(f"[{i}/{len(linhas)}] {arquivo} -> {doi}")

        cr = buscar_crossref(doi) or {}
        up = buscar_unpaywall(doi, email) or {}

        resultados.append({
            "arquivo": arquivo,
            "doi": doi,
            "titulo_en": cr.get("titulo_en", ""),
            "autores": cr.get("autores", ""),
            "periodico": cr.get("periodico", ""),
            "ano": cr.get("ano", ""),
            "resumo": cr.get("resumo", ""),
            "tipo_crossref": cr.get("tipo_crossref", ""),
            "acesso_aberto": up.get("acesso_aberto", ""),
            "link_pdf_aberto": up.get("link_pdf", ""),
        })

        # Ritmo educado para não sobrecarregar as APIs gratuitas
        time.sleep(0.3)

    campos = ["arquivo", "doi", "titulo_en", "autores", "periodico", "ano",
              "resumo", "tipo_crossref", "acesso_aberto", "link_pdf_aberto"]
    with open(caminho_saida, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(resultados)

    print(f"\nPronto! Resultados salvos em: {caminho_saida}")


if __name__ == "__main__":
    main()
