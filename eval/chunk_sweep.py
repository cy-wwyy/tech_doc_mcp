"""
分块大小 vs 检索质量曲线 —— 用数据确定 chunk_size,而非默认 1024 拍脑袋。

对每个 chunk_size:重切全部 clean 文档 → 嵌入(去代码正文)→ 建临时 ChromaDB →
跑 testset 纯语义检索,算 Recall@5/nDCG@10/MRR。临时索引落在 eval/.cache/sweep_chroma,
不碰生产 data/chroma。shadcn 的 clean 已不在磁盘,自动跳过。

用法:uv run python eval/chunk_sweep.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from harness import mrr, ndcg_at_k, recall_at_k  # noqa: E402

from tech_doc_mcp.client import embed, embed_one, get_embedding_client  # noqa: E402
from tech_doc_mcp.crawler.loader import load_documents  # noqa: E402
from tech_doc_mcp.processor.chunker import chunk_document  # noqa: E402
from tech_doc_mcp.store.vector_store import VectorStore  # noqa: E402

EVAL_DIR = Path(__file__).parent
TESTSET = EVAL_DIR / "testset.yaml"
SWEEP_DIR = EVAL_DIR / ".cache" / "sweep_chroma"

SIZES = [500, 800, 1024, 1500, 2000]


def index_source(store, source, size, ec):
    clean = Path(f"docs/{source}/clean")
    docs = load_documents(source, clean)
    chunks = []
    for d in docs:
        chunks.extend(chunk_document(d, chunk_size=size))
    for i in range(0, len(chunks), 10):
        batch = chunks[i : i + 10]
        vecs = embed(ec, [c.text_for_embedding for c in batch])
        for c, v in zip(batch, vecs):
            c.embedding = v
    store.delete_collection(source, str(size))
    store.insert(source, str(size), chunks)
    return len(chunks)


def sem_paths(res, k=10):
    seen = []
    for r in res:
        p = (r.get("metadata") or {}).get("path", "")
        if p and p not in seen:
            seen.append(p)
        if len(seen) >= k:
            break
    return seen


def main():
    cases = yaml.safe_load(TESTSET.read_text())
    sources_on_disk = {c["source"] for c in cases if Path(f"docs/{c['source']}/clean").exists()
                       and any(Path(f"docs/{c['source']}/clean").rglob("*.md"))}
    cases = [c for c in cases if c["source"] in sources_on_disk]
    print(f"覆盖源: {sorted(sources_on_disk)} | query 数: {len(cases)}")

    ec = get_embedding_client()
    qemb = {c["query"]: embed_one(ec, c["query"]) for c in cases}  # 查询向量与 size 无关,只嵌一次

    if SWEEP_DIR.exists():
        shutil.rmtree(SWEEP_DIR)
    store = VectorStore(persist_dir=SWEEP_DIR)

    rows = []
    for size in SIZES:
        total_chunks = 0
        for src in sorted(sources_on_disk):
            total_chunks += index_source(store, src, size, ec)
        nd = mr = rc = 0.0
        for c in cases:
            res = store.query(c["source"], str(size), qemb[c["query"]], n_results=30)
            ranked = sem_paths(res)
            gold = set(c["gold"])
            nd += ndcg_at_k(ranked, gold)
            mr += mrr(ranked, gold)
            rc += recall_at_k(ranked, gold)
        n = len(cases)
        rows.append((size, total_chunks, nd / n, mr / n, rc / n))
        print(f"  size={size:5}  chunks={total_chunks:5}  nDCG@10={nd/n:.4f}  MRR={mr/n:.4f}  R@5={rc/n:.4f}")

    print(f"\n{'chunk_size':>10} {'chunks':>7} {'nDCG@10':>8} {'MRR':>7} {'Recall@5':>9}")
    for size, ch, nd, mr, rc in rows:
        print(f"{size:>10} {ch:>7} {nd:>8.4f} {mr:>7.4f} {rc:>9.4f}")
    best = max(rows, key=lambda r: r[2])
    print(f"\n最优(nDCG@10): chunk_size={best[0]}  (当前默认 1024)")
    shutil.rmtree(SWEEP_DIR, ignore_errors=True)  # 清理临时索引


if __name__ == "__main__":
    main()
