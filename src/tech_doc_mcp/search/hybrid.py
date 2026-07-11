"""
混合搜索 — BM25 关键词 + 语义向量，RRF 融合

BM25 索引在初始化时从 ChromaDB 全量构建，内存常驻。
关键词路径和语义路径并行检索，RRF 融合排序。
"""

from tech_doc_mcp.search.bm25 import BM25Searcher
from tech_doc_mcp.store.vector_store import VectorStore


class HybridSearcher:
    """混合搜索引擎"""

    def __init__(self, source: str, version: str, store: VectorStore):
        self.source = source
        self.version = version
        self._store = store

        # 启动时一次性构建 BM25 索引
        self._bm25 = BM25Searcher()
        collection = store.get_or_create_collection(source, version)
        data = collection.get(include=["documents", "metadatas"])
        if data["ids"]:
            texts = data["documents"] or [""] * len(data["ids"])
            metas = data["metadatas"] or [{}] * len(data["ids"])
            self._bm25.index(texts, metas)

    @property
    def is_empty(self) -> bool:
        return self._bm25.is_empty

    def search(
        self,
        query: str,
        query_embedding: list[float],
        keywords: list[str] | None = None,
        limit: int = 10,
        rrf_k: int = 30,
    ) -> list[dict]:
        """混合搜索

        Args:
            query:           语义查询文本
            query_embedding: 查询向量
            keywords:        可选，精确关键词列表。如 ["Depends", "JWT token"]
            limit:           返回条数
            rrf_k:           RRF 平滑参数
        """
        fetch_n = max(limit * 3, 30)

        # 关键词路径（弱信号，0.3 权重；多取一些扩大覆盖面）
        kw_results: list[dict] = []
        if keywords:
            kw_query = " ".join(keywords)
            kw_results = self._bm25.search(kw_query, limit=fetch_n)

        # 语义路径
        semantic_results = self._store.query(
            self.source, self.version, query_embedding, n_results=fetch_n,
        )

        # RRF 融合
        merged = self._rrf_merge(kw_results, semantic_results, k=rrf_k)

        # 去重（同路径只保留最高分）+ 排序 + 截断
        best_per_path: dict[str, dict] = {}
        for item in merged:
            path = item.get("metadata", {}).get("path", "")
            if path not in best_per_path or item["score"] > best_per_path[path]["score"]:
                best_per_path[path] = item

        deduped = sorted(best_per_path.values(), key=lambda x: x["score"], reverse=True)
        return deduped[:limit]

    # ── 内部 ──────────────────────────────────────────

    def _rrf_merge(
        self,
        kw_results: list[dict],
        semantic_results: list[dict],
        k: int = 30,
    ) -> list[dict]:
        """加权 RRF 融合

        语义路权重 0.7，关键词路权重 0.3。
        关键词只作为弱信号辅助，不主导排序。
        """
        KW_W = 0.3
        SEM_W = 0.7
        id_map: dict[str, dict] = {}

        for rank, item in enumerate(kw_results, start=1):
            id_ = self._dedup_key(item)
            if id_ not in id_map:
                id_map[id_] = dict(item)
                id_map[id_]["score"] = 0.0
            id_map[id_]["score"] += KW_W / (k + rank)

        for rank, item in enumerate(semantic_results, start=1):
            id_ = self._dedup_key(item)
            if id_ not in id_map:
                id_map[id_] = dict(item)
                id_map[id_]["score"] = 0.0
            id_map[id_]["score"] += SEM_W / (k + rank)

        return list(id_map.values())

    @staticmethod
    def _dedup_key(item: dict) -> str:
        """用 path + chunk_index 做去重 key（两路结果均有此信息）"""
        meta = item.get("metadata", {})
        return f"{meta.get('path', '')}::{meta.get('chunk_index', 0)}"
