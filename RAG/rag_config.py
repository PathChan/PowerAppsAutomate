"""
RAG 系统全局配置。

所有路径、模型名、Chunk 参数集中管理。
自动加载项目根目录的 .env 文件。
"""
from __future__ import annotations

import os
from pathlib import Path

# ── 自动加载 .env ────────────────────────────────────────────────
_env_loaded: bool = False


def load_env_file(path: Path | None = None) -> None:
    """Load simple KEY=VALUE pairs from .env without requiring extra packages."""
    global _env_loaded
    if _env_loaded:
        return
    if path is None:
        path = Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
    _env_loaded = True


load_env_file()

# ── 项目路径 ──────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAG_DIR = PROJECT_ROOT / "RAG"
INSERT_ELEMENT_DIR = RAG_DIR / "InsertElement"

# ── 存储路径 ──────────────────────────────────────────────────────────
DB_DIR = PROJECT_ROOT / "datebase" / "RAG"
CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", str(DB_DIR / "chroma_db"))
SQLITE_PATH = os.getenv("SQLITE_PATH", str(DB_DIR / "rag_meta.db"))

# ── LLM（文本生成：DeepSeek-V4-Flash）────────────────────────────────
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding/v3")
LLM_MODEL = os.getenv("LLM_MODEL", "DeepSeek-V4-Flash")

# ── Embedding（豆包）──────────────────────────────────────────────────
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "")
EMBEDDING_BASE_URL = os.getenv(
    "EMBEDDING_BASE_URL",
    "https://ark.cn-beijing.volces.com/api/v3",
)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "")

# ── Reranker（本地）───────────────────────────────────────────────────
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
RERANKER_DEVICE = os.getenv("RERANKER_DEVICE", "cpu")

# ── Chunk 参数 ────────────────────────────────────────────────────────
CHUNK_MIN_LENGTH = int(os.getenv("CHUNK_MIN_LENGTH", "30"))

# ── 检索参数 ──────────────────────────────────────────────────────────
RETRIEVER_TOP_K = int(os.getenv("RETRIEVER_TOP_K", "30"))
RERANKER_TOP_K = int(os.getenv("RERANKER_TOP_K", "5"))

# ── ChromaDB Collection ──────────────────────────────────────────────
CHROMA_COLLECTION_NAME = "powerapps_components"