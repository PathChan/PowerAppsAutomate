"""
LLM / Embedding 客户端封装。

- text_llm：DeepSeek-V4-Flash（文本生成，通过 OpenAI SDK）
- embedder：Doubao-embedding（多模态向量化，直接 HTTP 请求）
"""
from __future__ import annotations

import json
import urllib.request

from openai import OpenAI

from .rag_config import (
    EMBEDDING_API_KEY,
    EMBEDDING_BASE_URL,
    EMBEDDING_MODEL,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
)


# ── 文本生成 LLM（OpenAI SDK）─────────────────────────────────────

def _build_llm_client() -> OpenAI:
    if not LLM_API_KEY:
        raise RuntimeError("LLM_API_KEY 未设置。请在 .env 中配置。")
    return OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)


_llm_client: OpenAI | None = None


def get_llm_client() -> OpenAI:
    global _llm_client
    if _llm_client is None:
        _llm_client = _build_llm_client()
    return _llm_client


def llm_chat(system: str, user: str, temperature: float = 0.3) -> str:
    """调用 DeepSeek-V4-Flash 生成文本。"""
    client = get_llm_client()
    resp = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
    )
    return resp.choices[0].message.content.strip()


# ── Embedding（Doubao-embedding，直接 HTTP 请求）────────────────────

_EMBED_API_URL = f"{EMBEDDING_BASE_URL.rstrip('/')}/embeddings/multimodal"


def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量文本向量化

    Ark 多模态向量化 API 每次调用返回单个 embedding，按 token 计费。
    因此每条文本单独发一次请求，不合并。
    """
    if not EMBEDDING_API_KEY:
        raise RuntimeError("EMBEDDING_API_KEY 未设置。请在 .env 中配置。")
    if not EMBEDDING_MODEL:
        raise RuntimeError("EMBEDDING_MODEL 未设置。请在 .env 中配置。")

    results: list[list[float]] = []
    for text in texts:
        body = json.dumps({
            "model": EMBEDDING_MODEL,
            "encoding_format": "float",
            "input": [{"type": "text", "text": text}],
        }).encode("utf-8")

        req = urllib.request.Request(
            _EMBED_API_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {EMBEDDING_API_KEY}",
            },
        )
        resp = urllib.request.urlopen(req)
        result = json.loads(resp.read().decode("utf-8"))

        # Ark 多模态接口返回单个 data 对象: {"embedding": [...], "object": "embedding"}
        results.append(result["data"]["embedding"])

    return results


def embed_query(text: str) -> list[float]:
    """单条查询向量化。"""
    return embed_texts([text])[0]