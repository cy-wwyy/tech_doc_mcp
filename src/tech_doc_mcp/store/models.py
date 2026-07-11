"""
数据模型 — 纯结构定义，不包含业务逻辑

对应 LlamaIndex 的 Document / Node 概念:
    SourceDocument  → python/framework/module_guides/loading/documents_and_nodes
    DocChunk        → TextNode (继承 Document metadata, 有 chunk 关系)
"""

from typing import Any

from pydantic import BaseModel, Field


# ── 文档源 — 一个完整的源文件 ──────────────────────

class SourceDocument(BaseModel):
    """一个文档源文件，对应 LlamaIndex 的 Document

    属性设计参考 LlamaIndex Document:
        text       — 原始全文
        metadata   — 文件级元数据（会被子 chunk 继承）
        source     — 所属文档源名称（如 "fastapi"）
        path       — 相对于文档源根目录的路径
    """
    id: str = Field(description="唯一标识，如 'fastapi::tutorial/first-steps'")
    source: str = Field(description="文档源名称，如 'fastapi'")
    path: str = Field(description="相对路径，如 'tutorial/first-steps.md'")
    text: str = Field(description="文件原始全文")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="文件级元数据，如 {'file_name': '...', 'url': '...'}，会被 chunk 继承"
    )


# ── 文档块 — 最小检索单元 ──────────────────────────

class DocChunk(BaseModel):
    """一个可检索的文本块，对应 LlamaIndex 的 TextNode

    属性设计参考 LlamaIndex TextNode + NodeRelationship:
        document_id    — PARENT 关系，指向 SourceDocument
        chunk_index    — 在源文件中的顺序位置
        text           — chunk 文本内容
        embedding      — 向量（索引后填入）
        metadata       — 继承自 SourceDocument + chunk 特有字段
    """
    id: str = Field(description="唯一标识，如 'fastapi::tutorial/first-steps::0'")
    document_id: str = Field(description="指向父文档的 id（PARENT 关系）")
    source: str = Field(description="文档源名称（从 Document 继承）")
    path: str = Field(description="文件路径（从 Document 继承）")
    chunk_index: int = Field(description="chunk 序号，从 0 开始")
    text: str = Field(description="完整文本（含代码块），存储于 ChromaDB，返回给 Agent")
    text_for_embedding: str = Field(
        default="",
        description="去掉代码块的纯自然语言文本，用于生成向量",
    )
    embedding: list[float] | None = Field(
        default=None,
        description="向量表示，索引阶段由 Embedder 填入"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="继承自父 Document 的 metadata + chunk 特有字段"
    )


# ── 文档源信息 — 管理界面的查询结果 ─────────────────

class SourceInfo(BaseModel):
    """文档源的汇总信息，供 list_sources 工具使用"""
    name: str = Field(description="文档源名称")
    version: str = Field(default="unknown", description="文档版本号")
    language: str = Field(default="", description="文档语言，如 'en', 'zh'")
    chunk_count: int = Field(default=0, description="总 chunk 数")
