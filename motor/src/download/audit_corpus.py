"""Audita se o que foi baixado é o que foi pedido.

O portão de identidade (``identity.py``) roda no momento do download e é a
primeira linha de defesa. Esta auditoria é a segunda, e responde a perguntas
que só fazem sentido sobre o acervo inteiro, depois do fato:

1. **Todo PDF corresponde a um DOI de alguma lista de entrada?** Um arquivo
   cujo DOI não consta em lista alguma entrou por engano — de uma execução
   antiga, de um teste, ou de uma resolução por título que achou outro artigo.
2. **O arquivo é mesmo o artigo?** O portão aceita por duas vias: o DOI
   impresso dentro do PDF (``doi_in_pdf``, forte) ou a correspondência dos
   metadados do registro de origem (``doi_in_record``, mais fraca). A segunda
   deixa passar material suplementar, formulários editoriais e cadernos de
   resumos, que trazem os metadados certos sem serem o artigo.
3. **O PDF é legível?** Um arquivo só de imagem, truncado ou corrompido conta
   como baixado e não serve para a triagem.

Nada é apagado: a saída é um relatório para decisão humana. Num fluxo cujo
produto é uma contagem auditável, remover arquivo automaticamente é pior do
que apontá-lo.

Uso::

    python audit_corpus.py                         # usa os caminhos padrão
    python audit_corpus.py --pdfs pdfs --listas "src/download/data/DOI's.txt"
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from identity import normalize_doi

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s,;\"']+", re.I)

# Marcas de que o arquivo é outra coisa que não o artigo. São procuradas só no
# começo do documento: no meio do texto, "supplementary table" é uma referência
# legítima que o artigo faz ao próprio material extra.
NAO_E_ARTIGO = [
    (re.compile(r"^\s*(supplementary|supplemental)\s+(table|figure|material|information|data|note)", re.I | re.M),
     "material suplementar, não o artigo"),
    (re.compile(r"reporting summary|life sciences reporting", re.I),
     "formulário editorial, não o artigo"),
    (re.compile(r"^\s*(author index|abstracts?\s+by\s+(number|author)|index of abstracts)", re.I | re.M),
     "índice de resumos, não o artigo"),
]

# Quantas linhas do topo são inspecionadas à procura das marcas acima.
LINHAS_CABECALHO = 25
MIN_TEXTO = 50  # abaixo disso o PDF não tem texto extraível


def dois_de(caminho: Path) -> set[str]:
    """Todos os DOIs de um arquivo de lista (.txt ou .csv)."""
    texto = caminho.read_text(encoding="utf-8", errors="replace")
    brutos: set[str] = set()
    if caminho.suffix.lower() == ".csv":
        for linha in csv.reader(texto.splitlines()):
            for campo in linha:
                brutos.update(DOI_RE.findall(campo or ""))
    else:
        brutos.update(DOI_RE.findall(texto))
    return {d for d in (normalize_doi(b.rstrip(".,;")) for b in brutos) if d}


def _abrir(pdf: Path):
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - ambiente sem pymupdf
        import fitz as pymupdf
    return pymupdf.open(pdf)


def inspecionar_conteudo(pdf: Path) -> tuple[int, str | None]:
    """(páginas, problema). ``problema`` é None quando o PDF parece o artigo."""
    try:
        doc = _abrir(pdf)
        paginas = doc.page_count
        texto = "\n".join(doc[i].get_text() for i in range(min(2, paginas)))
    except Exception as exc:
        return 0, f"ilegível ({type(exc).__name__})"

    if len(texto.strip()) < MIN_TEXTO:
        return paginas, "sem texto extraível (só imagem, truncado ou corrompido)"

    cabecalho = "\n".join(l for l in texto.split("\n")[:LINHAS_CABECALHO] if l.strip())
    for rx, rotulo in NAO_E_ARTIGO:
        if rx.search(cabecalho):
            return paginas, rotulo
    return paginas, None


def auditar(dir_pdfs: Path, autorizados: set[str], *, conteudo: bool = True) -> dict:
    achados: dict[str, list] = {
        "fora_da_lista": [], "sem_sidecar": [], "nao_validado": [],
        "doi_divergente": [], "conteudo_suspeito": [],
    }
    metodos: Counter[str] = Counter()
    por_doi: dict[str, list[str]] = {}
    por_hash: dict[str, list[str]] = {}
    total = 0

    for pdf in sorted(dir_pdfs.glob("*.pdf")):
        total += 1
        side = pdf.with_name(pdf.name + ".identity.json")
        if not side.exists():
            achados["sem_sidecar"].append(pdf.name)
            continue
        try:
            rec = json.loads(side.read_text(encoding="utf-8"))
        except Exception as exc:
            achados["sem_sidecar"].append(f"{pdf.name} (registro ilegível: {exc})")
            continue

        metodos[rec.get("validation_method") or "?"] += 1
        esperado = normalize_doi(str((rec.get("expected") or {}).get("doi") or ""))
        detectado = normalize_doi(str(rec.get("detected_doi") or ""))

        if not rec.get("identity_validated"):
            achados["nao_validado"].append((pdf.name, rec.get("reason")))
        if esperado:
            por_doi.setdefault(esperado, []).append(pdf.name)
            if autorizados and esperado not in autorizados:
                achados["fora_da_lista"].append((pdf.name, esperado, detectado))
        else:
            achados["fora_da_lista"].append((pdf.name, "(sem DOI pedido)", detectado))
        if esperado and detectado and esperado != detectado:
            achados["doi_divergente"].append((pdf.name, esperado, detectado))
        if rec.get("sha256"):
            por_hash.setdefault(rec["sha256"], []).append(pdf.name)

        if conteudo:
            paginas, problema = inspecionar_conteudo(pdf)
            if problema:
                achados["conteudo_suspeito"].append(
                    (pdf.name, paginas, problema, rec.get("validation_method")))

    achados["doi_duplicado"] = [(d, a) for d, a in por_doi.items() if len(a) > 1]
    achados["conteudo_duplicado"] = [(h, a) for h, a in por_hash.items() if len(a) > 1]
    return {"total": total, "metodos": metodos, "achados": achados}


def imprimir(nome: str, res: dict) -> int:
    a = res["achados"]
    problemas = (len(a["fora_da_lista"]) + len(a["sem_sidecar"]) + len(a["nao_validado"])
                 + len(a["doi_divergente"]) + len(a["conteudo_suspeito"])
                 + len(a["doi_duplicado"]) + len(a["conteudo_duplicado"]))

    print("=" * 76)
    print(f"{nome}  —  {res['total']} PDFs")
    print("=" * 76)
    for metodo, n in res["metodos"].most_common():
        forca = "DOI impresso no PDF (forte)" if metodo == "doi_in_pdf" else "metadados do registro (mais fraca)"
        print(f"  {n:5}  confirmados por {metodo:15} {forca}")

    rotulos = [
        ("fora_da_lista", "PDF cujo DOI não consta em lista alguma"),
        ("conteudo_suspeito", "PDF que provavelmente não é o artigo"),
        ("doi_divergente", "DOI do arquivo difere do pedido"),
        ("sem_sidecar", "PDF sem registro de identidade"),
        ("nao_validado", "identidade não validada"),
        ("doi_duplicado", "mesmo DOI em mais de um arquivo"),
        ("conteudo_duplicado", "arquivos com conteúdo idêntico"),
    ]
    print()
    for chave, rotulo in rotulos:
        n = len(a[chave])
        print(f"  {'❌' if n else '✅'} {rotulo:44} {n}")

    for chave, rotulo in rotulos:
        if not a[chave]:
            continue
        print(f"\n  --- {rotulo} ---")
        for item in a[chave][:40]:
            if chave == "conteudo_suspeito":
                arq, pg, motivo, metodo = item
                print(f"    {motivo:52} {pg:4}pg [{metodo}]")
                print(f"       {arq}")
            elif chave in ("fora_da_lista", "doi_divergente"):
                arq, esp, det = item
                print(f"    pedido={esp}  no_pdf={det or '?'}")
                print(f"       {arq}")
            elif chave in ("doi_duplicado", "conteudo_duplicado"):
                chave_dup, arqs = item
                print(f"    {str(chave_dup)[:40]}: {len(arqs)} arquivos")
                for x in arqs[:3]:
                    print(f"       {x}")
            else:
                print(f"    {item}")
    print()
    return problemas


def main() -> int:
    raiz = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description="Audita a procedência do acervo de PDFs.")
    ap.add_argument("--pdfs", nargs="*", default=["pdfs", "src/download/pdfs"],
                    help="diretórios de PDFs a auditar")
    ap.add_argument("--listas", nargs="*", default=[
        "lista_doi_para_script.csv", "src/download/data/DOI's.txt",
        "src/search/output/dois_extraidos.txt"],
        help="arquivos com os DOIs autorizados")
    ap.add_argument("--sem-conteudo", action="store_true",
                    help="não abrir os PDFs (mais rápido; só confere procedência)")
    args = ap.parse_args()

    autorizados: set[str] = set()
    print("DOIs autorizados:")
    for nome in args.listas:
        p = raiz / nome
        if not p.exists():
            print(f"  (ausente) {nome}")
            continue
        s = dois_de(p)
        autorizados |= s
        print(f"  {len(s):5}  {nome}")
    print(f"  {len(autorizados):5}  TOTAL (união)\n")

    problemas = 0
    for nome in args.pdfs:
        d = raiz / nome
        if not d.exists():
            continue
        problemas += imprimir(f"{nome}/", auditar(d, autorizados, conteudo=not args.sem_conteudo))

    print("=" * 76)
    if problemas:
        print(f"{problemas} ponto(s) merecem conferência manual. Nada foi apagado.")
    else:
        print("Nenhum problema encontrado.")
    return 1 if problemas else 0


if __name__ == "__main__":
    raise SystemExit(main())
