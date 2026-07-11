"""
Reranker 评测 —— qwen3-rerank vs 纯语义 vs 关键词融合。

用同一评测集 + 已缓存的语义候选,对比三种"精排"方式在 nDCG@10/MRR/Recall@5 上的表现,
并测不同粗筛力度 n(30/50)下 reranker 的效果与调用成本(token)。

用法:uv run python eval/rerank_eval.py   # 需先跑过 harness 生成 .cache/
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from harness import fuse, load_cache, mrr, ndcg_at_k, recall_at_k  # noqa: E402

from tech_doc_mcp.config import get_embedding_config  # noqa: E402

EVAL_DIR = Path(__file__).parent
CACHE_DIR = EVAL_DIR / ".cache"
TESTSET = EVAL_DIR / "testset.yaml"
RR_CACHE = CACHE_DIR / "rerank.json"
DOC_TRUNC = 2000  # 每篇文档截断字符数(控 token,rerank 单文档上限 4000 tokens)

SHIPPED = {"scheme": "multiplicative", "n": 30, "k1": 2.0, "lam": 0.5, "sem_norm": "minmax"}


def _rerank_url_key():
    cfg = get_embedding_config()
    base = cfg["api_base"].replace("/compatible-mode/", "/compatible-api/")
    return base.rstrip("/") + "/reranks", cfg["api_key"]


def rerank_call(query, docs, cache):
    """调 qwen3-rerank，返回 [(orig_index, score), ...] 降序。带缓存。"""
    key = f"{query}|||{len(docs)}|||{hash(tuple(d[:80] for d in docs)) & 0xffffffff}"
    if key in cache:
        return cache[key]["order"], cache[key]["tokens"]
    url, api_key = _rerank_url_key()
    payload = {"model": "qwen3-rerank", "query": query,
               "documents": [d[:DOC_TRUNC] for d in docs], "top_n": len(docs)}
    r = httpx.post(url, headers={"Authorization": f"Bearer {api_key}"}, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    order = [(item["index"], item["relevance_score"]) for item in data["results"]]
    tokens = data.get("usage", {}).get("total_tokens", 0)
    cache[key] = {"order": order, "tokens": tokens}
    return order, tokens


def rerank_paths(cand, query, n, cache):
    """对 top-n 候选做 rerank，dedup by path 取最高分，返回排序 path 列表。"""
    pool = cand[:n]
    order, tokens = rerank_call(query, [c["text"] for c in pool], cache)
    best = {}
    for idx, score in order:
        p = pool[idx]["path"]
        if p not in best or score > best[p]:
            best[p] = score
    ranked = [p for p, _ in sorted(best.items(), key=lambda x: -x[1])]
    return ranked, tokens


def sem_paths(cand, n):
    seen = []
    for c in cand[:n]:
        if c["path"] not in seen:
            seen.append(c["path"])
    return seen


def evaluate(name, ranker, cases, cands):
    nd = mr = rc = 0.0
    for c, cand in zip(cases, cands):
        ranked = ranker(c, cand)
        gold = set(c["gold"])
        nd += ndcg_at_k(ranked, gold)
        mr += mrr(ranked, gold)
        rc += recall_at_k(ranked, gold)
    n = len(cases)
    print(f"  {name:38} nDCG@10={nd/n:.4f}  MRR={mr/n:.4f}  R@5={rc/n:.4f}")
    return nd / n


def main():
    cases = yaml.safe_load(TESTSET.read_text())
    for c in cases:
        c.setdefault("keywords", [])
    cands = load_cache(cases)
    cache = json.loads(RR_CACHE.read_text()) if RR_CACHE.exists() else {}

    total_tokens = 0

    def make_rr(n):
        def rank(c, cand):
            nonlocal total_tokens
            ranked, tok = rerank_paths(cand, c["query"], n, cache)
            total_tokens += tok
            return ranked
        return rank

    print("对比(同一评测集 25 query):\n")
    evaluate("纯语义(baseline)", lambda c, cand: sem_paths(cand, 30), cases, cands)
    evaluate("关键词融合(现方案 乘性 n30)", lambda c, cand: fuse(cand, c["keywords"], SHIPPED), cases, cands)
    tok_before = total_tokens
    evaluate("qwen3-rerank  (粗筛 n=30)", make_rr(30), cases, cands)
    rr30_tok = total_tokens - tok_before
    tok_before = total_tokens
    evaluate("qwen3-rerank  (粗筛 n=50)", make_rr(50), cases, cands)
    rr50_tok = total_tokens - tok_before

    RR_CACHE.write_text(json.dumps(cache, ensure_ascii=False))
    print(f"\n成本(25 query 总 token):rerank n=30 → {rr30_tok},  n=50 → {rr50_tok}")


if __name__ == "__main__":
    main()
