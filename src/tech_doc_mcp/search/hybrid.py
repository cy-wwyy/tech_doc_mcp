"""
混合搜索 —— 语义为主干，关键词按 presence + 饱和做乘性加权重排(boost-only)。

设计与选参的完整依据见 docs/search-fusion-redesign.md。要点:
  - 语义:ChromaDB 向量(基于去代码正文),负责主召回与主排序。
  - 关键词:在候选的完整文本(含代码)上按 presence 计数 + BM25 式饱和,
            只对语义候选重排、不引入语义未召回的页(boost-only),根除污染。
  - 融合:final = sem_norm · (1 + λ · kw_score)。乘性使关键词按语义比例放大,
          弱语义页天然压住 —— 经网格搜索 + LLM-judge 交叉验证选定,对 λ 不敏感。
"""

from tech_doc_mcp.store.vector_store import VectorStore

import re

# ── 融合参数(由 eval/ 网格搜索 + LLM-judge 敲定,详见 design doc)──
N_CANDIDATES = 30   # 语义候选池;实测 gold 100% 落在 top-30 内
K1 = 2.0            # 饱和常数:count 1↔3 有别、3↔300 趋同;越大低端分辨率越高
LAMBDA = 0.5        # 关键词权重
SEM_FLOOR = 0.1     # 语义归一化下限:避免候选池最差项被归一化到 0 而彻底压死


def count_token(text: str, kw: str) -> int:
    """统计 kw 作为独立 token 出现的次数(词边界感知)。

    用词边界而非纯子串,避免短/常见关键词误命中更长标识符内部
    (如 "id" 命中 "video"/"grid"/"identifier")。对以词字符结尾/开头的边
    施加 (?<!\\w)/(?!\\w) 断言;含符号的边(如 "@apply"、"table=True")不施加,
    以兼容非标识符关键词。
    """
    if not kw:
        return 0
    left = r"(?<!\w)" if (kw[0].isalnum() or kw[0] == "_") else ""
    right = r"(?!\w)" if (kw[-1].isalnum() or kw[-1] == "_") else ""
    return len(re.findall(left + re.escape(kw) + right, text))


def _saturate(count: int, k1: float = K1) -> float:
    """BM25 式饱和:出现 1 次有意义，300 次≈3 次。"""
    return count / (count + k1) if count > 0 else 0.0


def _kw_score(text: str, keywords: list[str]) -> float:
    """presence + 饱和:Σ sat(count(kw)) / n_kw ∈ [0,1]。

    在完整文本(含代码)上计数 —— 纯代码 API 标识符正是关键词的价值所在;
    用 presence 饱和而非原始词频，免疫代码符号高频膨胀。
    """
    kws = [k for k in keywords if k]
    if not kws:
        return 0.0
    return sum(_saturate(count_token(text, kw)) for kw in kws) / len(kws)


class HybridSearcher:
    """混合搜索引擎(无内存索引，无启动预热)。"""

    def __init__(self, source: str, version: str, store: VectorStore):
        self.source = source
        self.version = version
        self._store = store

    def search(
        self,
        query: str,
        query_embedding: list[float],
        keywords: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """混合搜索

        Args:
            query:           语义查询文本(当前融合仅用向量，此参数保留以兼容/备用)
            query_embedding: 查询向量
            keywords:        可选，精确关键词列表，如 ["Depends", "JWT token"]
            limit:           返回条数
        """
        cand = self._store.query(
            self.source, self.version, query_embedding, n_results=N_CANDIDATES,
        )
        if not cand:
            return []

        # 语义分量:min-max 归一化到 [0,1]（候选内，最优=1）。距离越小越相似 → 取负。
        sims = [-(c.get("score") or 0.0) for c in cand]
        lo, hi = min(sims), max(sims)

        def sem_norm(s: float) -> float:
            # 映射到 [SEM_FLOOR, 1]：最差候选仍保留基础分，精确命中关键词者不被压死
            ratio = (s - lo) / (hi - lo) if hi > lo else 1.0
            return SEM_FLOOR + (1.0 - SEM_FLOOR) * ratio

        # 乘性融合:final = sem · (1 + λ · kw)
        for c, s in zip(cand, sims):
            kw = _kw_score(c.get("text") or "", keywords or [])
            c["score"] = sem_norm(s) * (1.0 + LAMBDA * kw)

        # 按 path 去重取最高分 + 排序 + 截断
        best_per_path: dict[str, dict] = {}
        for item in cand:
            path = (item.get("metadata") or {}).get("path", "")
            if path not in best_per_path or item["score"] > best_per_path[path]["score"]:
                best_per_path[path] = item

        deduped = sorted(best_per_path.values(), key=lambda x: x["score"], reverse=True)
        return deduped[:limit]
