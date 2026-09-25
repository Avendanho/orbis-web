import sqlite3
import json
from pathlib import Path
from config import settings

# Colunas acrescentadas depois da primeira versão. São aplicadas por migração
# em vez de recriação: quem já rodou a versão anterior tem pareceres gravados
# e não pode ser obrigado a apagar o banco para atualizar.
_COLUNAS_EXTRA = {
    "failure_reason": "TEXT",   # por que a análise não pôde ser feita
    "provider": "TEXT",         # qual modelo emitiu o parecer
    "protocol_hash": "TEXT",    # sob qual versão do protocolo
}


def get_db():
    db_path = Path(settings.db_dir) / "analysis.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL mode allows concurrent readers + one writer without blocking.
    # Prevents "database is locked" errors when running 10 parallel workers.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")  # safe with WAL; faster than FULL
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS articles (
            article_id TEXT PRIMARY KEY,
            filename TEXT,
            hash TEXT,
            status TEXT,
            decision TEXT,
            exclusion_code TEXT,
            confidence TEXT,
            analysis_json TEXT,
            failure_reason TEXT,
            provider TEXT,
            protocol_hash TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    existentes = {row["name"] for row in c.execute("PRAGMA table_info(articles)")}
    for coluna, tipo in _COLUNAS_EXTRA.items():
        if coluna not in existentes:
            c.execute(f"ALTER TABLE articles ADD COLUMN {coluna} {tipo}")
    conn.commit()
    conn.close()


def save_analysis(article_id: str, filename: str, file_hash: str, analysis: dict,
                  *, provider: str = None, protocol_hash: str = None):
    """Grava um parecer de triagem (status COMPLETED).

    Limpa `failure_reason`: se o artigo falhou numa execução anterior e agora
    foi analisado, o registro da falha não pode sobreviver ao lado do parecer.
    """
    decision = analysis.get("decision", "REVISÃO MANUAL")
    exclusion_code = analysis.get("exclusion_code")
    confidence = analysis.get("confidence")

    conn = get_db()
    conn.execute('''
        INSERT INTO articles (article_id, filename, hash, status, decision,
                              exclusion_code, confidence, analysis_json,
                              failure_reason, provider, protocol_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
        ON CONFLICT(article_id) DO UPDATE SET
            filename=excluded.filename,
            hash=excluded.hash,
            status=excluded.status,
            decision=excluded.decision,
            exclusion_code=excluded.exclusion_code,
            confidence=excluded.confidence,
            analysis_json=excluded.analysis_json,
            failure_reason=NULL,
            provider=excluded.provider,
            protocol_hash=excluded.protocol_hash,
            updated_at=CURRENT_TIMESTAMP
    ''', (article_id, filename, file_hash, "COMPLETED", decision, exclusion_code,
          confidence, json.dumps(analysis, ensure_ascii=False), provider, protocol_hash))
    conn.commit()
    conn.close()


def save_failure(article_id: str, filename: str, file_hash: str, reason: str,
                 *, detail: str = None, provider: str = None, protocol_hash: str = None):
    """Grava uma falha técnica (status FAILED, sem decisão).

    O artigo não foi triado — não foi incluído, não foi excluído e não foi
    mandado para revisão manual. `decision` fica NULL de propósito, para que
    nenhuma contagem o confunda com um parecer, e o registro existe para que
    ele apareça no relatório em vez de sumir.
    """
    payload = {"failure_reason": reason}
    if detail:
        payload["detail"] = detail

    conn = get_db()
    conn.execute('''
        INSERT INTO articles (article_id, filename, hash, status, decision,
                              exclusion_code, confidence, analysis_json,
                              failure_reason, provider, protocol_hash)
        VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?)
        ON CONFLICT(article_id) DO UPDATE SET
            filename=excluded.filename,
            hash=excluded.hash,
            status=excluded.status,
            decision=NULL,
            exclusion_code=NULL,
            confidence=NULL,
            analysis_json=excluded.analysis_json,
            failure_reason=excluded.failure_reason,
            provider=excluded.provider,
            protocol_hash=excluded.protocol_hash,
            updated_at=CURRENT_TIMESTAMP
    ''', (article_id, filename, file_hash, "FAILED",
          json.dumps(payload, ensure_ascii=False), reason, provider, protocol_hash))
    conn.commit()
    conn.close()


def get_article(article_id: str) -> dict:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM articles WHERE article_id = ?", (article_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None
