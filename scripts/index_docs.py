"""
文档索引 — 完整链路: 加载 → 分块 → 嵌入 → ChromaDB
用法: uv run python scripts/index_docs.py <source> <version>
示例: uv run python scripts/index_docs.py fastapi 0.139.0
"""

import asyncio
import sys
import time
from pathlib import Path

from openai import AsyncOpenAI

sys.path.insert(0, "src")
from tech_doc_mcp.config import load_config, get_embedding_config
from tech_doc_mcp.crawler.loader import load_documents
from tech_doc_mcp.processor.chunker import chunk_document
from tech_doc_mcp.store.vector_store import VectorStore


async def main(source: str, version: str):
    config = load_config()
    ec = get_embedding_config(config)

    client = AsyncOpenAI(base_url=ec["api_base"], api_key=ec["api_key"])
    batch_size = ec.get("batch_size", 10)
    store = VectorStore()

    # ── 1. 加载 ─────────────────────────────────
    clean_dir = Path(f"docs/{source}/clean")
    if not clean_dir.exists():
        print(f"❌ 目录不存在: {clean_dir}")
        return

    t0 = time.time()
    documents = load_documents(source, clean_dir)
    print(f"📄 加载 {len(documents)} 篇文档")

    # ── 2. 分块 ─────────────────────────────────
    all_chunks = []
    for doc in documents:
        chunks = chunk_document(doc, chunk_size=1024, chunk_overlap=200)
        all_chunks.extend(chunks)

    print(f"🔪 分块 {len(all_chunks)} 个 (avg {len(all_chunks)//max(len(documents),1)}/doc)")

    # ── 3. 嵌入（批量 + 并发）─────────────────────
    print(f"🧮 嵌入中 (batch={batch_size}, concurrent=5)...")

    sem = asyncio.Semaphore(5)
    embedded_count = 0

    async def embed_batch(batch_chunks: list):
        nonlocal embedded_count
        async with sem:
            texts = [c.text for c in batch_chunks]
            resp = await client.embeddings.create(model=ec["model"], input=texts)
            for ch, emb_data in zip(batch_chunks, resp.data):
                ch.embedding = emb_data.embedding
            embedded_count += len(batch_chunks)
            print(f"  📊 {embedded_count}/{len(all_chunks)}", end="\r")

    # 按 batch_size 分组
    batches = [all_chunks[i:i+batch_size] for i in range(0, len(all_chunks), batch_size)]
    await asyncio.gather(*(embed_batch(b) for b in batches))

    print(f"\n✅ 嵌入完成: {embedded_count} chunks")

    # ── 4. 入库 ─────────────────────────────────
    # 先删旧版本（如果存在）
    if store.delete_collection(source, version):
        print(f"🗑️  删除旧版 collection: docs_{source}_{version}")

    n = store.insert(source, version, all_chunks)
    elapsed = time.time() - t0
    print(f"💾 入库 {n} chunks → collection: docs_{source}_{version}")
    print(f"⏱️  总耗时: {elapsed:.0f}s")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("用法: uv run python scripts/index_docs.py <source> <version>")
        print("示例: uv run python scripts/index_docs.py fastapi 0.139.0")
        sys.exit(1)

    asyncio.run(main(sys.argv[1], sys.argv[2]))
