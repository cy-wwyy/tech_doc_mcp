"""
LLM-as-judge 交叉复核 —— 独立于人工 gold,验证决赛圈配置。

对每个 query，取各决赛配置返回的 top-K 页并集，让 LLM 给每页打相关分(0-3)，
再用这份"LLM 真值"算 graded nDCG@K，排出各配置。与 harness 的人工-gold 排名对照，
若结论一致 → gold 标注可信、方案可敲定。

判分结果缓存在 .cache/judge.json（键 = query|||path），跨配置复用，控成本。

用法:uv run python eval/judge.py   # 需先跑过 harness 生成 .cache/
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from harness import fuse, load_cache  # noqa: E402

from tech_doc_mcp.client import get_llm_client, get_llm_extra_body, get_llm_model  # noqa: E402

EVAL_DIR = Path(__file__).parent
CACHE_DIR = EVAL_DIR / ".cache"
TESTSET = EVAL_DIR / "testset.yaml"
JUDGE_CACHE = CACHE_DIR / "judge.json"
K = 6            # 每配置取 top-K 页参与判分
CONCURRENCY = 5

# 决赛圈:基线 + 网格最优 + 两个代表性对照
FINALISTS = {
    "semantic_only(基线)": {"scheme": "semantic_only", "n": 30},
    "additive raw k1=2.0 λ=0.25(网格最优)": {"scheme": "additive", "n": 30, "k1": 2.0, "lam": 0.25, "sem_norm": "raw"},
    "additive minmax k1=1.5 λ=0.5": {"scheme": "additive", "n": 30, "k1": 1.5, "lam": 0.5, "sem_norm": "minmax"},
    "multiplicative minmax k1=2.0 λ=0.5": {"scheme": "multiplicative", "n": 30, "k1": 2.0, "lam": 0.5, "sem_norm": "minmax"},
}

PROMPT = """你在评估一个技术文档检索系统的结果。判断下面这段文档片段，对回答用户查询的相关程度。

用户查询：{query}

文档路径：{path}
文档片段：
{snippet}

评分标准（只输出一个数字 0-3）：
0 = 无关
1 = 略相关（提到但不解决）
2 = 相关（部分回答）
3 = 高度相关（正是答案所在页）

只输出一个数字。"""


def snippet_for(cand, path):
    for c in cand:
        if c["path"] == path:
            return c["text"][:900]
    return ""


async def judge_one(client, model, extra, sem, query, path, snippet, cache):
    key = f"{query}|||{path}"
    if key in cache:
        return
    async with sem:
        for _ in range(2):
            try:
                kw = dict(model=model, messages=[{"role": "user", "content": PROMPT.format(query=query, path=path, snippet=snippet)}], temperature=0)
                if extra:
                    kw["extra_body"] = extra
                resp = await client.chat.completions.create(**kw)
                # 取末位 0-3 数字：模型即便带前言，评分通常在结尾
                nums = re.findall(r"[0-3]", resp.choices[0].message.content)
                if nums:
                    cache[key] = int(nums[-1])
                    return
            except Exception:
                await asyncio.sleep(1)
        cache[key] = 0


def ndcg(ranked, rel, k=K):
    dcg = sum((2 ** rel.get(p, 0) - 1) / math.log2(i + 2) for i, p in enumerate(ranked[:k]))
    ideal = sorted(rel.values(), reverse=True)[:k]
    idcg = sum((2 ** r - 1) / math.log2(i + 2) for i, r in enumerate(ideal))
    return dcg / idcg if idcg > 0 else 0.0


async def main():
    cases = yaml.safe_load(TESTSET.read_text())
    for c in cases:
        c.setdefault("keywords", [])
    cands = load_cache(cases)
    cache = json.loads(JUDGE_CACHE.read_text()) if JUDGE_CACHE.exists() else {}

    # 收集所有 (query, path) 判分对（各配置 top-K 并集）
    per_case_pages = []
    tasks = []
    client, model, extra = get_llm_client(), get_llm_model(), get_llm_extra_body()
    sem = asyncio.Semaphore(CONCURRENCY)
    for c, cand in zip(cases, cands):
        pages = set()
        for cfg in FINALISTS.values():
            pages.update(fuse(cand, c["keywords"], cfg)[:K])
        per_case_pages.append(pages)
        for p in pages:
            tasks.append(judge_one(client, model, extra, sem, c["query"], p, snippet_for(cand, p), cache))
    print(f"判分 {len(tasks)} 个 (query,page) 对(缓存命中跳过)…")
    await asyncio.gather(*tasks)
    JUDGE_CACHE.write_text(json.dumps(cache, ensure_ascii=False))

    # 逐配置算 LLM-graded nDCG@K
    print(f"\n{'='*64}\nLLM-judge 排名(graded nDCG@{K}，独立于人工 gold):\n")
    scores = {}
    for name, cfg in FINALISTS.items():
        tot = 0.0
        for idx, (c, cand) in enumerate(zip(cases, cands)):
            rel = {p: cache.get(f"{c['query']}|||{p}", 0) for p in per_case_pages[idx]}
            tot += ndcg(fuse(cand, c["keywords"], cfg), rel)
        scores[name] = tot / len(cases)
    for name, s in sorted(scores.items(), key=lambda x: -x[1]):
        print(f"  {s:.4f}   {name}")


if __name__ == "__main__":
    asyncio.run(main())
