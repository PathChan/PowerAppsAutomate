"""
查询管线：改写 → 向量检索 → Reranker 重排 → Top 5。
"""
from __future__ import annotations

from sentence_transformers import CrossEncoder
from .rag_config import RERANKER_DEVICE, RERANKER_MODEL, RERANKER_TOP_K, RETRIEVER_TOP_K
from .prompts import RETRIEVER_REWRITE_SYSTEM_PROMPT
from .llm_client import embed_query, llm_chat
from .schema import get_or_create_collection


# ═══════════════════════════════════════════════════════════════
#  Step 1: 问题改写（轻量 LLM 调用）
# ═══════════════════════════════════════════════════════════════

REWRITE_SYSTEM_PROMPT = """你是一个 PowerApps 组件检索助手。用户的原始问题可能包含复合意图或口语化表达。

请做两件事：
1. 如果问题包含多个独立意图，拆分成多个子问题（每行一条）。
2. 对每个子问题做同义词/术语规范化（比如 "下拉菜单" → "Dropdown"，"按钮" → "Button"，"开关" → "Toggle"）。

要求：
- 只输出改写后的问题，每行一条，不要序号和额外文字。
- 如果不需要拆分和改写，原样输出即可。"""


def rewrite_query(user_query: str) -> list[str]:
    """将用户原始问题改写/拆解为 1~N 个检索用 query。"""
    raw = llm_chat(REWRITE_SYSTEM_PROMPT, user_query, temperature=0.1)
    queries = [q.strip() for q in raw.splitlines() if q.strip()]
    # 去重
    seen: set[str] = set()
    result: list[str] = []
    for q in queries:
        if q.lower() not in seen:
            seen.add(q.lower())
            result.append(q)
    return result if result else [user_query]


# ═══════════════════════════════════════════════════════════════
#  Step 2: 向量检索
# ═══════════════════════════════════════════════════════════════

def _vector_search(query: str, top_k: int = RETRIEVER_TOP_K) -> list[dict]:
    """单 query 向量检索，返回候选块列表（含 chunk_id 和原始文本）。"""
    collection = get_or_create_collection()
    q_vec = embed_query(query)
    results = collection.query(
        query_embeddings=[q_vec],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )

    candidates: list[dict] = []
    if not results["ids"] or not results["ids"][0]:
        return candidates

    for i, doc_id in enumerate(results["ids"][0]):
        meta = results["metadatas"][0][i] if results["metadatas"] else {}
        candidates.append({
            "id": doc_id,
            "document": results["documents"][0][i] if results["documents"] else "",
            "chunk_id": meta.get("chunk_id", ""),
            "file_path": meta.get("file_path", ""),
            "chunk_heading": meta.get("chunk_heading", ""),
            "text_type": meta.get("text_type", ""),
            "distance": results["distances"][0][i] if results["distances"] else 0,
        })
    return candidates


def _dedup_by_chunk_id(candidates: list[dict]) -> list[dict]:
    """多 query 结果合并时，按 chunk_id 去重（保留距离最小的那条）。"""
    seen: dict[str, dict] = {}
    for c in candidates:
        cid = c["chunk_id"]
        if cid not in seen or c["distance"] < seen[cid]["distance"]:
            seen[cid] = c
    return list(seen.values())


# ═══════════════════════════════════════════════════════════════
#  Step 3: Reranker (Cross-Encoder) 精排
# ═══════════════════════════════════════════════════════════════

_reranker: CrossEncoder | None = None


def _get_reranker() -> CrossEncoder:
    global _reranker
    if _reranker is None:
        print(f"[Reranker] 加载模型 {RERANKER_MODEL} ...")
        _reranker = CrossEncoder(
            RERANKER_MODEL,
            device=RERANKER_DEVICE,
        )
    return _reranker


def _rerank(query: str, candidates: list[dict], top_k: int = RERANKER_TOP_K) -> list[dict]:
    """用 Cross-Encoder 对候选块做全注意力重排。"""
    if not candidates:
        return []

    reranker = _get_reranker()
    pairs = [(query, c["document"]) for c in candidates]
    scores = reranker.predict(pairs)

    # 按得分降序排列
    scored = list(zip(candidates, scores))
    scored.sort(key=lambda x: x[1], reverse=True)

    return [
        {**c, "rerank_score": float(s)} for c, s in scored[:top_k]
    ]


# ═══════════════════════════════════════════════════════════════
#  对外接口
# ═══════════════════════════════════════════════════════════════

def retrieve(user_query: str) -> list[dict]:
    """完整检索管线：改写 → 向量检索 → Reranker → Top 5。

    Returns:
        list[dict]: 每个 dict 包含:
            - chunk_id / file_path / chunk_heading
            - document: 匹配的原始文本
            - text_type: original / summary / sim_q1~q3
            - distance: 向量距离
            - rerank_score: Cross-Encoder 得分
    """
    # Step 1: 改写
    queries = rewrite_query(user_query)
    print(f"[检索] 原始问题: {user_query}")
    print(f"[检索] 改写后: {queries}")

    # Step 2: 多 query 向量检索 + 合并
    all_candidates: list[dict] = []
    for q in queries:
        candidates = _vector_search(q, top_k=RETRIEVER_TOP_K)
        all_candidates.extend(candidates)

    merged = _dedup_by_chunk_id(all_candidates)
    # 如果不够，放宽一次
    if len(merged) < RERANKER_TOP_K:
        merged = _dedup_by_chunk_id(all_candidates)

    # Step 3: Reranker
    top_results = _rerank(user_query, merged, top_k=RERANKER_TOP_K)

    print(f"[检索] 向量候选: {len(merged)} 个 → Reranker 取 Top {len(top_results)}")
    for r in top_results:
        print(f"   [{r['rerank_score']:.4f}] {r['file_path']} › {r['chunk_heading']} ({r['text_type']})")

    return top_results