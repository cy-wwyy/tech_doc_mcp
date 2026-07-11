"""
召回缺口诊断 —— 量化 boost-only 的边界情况。

问题:关键词只在代码里 → 语义(去代码)可能召不回该页 → boost-only 只搜语义候选 → 够不着。
本脚本量化:有多少 gold 页落在语义 top-N 之外(boost-only 不可达),其中多少能被 $contains 兜回,
以及这些关键词在全库的稀有度(IDF 门控是否可行)。

用法:uv run python eval/recall_gap.py   # 需先跑过 harness 生成 .cache/
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from tech_doc_mcp.store.vector_store import VectorStore

EVAL_DIR = Path(__file__).parent
CACHE_DIR = EVAL_DIR / ".cache"
TESTSET = EVAL_DIR / "testset.yaml"


def sem_paths(cache: list[dict], n: int) -> list[str]:
    """语义 top-n 候选去重后的页面集合(保序)。"""
    seen = []
    for c in cache[:n]:
        p = c["path"]
        if p not in seen:
            seen.append(p)
    return seen


def main() -> None:
    cases = yaml.safe_load(TESTSET.read_text())
    vs = VectorStore()

    tot_gold = 0
    in30 = in60 = 0
    missed_recoverable = 0
    missed_lost = 0
    gap_rows = []  # 漏掉的 gold 明细

    for i, c in enumerate(cases):
        cache = json.loads((CACHE_DIR / f"{c['source']}__{i}.json").read_text())
        gold = c["gold"]
        s30 = set(sem_paths(cache, 30))
        s60 = set(sem_paths(cache, 60))
        col = vs.get_or_create_collection(c["source"], c["version"])

        for g in gold:
            tot_gold += 1
            if g in s30:
                in30 += 1
                in60 += 1
                continue
            if g in s60:
                in60 += 1
                continue
            # 语义 top-60 都没召回 → boost-only(n≤60)不可达。看 $contains 能否兜回
            recovered_by = []
            for kw in c.get("keywords", []):
                r = col.get(where_document={"$contains": kw}, include=["metadatas"])
                pages = {(m or {}).get("path", "") for m in (r["metadatas"] or [])}
                pagefreq = len(pages)  # 含该 kw 的页数(越小越稀有 → IDF 越高)
                if g in pages:
                    recovered_by.append((kw, pagefreq))
            if recovered_by:
                missed_recoverable += 1
            else:
                missed_lost += 1
            gap_rows.append((c["source"], c["query"], g, recovered_by))

    print(f"\n{'='*60}")
    print(f"gold 总数: {tot_gold}")
    print(f"  语义 top-30 已召回: {in30}  ({in30/tot_gold:.0%})")
    print(f"  语义 top-60 已召回: {in60}  ({in60/tot_gold:.0%})")
    print(f"  语义 top-60 外(boost-only 不可达): {tot_gold - in60}")
    print(f"      其中 $contains 可兜回: {missed_recoverable}")
    print(f"      其中彻底丢失(连 $contains 也无): {missed_lost}")
    if gap_rows:
        print("\n漏掉的 gold 明细(kw, 含该词的页数):")
        for src, q, g, rec in gap_rows:
            tag = "  ".join(f"{kw}(含{pf}页)" for kw, pf in rec) if rec else "无 kw 兜回"
            print(f"  [{src}] {g}\n        q={q!r}  → {tag}")
    else:
        print("\n✓ 没有任何 gold 页落在语义 top-60 之外 —— 该语料不存在此边界缺口。")


if __name__ == "__main__":
    main()
