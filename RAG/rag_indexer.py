"""
索引管线：读取 .md → 切 Small 块 → LLM 生成摘要+提问 → 双写入。

支持全量重建和增量更新（通过文件 hash 判断）。
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from .llm_client import embed_texts, llm_chat
from .prompts import INDEXER_SYSTEM_PROMPT, INDEXER_USER_TEMPLATE
from .rag_config import CHUNK_MIN_LENGTH, INSERT_ELEMENT_DIR
from .schema import (
    delete_chunks_by_file,
    get_file_hash,
    get_or_create_collection,
    init_sqlite,
    insert_chunk,
    upsert_file,
)

# ── 读取 & 切块 ──────────────────────────────────────────────────


def _read_md(file_path: Path) -> str:
    return file_path.read_text(encoding="utf-8")


def _split_into_chunks(text: str, source_file: str) -> list[dict]:
    """
    按 `##` 标题拆分 .md 文件为 Small 块。
    每个块包含: heading, content, index
    """
    import re

    # 按 ## 标题分割，保留标题行
    sections = re.split(r"^(#{2}\s+.+)$", text, flags=re.MULTILINE)
    # sections 是交错排列: [前置内容, 标题1, 标题1下内容, 标题2, 标题2下内容, ...]

    chunks: list[dict] = []
    chunk_index = 0

    # 处理第一个 ## 之前的头部内容（通常是 # 总标题），作为单独一块
    if sections and not sections[0].startswith("##"):
        head = sections[0].strip()
        if head and len(head) >= CHUNK_MIN_LENGTH:
            chunks.append({
                "heading": source_file,
                "content": head,
                "index": chunk_index,
                "source": source_file,
            })
            chunk_index += 1
        sections = sections[1:]

    # 成对处理 (heading, content)
    for i in range(0, len(sections) - 1, 2):
        heading = sections[i].lstrip("#").strip()
        content_lines = sections[i + 1].splitlines()
        # 过滤掉可能的 --- 分隔行（美化的空行）
        filtered = [l for l in content_lines if l.strip() != "---"]
        content = "\n".join(filtered).strip()

        if len(content) < CHUNK_MIN_LENGTH:
            continue

        chunks.append({
            "heading": heading,
            "content": content,
            "index": chunk_index,
            "source": source_file,
        })
        chunk_index += 1

    return chunks


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_file(file_path: Path) -> str:
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


# ── LLM 生成摘要 & 模拟提问 ──────────────────────────────────────

def _generate_summary_and_questions(heading: str, content: str) -> dict:
    user_prompt = INDEXER_USER_TEMPLATE.format(heading=heading, content=content[:2000])
    raw = llm_chat(INDEXER_SYSTEM_PROMPT, user_prompt)

    summary, q1, q2, q3 = "", "", "", ""
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("摘要："):
            summary = line.removeprefix("摘要：").strip()
        elif line.startswith("Q1："):
            q1 = line.removeprefix("Q1：").strip()
        elif line.startswith("Q2："):
            q2 = line.removeprefix("Q2：").strip()
        elif line.startswith("Q3："):
            q3 = line.removeprefix("Q3：").strip()
    return {"summary": summary, "q1": q1, "q2": q2, "q3": q3}


# ── 索引构建 ─────────────────────────────────────────────────────

def build_index(*, force_rebuild: bool = False) -> None:
    """全量/增量构建索引。

    Args:
        force_rebuild: True 则忽略 hash、全部重建。
    """
    conn = init_sqlite()
    collection = get_or_create_collection()

    md_files = sorted(INSERT_ELEMENT_DIR.glob("*.md"))
    if not md_files:
        print("[索引] InsertElement 目录下没有 .md 文件")
        return

    total_chunks = 0
    skipped_files = 0

    for md_file in md_files:
        rel_path = md_file.name
        file_hash = _hash_file(md_file)

        # 增量检查：hash 没变就跳过
        if not force_rebuild:
            existing_hash = get_file_hash(conn, rel_path)
            if existing_hash == file_hash:
                print(f"  ⏭ 跳过（未变更）: {rel_path}")
                skipped_files += 1
                continue

        print(f"  📄 索引: {rel_path}")
        text = _read_md(md_file)
        chunks = _split_into_chunks(text, rel_path)

        # 删除该文件旧的 chunks（SQLite + ChromaDB）
        delete_chunks_by_file(conn, rel_path)

        # 收集所有需要写入 ChromaDB 的数据
        chroma_ids: list[str] = []
        chroma_texts: list[str] = []
        chroma_metadatas: list[dict] = []

        for chunk in chunks:
            chunk_id = str(uuid.uuid4())
            chunk_hash = _hash_text(chunk["content"])

            # 调用 LLM 生成摘要 + 模拟提问
            gen = _generate_summary_and_questions(chunk["heading"], chunk["content"])

            # ── 写入 SQLite ──
            insert_chunk(
                conn=conn,
                chunk_id=chunk_id,
                file_path=chunk["source"],
                chunk_index=chunk["index"],
                chunk_heading=chunk["heading"],
                summary=gen["summary"],
                q1=gen["q1"],
                q2=gen["q2"],
                q3=gen["q3"],
                chunk_text_hash=chunk_hash,
            )

            # ── 准备 ChromaDB 写入（5 条记录：原文 + 摘要 + 3 提问）──
            base_meta = {
                "chunk_id": chunk_id,
                "file_path": chunk["source"],
                "chunk_index": chunk["index"],
                "chunk_heading": chunk["heading"],
            }

            items_for_embedding = [
                ("original", chunk["content"], {**base_meta, "text_type": "original"}),
            ]

            if gen["summary"]:
                items_for_embedding.append(
                    ("summary", gen["summary"], {**base_meta, "text_type": "summary"})
                )
            for key, label in [("q1", "sim_q1"), ("q2", "sim_q2"), ("q3", "sim_q3")]:
                if gen.get(key):
                    items_for_embedding.append(
                        (label, gen[key], {**base_meta, "text_type": label})
                    )

            for label, text, meta in items_for_embedding:
                chroma_ids.append(f"{chunk_id}_{label}")
                chroma_texts.append(text)
                chroma_metadatas.append(meta)

            total_chunks += 1

        # ── 批量写入 ChromaDB ──
        if chroma_texts:
            print(f"     → {len(chroma_texts)} 条向量写入 ChromaDB ...")
            embeddings = embed_texts(chroma_texts)
            collection.add(
                ids=chroma_ids,
                embeddings=embeddings,
                documents=chroma_texts,
                metadatas=chroma_metadatas,
            )

        # 更新文件 hash
        upsert_file(conn, rel_path, file_hash)

    conn.close()
    print(f"\n✅ 索引完成！共处理 {total_chunks} 个块，跳过 {skipped_files} 个未变更文件")