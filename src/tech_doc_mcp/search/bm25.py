"""
BM25 词法搜索 — 精确匹配关键词，支持中英文混合分词
"""

import re

import jieba
from rank_bm25 import BM25Okapi

# 提取英文单词（字母、数字、下划线）
_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")
# 检测是否包含中文
_CJK_RE = re.compile(r"[一-鿿]")


def tokenize(text: str) -> list[str]:
    """中英文混合分词，统一小写

    - 含中文 → jieba 精确切词
    - 纯英文 → 正则提取单词，自动去标点
    """
    text = text.lower()
    if _CJK_RE.search(text):
        tokens = jieba.lcut(text)
        return [t for t in tokens if t.strip()]
    else:
        return _WORD_RE.findall(text)


class BM25Searcher:
    """BM25 词法搜索引擎

    不涉及磁盘 I/O，内存常驻。
    """

    def __init__(self):
        self._bm25: BM25Okapi | None = None
        self._texts: list[str] = []
        self._metas: list[dict] = []
        self.chunk_count: int = 0

    def index(self, texts: list[str], metas: list[dict]) -> None:
        """构建 BM25 索引（含分词）"""
        self._texts = texts
        self._metas = metas
        self.chunk_count = len(texts)
        tokenized = [tokenize(t) for t in texts]
        self._bm25 = BM25Okapi(tokenized)

    @property
    def is_empty(self) -> bool:
        return self._bm25 is None or self.chunk_count == 0

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """搜索，返回 top-N 结果"""
        if self.is_empty:
            return []

        tokens = tokenize(query)
        if not tokens:
            return []

        scores = self._bm25.get_scores(tokens)
        indexed = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)

        results = []
        for idx, score in indexed[:limit]:
            if score <= 0:
                continue
            results.append({
                "text": self._texts[idx],
                "metadata": self._metas[idx],
                "score": round(float(score), 4),
            })
        return results
