"""
搜索融合网格搜索 —— 用评测集给每套融合配置打分，用数据敲定方案。

流程：
  1. 缓存层：每 query 的 embedding + 语义候选(N)取一次存盘，网格搜索纯内存重排，零 API/DB 开销。
  2. fuse(candidates, keywords, config) 纯函数：4 种 scheme × 参数网格。
  3. 指标：nDCG@10 / MRR / Recall@5（gold 被挤下去 → 指标降，故 nDCG/MRR 即“抗污染”信号）。
  4. 输出 eval/results.md 排行榜。

用法：
  uv run python eval/harness.py            # 有缓存则直接跑网格；无则先建缓存
  uv run python eval/harness.py --rebuild  # 强制重建缓存（改了评测集/索引后）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from itertools import product
from pathlib import Path

import yaml

from tech_doc_mcp.client import embed_one, get_embedding_client
from tech_doc_mcp.search.hybrid import SEM_FLOOR, count_token
from tech_doc_mcp.store.vector_store import VectorStore

EVAL_DIR = Path(__file__).parent
CACHE_DIR = EVAL_DIR / ".cache"
TESTSET = EVAL_DIR / "testset.yaml"
RESULTS = EVAL_DIR / "results.md"

CAND_N = 60  # 缓存的候选池上限；网格里的 n 从中取子集

# ── 网格 ──────────────────────────────────────────────
SCHEMES = ["semantic_only", "additive", "multiplicative", "rrf_boost"]
K1_GRID = [0.5, 1.0, 1.5, 2.0]
LAM_GRID = [0.15, 0.25, 0.5, 0.75, 1.0]
SEMNORM_GRID = ["minmax", "raw"]
N_GRID = [30, 60]
RRF_K = 60


# ── 缓存构建（唯一需要 API/DB 的部分）────────────────────
# 内容寻址:文件名带 (source,version,query) 的哈希 —— 改评测集自动失效对应缓存，
# 不会像按下标命名那样在增删/改动 query 后错位。
def _cache_file(case: dict) -> Path:
    sig = hashlib.md5(f"{case['source']}|{case['version']}|{case['query']}".encode()).hexdigest()[:12]
    return CACHE_DIR / f"{case['source']}__{sig}.json"


def ensure_cache(cases: list[dict], rebuild: bool = False) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    vs = ec = None
    for c in cases:
        fp = _cache_file(c)
        if fp.exists() and not rebuild:
            continue
        if vs is None:
            vs, ec = VectorStore(), get_embedding_client()
        emb = embed_one(ec, c["query"])
        res = vs.query(c["source"], c["version"], emb, n_results=CAND_N)
        cand = [
            {
                "path": (r.get("metadata") or {}).get("path", ""),
                "text": r.get("text") or "",
                "distance": float(r.get("score") or 0.0),
            }
            for r in res
        ]
        fp.write_text(json.dumps(cand, ensure_ascii=False))
        print(f"  缓存 {fp.name}  候选 {len(cand)}")


def load_cache(cases: list[dict]) -> list[list[dict]]:
    return [json.loads(_cache_file(c).read_text()) for c in cases]


# ── 融合 ──────────────────────────────────────────────
def sat(f: int, k1: float) -> float:
    """BM25 饱和曲线：1 次有意义，300 次≈3 次。"""
    return f / (f + k1) if f > 0 else 0.0


def kw_score(text: str, keywords: list[str], k1: float) -> float:
    """presence + 饱和:Σ sat(count(kw)) / n_kw ∈ [0,1]。计数与生产 hybrid 一致(词边界)。"""
    kws = [k for k in keywords if k]
    if not kws:
        return 0.0
    return sum(sat(count_token(text, kw), k1) for kw in kws) / len(kws)


def fuse(candidates: list[dict], keywords: list[str], cfg: dict) -> list[str]:
    """返回按 path 去重后的排序结果（path 列表）。"""
    cand = candidates[: cfg["n"]]
    scheme = cfg["scheme"]

    # 语义分量
    dists = [c["distance"] for c in cand]
    if scheme == "rrf_boost":
        sem = [1.0 / (RRF_K + rank) for rank in range(1, len(cand) + 1)]  # 候选已按距离升序
    elif cfg.get("sem_norm") == "minmax":
        sims = [-d for d in dists]
        lo, hi = min(sims), max(sims)
        # 映射到 [SEM_FLOOR, 1]，与生产 hybrid.sem_norm 一致
        sem = [
            SEM_FLOOR + (1.0 - SEM_FLOOR) * ((s - lo) / (hi - lo) if hi > lo else 1.0)
            for s in sims
        ]
    else:  # raw
        sem = [1.0 - d for d in dists]

    # 关键词分量
    kw = [kw_score(c["text"], keywords, cfg.get("k1", 1.0)) for c in cand]

    # 组合
    lam = cfg.get("lam", 0.0)
    scores = []
    if scheme == "semantic_only":
        scores = sem
    elif scheme == "additive":
        scores = [sem[i] + lam * kw[i] for i in range(len(cand))]
    elif scheme == "multiplicative":
        scores = [sem[i] * (1.0 + lam * kw[i]) for i in range(len(cand))]
    elif scheme == "rrf_boost":
        # 关键词按 kw_score 排名，转 RRF 项叠加
        order = sorted(range(len(cand)), key=lambda i: -kw[i])
        kw_rank = {i: r for r, i in enumerate(order, 1)}
        scores = [
            sem[i] + (lam * 1.0 / (RRF_K + kw_rank[i]) if kw[i] > 0 else 0.0)
            for i in range(len(cand))
        ]

    # path 去重取最高分
    best: dict[str, float] = {}
    for c, s in zip(cand, scores):
        p = c["path"]
        if p not in best or s > best[p]:
            best[p] = s
    return [p for p, _ in sorted(best.items(), key=lambda x: -x[1])]


# ── 指标 ──────────────────────────────────────────────
def ndcg_at_k(ranked: list[str], gold: set[str], k: int = 10) -> float:
    dcg = sum(1.0 / math.log2(i + 2) for i, p in enumerate(ranked[:k]) if p in gold)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(len(gold), k)))
    return dcg / idcg if idcg > 0 else 0.0


def mrr(ranked: list[str], gold: set[str]) -> float:
    for i, p in enumerate(ranked, 1):
        if p in gold:
            return 1.0 / i
    return 0.0


def recall_at_k(ranked: list[str], gold: set[str], k: int = 5) -> float:
    return len(set(ranked[:k]) & gold) / len(gold) if gold else 0.0


# ── 网格枚举 ──────────────────────────────────────────
def all_configs() -> list[dict]:
    cfgs = []
    for n in N_GRID:
        cfgs.append({"scheme": "semantic_only", "n": n})
        for k1, lam in product(K1_GRID, LAM_GRID):
            cfgs.append({"scheme": "rrf_boost", "n": n, "k1": k1, "lam": lam})
            for sn in SEMNORM_GRID:
                for scheme in ("additive", "multiplicative"):
                    cfgs.append(
                        {"scheme": scheme, "n": n, "k1": k1, "lam": lam, "sem_norm": sn}
                    )
    return cfgs


def cfg_label(cfg: dict) -> str:
    parts = [cfg["scheme"], f"n{cfg['n']}"]
    if "k1" in cfg:
        parts.append(f"k1={cfg['k1']}")
    if "lam" in cfg:
        parts.append(f"λ={cfg['lam']}")
    if "sem_norm" in cfg:
        parts.append(cfg["sem_norm"])
    return " ".join(parts)


# ── 主流程 ────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="强制重建缓存")
    ap.add_argument("--top", type=int, default=25, help="排行榜显示条数")
    args = ap.parse_args()

    cases = yaml.safe_load(TESTSET.read_text())
    for c in cases:
        c.setdefault("keywords", [])
        c["_gold"] = set(c["gold"])

    print("检查/构建缓存（embedding + 候选，仅缺失或 --rebuild 时调 API）…")
    ensure_cache(cases, rebuild=args.rebuild)
    cands = load_cache(cases)

    configs = all_configs()
    print(f"评测 {len(cases)} 条 query × {len(configs)} 套配置 …")

    rows = []
    for cfg in configs:
        nd = mr = rc = 0.0
        for c, cand in zip(cases, cands):
            ranked = fuse(cand, c["keywords"], cfg)
            nd += ndcg_at_k(ranked, c["_gold"])
            mr += mrr(ranked, c["_gold"])
            rc += recall_at_k(ranked, c["_gold"])
        n = len(cases)
        rows.append((cfg, nd / n, mr / n, rc / n))

    rows.sort(key=lambda r: (r[1], r[2]), reverse=True)
    base = next(r for r in rows if r[0]["scheme"] == "semantic_only")

    lines = [
        "# 融合网格搜索结果",
        "",
        f"评测集 {len(cases)} 条 query，{len(configs)} 套配置。主指标 nDCG@10（gold 被噪声挤下 → 分降）。",
        "",
        f"**纯语义基线**：nDCG@10={base[1]:.4f}  MRR={base[2]:.4f}  Recall@5={base[3]:.4f}",
        "",
        "| # | 配置 | nDCG@10 | MRR | Recall@5 | vs 基线 |",
        "|---|------|---------|-----|----------|--------|",
    ]
    for i, (cfg, nd, mr, rc) in enumerate(rows[: args.top], 1):
        delta = nd - base[1]
        lines.append(
            f"| {i} | {cfg_label(cfg)} | {nd:.4f} | {mr:.4f} | {rc:.4f} | {delta:+.4f} |"
        )
    RESULTS.write_text("\n".join(lines) + "\n")
    print(f"\n排行榜写入 {RESULTS}")
    for i, (cfg, nd, mr, rc) in enumerate(rows[:10], 1):
        print(f"  {i:2}. {cfg_label(cfg):48} nDCG={nd:.4f} MRR={mr:.4f} R@5={rc:.4f}")
    print(f"  基线 semantic_only: nDCG={base[1]:.4f}")


if __name__ == "__main__":
    main()
