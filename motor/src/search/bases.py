"""Registro das bases de busca disponíveis.

Cada base tem uma chave (usada em ``--bases``, nas seções ``[CHAVE]`` do
quary.txt e na API do backend), um rótulo exibido e a função de busca, que
recebe a query e devolve ``(total, dois, registros_sem_doi)``.
"""
from connectors.base_search import fetch_base_dois
from connectors.core import fetch_core_dois
from connectors.embase import fetch_embase_dois
from connectors.europepmc import fetch_europepmc_dois
from connectors.ieee import fetch_ieee_dois
from connectors.lilacs import fetch_lilacs_dois
from connectors.oasisbr import fetch_oasisbr_dois
from connectors.openalex import fetch_openalex_dois
from connectors.pubmed import fetch_pubmed_dois
from connectors.scopus import fetch_scopus_dois
from connectors.semantic_scholar import fetch_semantic_scholar_dois

# chave -> (rótulo, função)
BASES = {
    "PUBMED": ("PubMed", fetch_pubmed_dois),
    "EMBASE": ("Embase", fetch_embase_dois),
    "LILACS": ("LILACS", fetch_lilacs_dois),
    "EUROPEPMC": ("EuropePMC", fetch_europepmc_dois),
    "OPENALEX": ("OpenAlex", fetch_openalex_dois),
    "SEMANTICSCHOLAR": ("SemanticScholar", fetch_semantic_scholar_dois),
    "SCOPUS": ("Scopus", fetch_scopus_dois),
    "IEEE": ("IEEE", fetch_ieee_dois),
    "CORE": ("CORE", fetch_core_dois),
    "OASISBR": ("Oasisbr", fetch_oasisbr_dois),
    "BASE": ("BASE", fetch_base_dois),
}

LABEL_TO_KEY = {label.upper(): key for key, (label, _fn) in BASES.items()}


def resolve_base(name: str) -> str | None:
    """Aceita a chave ou o rótulo, sem diferenciar maiúsculas; devolve a chave."""
    key = (name or "").strip().upper().replace(" ", "").replace("-", "").replace("_", "")
    if key in BASES:
        return key
    return LABEL_TO_KEY.get(key)
