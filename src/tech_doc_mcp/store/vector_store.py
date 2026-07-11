"""
ChromaDB 向量存储 — 封装 collection 的 CRUD 操作

设计参考 LlamaIndex 的 VectorStore 抽象:
    - 每个文档源一个 collection（隔离）
    - 存储: id, embedding, document(text), metadata
    - 支持 query / get / delete / count
"""

from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

from tech_doc_mcp.logging import get_logger
from tech_doc_mcp.store.models import DocChunk, SourceInfo

logger = get_logger(__name__)


class VectorStore:
    """ChromaDB 向量存储封装

    每个 source+version 组合对应一个 ChromaDB collection:
        docs_{source}_{version}  如 docs_fastapi_0.139.0

    新旧版本并存，删除/重建互不影响。
    """

    def __init__(self, persist_dir: str | Path = "data/chroma"):
        self.persist_dir = str(persist_dir)
        self._client = chromadb.PersistentClient(
            path=self.persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )

    # ── Collection 管理 ───────────────────────────────

    def _collection_name(self, source: str, version: str = "") -> str:
        """生成 collection 名称，带版本号"""
        base = f"docs_{source}"
        if version:
            base = f"{base}_{version}"
        return base

    def get_or_create_collection(self, source: str, version: str = ""):
        """获取或创建指定文档源+版本的 collection

        metadata 存储 source 和 version，list_sources 优先读 metadata 而非解析名称。
        """
        name = self._collection_name(source, version)
        return self._client.get_or_create_collection(
            name=name,
            metadata={"source": source, "version": version},
        )

    def list_collections(self) -> list[str]:
        """列出所有 collection 名"""
        return [c.name for c in self._client.list_collections()]

    # ── 写入 ──────────────────────────────────────────

    MAX_BATCH = 5000  # ChromaDB 单次插入上限

    def insert(self, source: str, version: str, chunks: list[DocChunk]) -> int:
        """批量插入 chunks（自动分批）"""
        if not chunks:
            return 0

        collection = self.get_or_create_collection(source, version)
        total = 0

        for i in range(0, len(chunks), self.MAX_BATCH):
            batch = chunks[i : i + self.MAX_BATCH]

            ids = [c.id for c in batch]
            embeddings = [c.embedding for c in batch]
            documents = [c.text for c in batch]
            metadatas = [
                {
                    "document_id": c.document_id,
                    "source": c.source,
                    "version": version,
                    "path": c.path,
                    "chunk_index": c.chunk_index,
                    "content_type": c.metadata.get("content_type", "doc"),
                    **c.metadata,
                }
                for c in batch
            ]

            collection.add(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)
            total += len(batch)

        return total

    # ── 查询 ──────────────────────────────────────────

    def query(
        self,
        source: str,
        version: str,
        query_embedding: list[float],
        n_results: int = 10,
    ) -> list[dict]:
        """语义搜索"""
        collection = self.get_or_create_collection(source, version)
        result = collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )
        return self._format_results(result)

    def keyword_search(
        self,
        source: str,
        version: str,
        keyword: str,
        limit: int = 50,
    ) -> list[dict]:
        """关键词搜索 — 使用 ChromaDB 全文索引"""
        collection = self.get_or_create_collection(source, version)
        result = collection.get(
            where_document={"$contains": keyword},
            limit=limit,
            include=["documents", "metadatas"],
        )
        return self._format_get_results(result, keyword)

    def get_context(
        self,
        source: str,
        version: str,
        document_id: str,
        chunk_index: int,
        count: int = 2,
    ) -> list[str]:
        """获取同一文档中指定 chunk 之后的 count 个 chunk 文本"""
        collection = self.get_or_create_collection(source, version)
        results: list[str] = []
        for offset in range(1, count + 1):
            result = collection.get(
                where={
                    "$and": [
                        {"document_id": document_id},
                        {"chunk_index": chunk_index + offset},
                    ],
                },
                limit=1,
                include=["documents"],
            )
            if result["documents"]:
                results.append(result["documents"][0])
        return results

    def get_all(self, source: str, version: str) -> dict:
        """获取 collection 的全部数据（documents + metadatas），供 BM25 初始化"""
        collection = self.get_or_create_collection(source, version)
        return collection.get(include=["documents", "metadatas"])

    def update_content_type(self, source: str, version: str, content_type: str = "doc") -> int:
        """为已有 collection 的所有 chunk 补 content_type 字段"""
        collection = self.get_or_create_collection(source, version)
        data = collection.get(include=["metadatas"])
        if not data["ids"]:
            return 0

        ids = data["ids"]
        new_metas = []
        for meta in (data["metadatas"] or []):
            m = dict(meta)
            if "content_type" not in m:
                m["content_type"] = content_type
            new_metas.append(m)

        collection.update(ids=ids, metadatas=new_metas)
        return len(ids)

    # ── 管理 ──────────────────────────────────────────

    def count(self, source: str, version: str) -> int:
        collection = self.get_or_create_collection(source, version)
        return collection.count()

    def delete_collection(self, source: str, version: str) -> bool:
        """删除指定 source+version 的 collection

        Returns:
            True 表示已删除，False 表示 collection 本来就不存在
        """
        name = self._collection_name(source, version)
        try:
            self._client.delete_collection(name=name)
            logger.info("已删除旧 collection: %s", name)
            return True
        except Exception:
            # collection 不存在 — 正常情况（首次索引）
            return False

    def list_sources(self) -> list[SourceInfo]:
        """列出所有已索引的文档源→版本

        source/version 优先从 collection metadata 读取，
        无 metadata 时回退到解析 collection name。
        """
        result: dict[str, SourceInfo] = {}
        for collection in self._client.list_collections():
            meta = getattr(collection, "metadata", None) or {}

            source_name = meta.get("source") or self._parse_source_from_name(collection.name)
            version = meta.get("version") or self._parse_version_from_name(collection.name)
            chunk_count = collection.count()

            # 同一 source+version 去重（优先保留 metadata 完整的）
            key = f"{source_name}@{version}"
            if key not in result:
                result[key] = SourceInfo(
                    name=source_name,
                    version=version,
                    language="",
                    chunk_count=chunk_count,
                )
        return list(result.values())

    # ── 内部 ──────────────────────────────────────────

    @staticmethod
    def _parse_source_from_name(name: str) -> str:
        """从 collection name 回退解析 source（无 metadata 时）"""
        import re
        inner = name.replace("docs_", "", 1)
        # 匹配 name 末尾 _x.y.z 版本号
        m = re.match(r"(.+)_(\d+\.\d+\.\d+)", inner)
        if m:
            return m.group(1)
        return inner.rsplit("_", 1)[0]

    @staticmethod
    def _parse_version_from_name(name: str) -> str:
        """从 collection name 回退解析 version（无 metadata 时）"""
        import re
        inner = name.replace("docs_", "", 1)
        m = re.match(r"(.+)_(\d+\.\d+\.\d+)", inner)
        if m:
            return m.group(2)
        parts = inner.rsplit("_", 1)
        return parts[1] if len(parts) == 2 else "unknown"

    def _format_results(self, result: dict) -> list[dict]:
        """将 ChromaDB query 返回格式化为 list[dict]"""
        formatted = []
        if not result.get("ids") or not result["ids"][0]:
            return formatted

        ids = result["ids"][0]
        docs = result.get("documents", [[None]])[0]
        metas = result.get("metadatas", [[None]])[0]
        distances = result.get("distances", [[None]])[0]

        for i, id_ in enumerate(ids):
            item = {
                "id": id_,
                "text": docs[i] if i < len(docs) else None,
                "metadata": metas[i] if i < len(metas) else None,
                "score": distances[i] if i < len(distances) else None,
            }
            formatted.append(item)
        return formatted

    def _format_get_results(self, result: dict, keyword: str) -> list[dict]:
        """将 ChromaDB get 返回格式化为 list[dict]，附匹配上下文"""
        formatted = []
        ids = result.get("ids", [])
        docs = result.get("documents", [])
        metas = result.get("metadatas", [])

        keyword_lower = keyword.lower()
        for i, id_ in enumerate(ids):
            text = docs[i] if i < len(docs) else ""
            meta = metas[i] if i < len(metas) else {}
            # 提取命中关键词的上下文（前后 50 字符）
            ctx = ""
            if text:
                pos = text.lower().find(keyword_lower)
                if pos >= 0:
                    start = max(0, pos - 50)
                    end = min(len(text), pos + len(keyword) + 50)
                    ctx = text[start:end].replace("\n", " ")
                    if start > 0:
                        ctx = "..." + ctx
                    if end < len(text):
                        ctx = ctx + "..."

            formatted.append({
                "id": id_,
                "text": text,
                "metadata": meta,
                "context": ctx,
            })
        return formatted
