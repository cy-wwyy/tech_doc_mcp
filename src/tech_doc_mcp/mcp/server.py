"""
MCP Server — 面向 Agent 的技术文档搜索

工具:
    search_docs   — 语义 + 关键词混合搜索
    read_doc      — 读取完整文档页面
    list_sources  — 列出已索引的文档源
    list_docs     — 浏览文档目录结构
"""

from pathlib import Path
from textwrap import dedent

from fastmcp import FastMCP

from tech_doc_mcp.client import get_embedding_client, embed_one
from tech_doc_mcp.store.vector_store import VectorStore
from tech_doc_mcp.search.hybrid import HybridSearcher

mcp = FastMCP(
    name="tech-doc-mcp",
    instructions=dedent("""\
        Tech Doc MCP — 本地技术文档搜索引擎。
        先调用 list_sources 获取可用文档源列表。
        再调用 search_docs 搜索文档。
        需要完整页面内容时调用 read_doc。
    """),
)

# ── 惰性初始化 ────────────────────────────────────────

_store: VectorStore | None = None
_embed_client: object | None = None
_searchers: dict[str, HybridSearcher] = {}


def _get_store() -> VectorStore:
    global _store
    if _store is None:
        _store = VectorStore()
    return _store


def _get_embed_client():
    global _embed_client
    if _embed_client is None:
        _embed_client = get_embedding_client()
    return _embed_client


# ── 辅助 ──────────────────────────────────────────────

def _resolve_version(source: str) -> str:
    """找到指定 source 的最新版本"""
    sources = _get_store().list_sources()
    versions = [s.version for s in sources if s.name == source]
    if not versions:
        raise ValueError(f"未找到文档源: {source}")
    # 多版本时取最大的（按版本号排序）
    return sorted(versions, reverse=True)[0]


def _embed(query: str) -> list[float]:
    """将查询文本转为向量"""
    return embed_one(_get_embed_client(), query)


def _get_searcher(source: str) -> HybridSearcher:
    """获取或创建 HybridSearcher（按 source+version 缓存）"""
    version = _resolve_version(source)
    key = f"{source}@{version}"
    if key not in _searchers:
        _searchers[key] = HybridSearcher(source, version, _get_store())
    return _searchers[key]


def _format_search_results(
    results: list[dict], source: str, version: str, store: VectorStore,
) -> str:
    """格式化搜索结果，附后续 2 个 chunk 作为上下文"""
    if not results:
        return f"No results found (source={source} v{version})"

    lines = [f"source: {source} v{version}", f"count: {len(results)}", ""]
    for i, r in enumerate(results, 1):
        meta = r.get("metadata", {})
        path = meta.get("path", "unknown")
        score = r.get("score", 0)
        text = r.get("text", "").replace("\n", " ")
        doc_id = meta.get("document_id", "")
        chunk_idx = meta.get("chunk_index", 0)
        title = meta.get("title", "")
        url = meta.get("url", meta.get("source", ""))
        content_type = meta.get("content_type", "doc")

        lines.append("---")
        lines.append(f"rank: {i}")
        lines.append(f"path: {path}")
        lines.append(f"title: {title}")
        lines.append(f"url: {url}")
        lines.append(f"type: {content_type}")
        lines.append(f"score: {score:.4f}")
        lines.append(f"text: {text}")

        # 附上下文 chunk（同一文档后续 2 个）
        ctx = store.get_context(source, version, doc_id, chunk_idx, count=2)
        if ctx:
            for j, ctx_text in enumerate(ctx, 1):
                lines.append(f"context_{j}: {ctx_text.replace(chr(10), ' ')}")
    return "\n".join(lines)


# ── 工具 ──────────────────────────────────────────────

@mcp.tool
def search_docs(
    query: str,
    source: str,
    keywords: list[str] | None = None,
    limit: int = 10,
) -> str:
    """搜索技术文档。用于查找 API 用法、配置方式、代码示例等。

    检索策略:语义(向量)为主召回与主排序；keywords 作为精确信号，把"确实出现这些
    符号"的语义相关页往前提(boost-only:只重排语义候选，不会引入无关页)。

    参数:
        query:    自然语言查询，描述要搜索的概念。如 "how to implement OAuth2 authentication"
        source:   文档源名称，如 "fastapi"。用 list_sources 查看可用源。
        keywords: 可选，精确标识符列表(API 名/函数名/配置项)，如 ["OAuth2PasswordBearer", "create_access_token"]。
                  何时用:答案必须包含某个确切符号、尤其是只出现在代码里的 API 名时。
                  安全:只对语义相关结果重排，不引入无关页、不会让结果变差；出现几次不影响(按是否出现计)。
                  建议:用独特标识符，避免 "id"/"get"/"in" 这类过短或过泛的词。
        limit:    返回条数，默认 10。

    返回格式:
        source: <name> <version>
        count: <n>
        ---
        rank: <i>
        path: <document_path>
        score: <relevance_score>
        text: <full_chunk_text>
    """
    version = _resolve_version(source)
    searcher = _get_searcher(source)
    embedding = _embed(query)
    results = searcher.search(query, embedding, keywords=keywords, limit=limit)
    return _format_search_results(results, source, version, _get_store())


@mcp.tool
def read_doc(path: str, source: str) -> str:
    """读取完整文档页面。先用 search_docs 获取 path，再传本工具读取全文。

    参数:
        path:   文档路径，来自 search_docs 返回的 path 字段
        source: 文档源名称
    """
    file_path = Path(f"docs/{source}/clean/{path}.md")
    if not file_path.exists():
        alt = Path(f"docs/{source}/clean/{path}")
        if alt.exists():
            file_path = alt
        else:
            return f"Document not found: {source}/{path}"

    content = file_path.read_text(encoding="utf-8")
    return content


@mcp.tool
def list_sources() -> str:
    """列出所有已索引的文档源。Agent 首次调用时使用，获取可用源列表。

    返回格式:
        name: <source_name>
        version: <version>
        chunks: <chunk_count>
    """
    sources = _get_store().list_sources()
    if not sources:
        return "No indexed sources available."

    lines = [f"total: {len(sources)}", ""]
    for s in sources:
        lines.append(f"name: {s.name}")
        lines.append(f"version: {s.version}")
        lines.append(f"chunks: {s.chunk_count}")
        lines.append("")
    return "\n".join(lines)


@mcp.tool
def list_docs(source: str, path: str | None = None) -> str:
    """浏览文档源目录结构。用于发现可用的文档路径。

    参数:
        source: 文档源名称
        path:   可选，子目录路径。不传则列出根目录。
    """
    base = Path(f"docs/{source}/clean")
    if not base.exists():
        return f"Source not found: {source}"

    target = base / (path or "")
    if not target.exists():
        return f"Path not found: {source}/{path or ''}"

    if target.is_file():
        content = target.read_text(encoding="utf-8")
        return content[:5000] if len(content) > 5000 else content

    lines = [f"directory: {source}/{path or ''}", ""]
    for item in sorted(target.iterdir()):
        name = item.name.replace(".md", "")
        item_type = "dir" if item.is_dir() else "file"
        lines.append(f"  [{item_type}] {name}")
    return "\n".join(lines)
