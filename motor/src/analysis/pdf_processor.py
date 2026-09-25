import os
import json
import hashlib
import signal
import threading
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
import concurrent.futures
import pymupdf4llm
from config import settings

PDF_TIMEOUT_SECONDS = 60


class _PdfTimeout(BaseException):
    """Levantada pelo SIGALRM. Herda de BaseException para não ser engolida
    pelos `except Exception` dentro da extração."""

def get_hash(filepath: str) -> str:
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

def _process_pdf_worker(filepath: str, article_id: str) -> Dict[str, Any]:
    out_dir = Path(settings.db_dir) / "extracted" / article_id
    out_dir.mkdir(parents=True, exist_ok=True)
    
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    
    text = ""
    extraction_error = False
    try:
        # Extração em Markdown, com imagens.
        text = pymupdf4llm.to_markdown(filepath, write_images=True, image_path=str(images_dir))
    except Exception as e:
        # A escrita de imagens pode falhar (ex.: caminhos com espaços/acentos que
        # o MuPDF não consegue abrir). O texto é o essencial para a triagem, então
        # tentamos novamente sem imagens antes de desistir.
        try:
            text = pymupdf4llm.to_markdown(filepath, write_images=False)
        except Exception as e2:
            text = f"Erro na extração PyMuPDF4LLM: {str(e2)} (falha inicial: {str(e)})"
            extraction_error = True
            
    if not text.strip():
        text = "Não foi possível extrair o texto do PDF."
        
    with open(out_dir / "content.md", "w", encoding="utf-8") as f:
        f.write(text)
        
    quality = "HIGH" if len(text) > 1000 else "LOW"
    # Antes: `"Erro" in text` marcava como ERROR qualquer artigo contendo a
    # palavra "Erro" (ex.: "Erro padrão"). Usa-se o sinalizador explícito.
    if extraction_error:
        quality = "ERROR"
        
    meta = {
        "article_id": article_id,
        "filename": os.path.basename(filepath),
        "text_quality": quality,
        "hash": get_hash(filepath)
    }
    
    with open(out_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
        
    return meta

def _write_timeout_meta(filepath: str, article_id: str, timeout: int) -> Dict[str, Any]:
    out_dir = Path(settings.db_dir) / "extracted" / article_id
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "content.md", "w", encoding="utf-8") as f:
        f.write(f"Erro: Timeout ao processar o PDF. Excedeu {timeout} segundos.")

    meta = {
        "article_id": article_id,
        "filename": os.path.basename(filepath),
        "text_quality": "ERROR",
        "hash": get_hash(filepath)
    }
    with open(out_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return meta


def process_pdf(filepath: str, article_id: str, timeout: int = PDF_TIMEOUT_SECONDS) -> Dict[str, Any]:
    """Extrai texto e imagens do PDF com suporte a timeout para evitar travamentos."""
    use_alarm = hasattr(signal, "SIGALRM") and threading.current_thread() is threading.main_thread()
    if use_alarm:
        # POSIX, thread principal (ex.: worker de ProcessPoolExecutor): o alarme
        # interrompe a extração de verdade, sem deixar thread órfã rodando.
        def _on_alarm(signum, frame):
            raise _PdfTimeout()

        previous = signal.signal(signal.SIGALRM, _on_alarm)
        signal.alarm(timeout)
        try:
            return _process_pdf_worker(filepath, article_id)
        except _PdfTimeout:
            return _write_timeout_meta(filepath, article_id, timeout)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)

    # Fallback (Windows ou chamada fora da thread principal). Não usar
    # `with ThreadPoolExecutor`: o __exit__ espera a thread terminar, o que
    # anulava o timeout e travava o scan no PDF problemático.
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_process_pdf_worker, filepath, article_id)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        return _write_timeout_meta(filepath, article_id, timeout)
    finally:
        executor.shutdown(wait=False)


def scan_one(pdf_path: str) -> Tuple[str, Optional[str]]:
    """Processa um PDF (usado pelo ProcessPoolExecutor do comando `scan`).
    Retorna (caminho, mensagem_de_erro_ou_None)."""
    try:
        process_pdf(pdf_path, Path(pdf_path).stem)
        return pdf_path, None
    except Exception as e:
        return pdf_path, str(e)
