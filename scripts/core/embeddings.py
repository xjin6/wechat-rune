"""
Embedding module: convert messages to vectors using sentence-transformers and store them in local SQLite.
Supports semantic search as a complement to keyword search (RAG long-term memory layer).
"""

import os, sqlite3, json, time
import numpy as np
from config import BASE_DIR

VECTOR_DB = os.path.join(BASE_DIR, "db", "vectors.db")
MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

_model = None


def _get_model():
    global _model
    if _model is None:
        print("[Embedding] Loading embedding model...", flush=True)
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
        print("[Embedding] Model loaded", flush=True)
    return _model


def _get_db():
    os.makedirs(os.path.dirname(VECTOR_DB), exist_ok=True)
    conn = sqlite3.connect(VECTOR_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS message_vectors (
            table_name  TEXT,
            local_id    INTEGER,
            embedding   BLOB,
            text        TEXT,
            sender      TEXT,
            create_time INTEGER,
            PRIMARY KEY (table_name, local_id)
        )
    """)
    conn.commit()
    return conn


def embed(text: str) -> np.ndarray:
    """Convert text to a vector"""
    model = _get_model()
    return model.encode(text, normalize_embeddings=True)


def store(table: str, local_id: int, text: str,
          sender: str, create_time: int):
    """Vectorize a message and store it in the DB (background call, does not block the main flow)"""
    try:
        if not text or text.startswith("<") or len(text.strip()) < 3:
            return
        vec = embed(text)
        blob = vec.astype(np.float32).tobytes()
        conn = _get_db()
        conn.execute(
            "INSERT OR REPLACE INTO message_vectors "
            "(table_name, local_id, embedding, text, sender, create_time) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (table, local_id, blob, text, sender, create_time)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Embedding] Storage failed: {e}", flush=True)


def semantic_search(table: str, query: str,
                    top_k: int = 15,
                    since_ts: int = 0) -> list[dict]:
    """
    Semantic search: return the most similar messages, each containing text/sender/create_time/score.
    """
    try:
        conn = _get_db()
        where = "table_name = ?"
        params = [table]
        if since_ts:
            where += " AND create_time >= ?"
            params.append(since_ts)

        rows = conn.execute(
            f"SELECT local_id, embedding, text, sender, create_time "
            f"FROM message_vectors WHERE {where}",
            params
        ).fetchall()
        conn.close()

        if not rows:
            return []

        query_vec = embed(query).astype(np.float32)
        results = []
        for row in rows:
            vec = np.frombuffer(row[1], dtype=np.float32)
            score = float(np.dot(query_vec, vec))  # Already normalized; dot product = cosine similarity
            results.append({
                "local_id":    row[0],
                "text":        row[2],
                "sender":      row[3],
                "create_time": row[4],
                "score":       score,
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    except Exception as e:
        print(f"[Embedding] Search failed: {e}", flush=True)
        return []


def count(table: str) -> int:
    """Return the number of vectorized messages for this conversation"""
    try:
        conn = _get_db()
        r = conn.execute(
            "SELECT COUNT(*) FROM message_vectors WHERE table_name = ?",
            (table,)
        ).fetchone()
        conn.close()
        return r[0] if r else 0
    except Exception:
        return 0
