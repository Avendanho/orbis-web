import os
import yaml
from pathlib import Path
from pydantic import BaseModel, Field

# Onde ficam os dados (PDFs, banco, relatórios). Por padrão é a raiz do motor,
# mas `ORBIS_DATA_DIR` permite apontar para outro lugar — é o que mantém o
# acervo já baixado no seu diretório de origem, fora do repositório.
ROOT_DIR = Path(os.environ.get("ORBIS_DATA_DIR") or Path(__file__).resolve().parent.parent.parent)

class Config(BaseModel):
    pdf_dir: str = str(ROOT_DIR / "pdfs")
    output_dir: str = str(ROOT_DIR / "relatorio")
    db_dir: str = str(ROOT_DIR / "data")
    genetic_scope: str = "EXPANDED"
    workers: int = 4
    ocr_enabled: bool = True
    cache_enabled: bool = True
    confidence_threshold: str = "MODERATE"

def load_config(path: str = "config.yaml") -> Config:
    if not os.path.exists(path) and not os.path.isabs(path):
        # Não depender do cwd: procurar ao lado deste módulo
        candidate = Path(__file__).resolve().parent / path
        if candidate.exists():
            path = str(candidate)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            return Config(**data)
    return Config()

settings = load_config()
