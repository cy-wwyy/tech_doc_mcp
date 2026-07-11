"""
Pooling 扩充 gold —— 消除窄 gold 偏差。

IR 标准做法:把多个系统(语义/关键词融合/reranker)的 top 结果并成候选池,
让 LLM 逐页判相关性(0-3),≥2 分的纳入 gold。多系统 pooling 降低单系统偏见,
LLM 打分复用 judge.json 缓存。

输出「当前 gold vs 建议 gold」的 diff,供人工审核后再写入 testset.yaml。

用法:uv run python eval/expand_gold.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from harness import fuse, load_cache  # noqa: E402
from judge import PROMPT, judge_one, snippet_for  # noqa: E402
from rerank_eval import SHIPPED, rerank_paths  # noqa: E402

from tech_doc_mcp.client import get_llm_client, get_llm_extra_body, get_llm_model  # noqa: E402

EVAL_DIR = Path(__file__).parent
CACHE_DIR = EVAL_DIR / ".cache"
TESTSET = EVAL_DIR / "testset.yaml"
JUDGE_CACHE = CACHE_DIR / "judge.json"
RR_CACHE = CACHE_DIR / "rerank.json"
POOL_K = 8          # 每系统取 top-K 入池
GOLD_THRESH = 2     # 相关性 ≥ 此值纳入 gold


async def main():
    cases = yaml.safe_load(TESTSET.read_text())
    for c in cases:
        c.setdefault("keywords", [])
    cands = load_cache(cases)
    jcache = json.loads(JUDGE_CACHE.read_text()) if JUDGE_CACHE.exists() else {}
    rcache = json.loads(RR_CACHE.read_text()) if RR_CACHE.exists() else {}

    # 每题构建候选池(三系统并集)
    pools = []
    for c, cand in zip(cases, cands):
        sem = []
        for x in cand[:POOL_K * 2]:
            if x["path"] not in sem:
                sem.append(x["path"])
        kw = fuse(cand, c["keywords"], SHIPPED)[:POOL_K]
        rr = rerank_paths(cand, c["query"], 30, rcache)[0][:POOL_K]
        pools.append(set(sem[:POOL_K]) | set(kw) | set(rr))

    # 判分(复用缓存)
    client, model, extra = get_llm_client(), get_llm_model(), get_llm_extra_body()
    sem_ = asyncio.Semaphore(5)
    tasks = []
    for i, (c, cand) in enumerate(zip(cases, cands)):
        for p in pools[i]:
            tasks.append(judge_one(client, model, extra, sem_, c["query"], p, snippet_for(cand, p), jcache))
    print(f"pooling 判分 {len(tasks)} 对(缓存跳过)…")
    await asyncio.gather(*tasks)
    JUDGE_CACHE.write_text(json.dumps(jcache, ensure_ascii=False))
    RR_CACHE.write_text(json.dumps(rcache, ensure_ascii=False))

    # 输出 diff
    print(f"\n{'='*72}\ngold 扩充建议(相关性≥{GOLD_THRESH} 纳入;括号内为 LLM 分):\n")
    proposed = {}
    for i, c in enumerate(cases):
        rel = {p: jcache.get(f"{c['query']}|||{p}", 0) for p in pools[i]}
        new_gold = sorted([p for p, s in rel.items() if s >= GOLD_THRESH], key=lambda p: -rel[p])
        proposed[i] = new_gold
        cur = set(c["gold"])
        added = [p for p in new_gold if p not in cur]
        dropped = [p for p in c["gold"] if rel.get(p, 3) < GOLD_THRESH]  # 原 gold 却低分(可疑)
        print(f"[{c['source']}] {c['query']}")
        print(f"    原 gold: {c['gold']}")
        if added:
            print(f"    + 新增: {[(p, rel[p]) for p in added]}")
        if dropped:
            print(f"    ? 原 gold 但 LLM 判低分: {[(p, rel.get(p)) for p in dropped]}")
        print()

    # 落一份建议稿(不直接改 testset,供审核)
    out = []
    for c, i in zip(cases, range(len(cases))):
        out.append({"source": c["source"], "version": c["version"], "query": c["query"],
                    "keywords": c["keywords"], "gold": proposed[i]})
    (EVAL_DIR / "testset.proposed.yaml").write_text(
        yaml.safe_dump(out, allow_unicode=True, sort_keys=False))
    print("建议稿写入 eval/testset.proposed.yaml —— 审核后覆盖 testset.yaml")


if __name__ == "__main__":
    asyncio.run(main())
