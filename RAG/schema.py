"""
LiteSQL（SQLite）Schema + ChromaDB 初始化。

SQLite 只存文档路径与 LLM 生成的元数据，不存文档全文。
ChromaDB 存向量 + 原文。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import chromadb

from .rag_config import CHROMA_COLLECTION_NAME, CHROMA_PERSIST_DIR, SQLITE_PATH


# ===================================================================
#  SQLite
# ===================================================================

SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS files (
    file_path     TEXT PRIMARY KEY,
    file_hash     TEXT NOT NULL,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chunks (
    id            TEXT PRIMARY KEY,
    file_path     TEXT NOT NULL,
    chunk_index   INTEGER NOT NULL,
    chunk_heading TEXT,
    summary       TEXT,
    q1            TEXT,
    q2            TEXT,
    q3            TEXT,
    chunk_text_hash TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def init_sqlite() -> sqlite3.Connection:
    """初始化 SQLite，建表并返回连接。"""
    db_path = Path(SQLITE_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SQLITE_DDL)
    conn.commit()
    return conn


def get_file_hash(conn: sqlite3.Connection, file_path: str) -> str | None:
    cur = conn.execute("SELECT file_hash FROM files WHERE file_path = ?", (file_path,))
    row = cur.fetchone()
    return row[0] if row else None


def upsert_file(conn: sqlite3.Connection, file_path: str, file_hash: str) -> None:
    conn.execute(
        """INSERT INTO files (file_path, file_hash, updated_at)
           VALUES (?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(file_path) DO UPDATE SET
               file_hash = excluded.file_hash,
               updated_at = CURRENT_TIMESTAMP""",
        (file_path, file_hash),
    )
    conn.commit()


def insert_chunk(
    conn: sqlite3.Connection,
    chunk_id: str,
    file_path: str,
    chunk_index: int,
    chunk_heading: str | None,
    summary: str | None,
    q1: str | None,
    q2: str | None,
    q3: str | None,
    chunk_text_hash: str,
) -> None:
    conn.execute(
        """INSERT INTO chunks (id, file_path, chunk_index, chunk_heading,
                               summary, q1, q2, q3, chunk_text_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (chunk_id, file_path, chunk_index, chunk_heading,
         summary, q1, q2, q3, chunk_text_hash),
    )
    conn.commit()


def delete_chunks_by_file(conn: sqlite3.Connection, file_path: str) -> None:
    conn.execute("DELETE FROM chunks WHERE file_path = ?", (file_path,))
    conn.commit()


# ===================================================================
#  ChromaDB
# ===================================================================

def get_chroma_client() -> chromadb.PersistentClient:
    return chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)


def get_or_create_collection() -> chromadb.Collection:
    client = get_chroma_client()
    return client.get_or_create_collection(
        name=CHROMA_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )