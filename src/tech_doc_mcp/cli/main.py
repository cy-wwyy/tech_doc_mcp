"""
CLI 入口 — 唯一启动入口
    tech-doc-mcp index   → 索引文档（clean/ → ChromaDB）
    tech-doc-mcp serve   → 启动 MCP Server（给 Claude Code 用）
    tech-doc-mcp web     → 启动 Web 管理界面（浏览器访问）
"""

from datetime import date
from pathlib import Path

import typer
from tech_doc_mcp.client import get_embedding_client, embed
from tech_doc_mcp.crawler.loader import load_documents
from tech_doc_mcp.logging import get_logger
from tech_doc_mcp.processor.chunker import chunk_document
from tech_doc_mcp.store.vector_store import VectorStore

logger = get_logger(__name__)
app = typer.Typer(name="tech-doc-mcp")


@app.command()
def index(
    source: str = typer.Argument(..., help="文档源名称，如 fastapi"),
    version: str = typer.Option(
        "", "--version", "-v",
        help="版本号，默认使用当天日期 (YYYY-MM-DD)",
    ),
    docs_dir: str = typer.Option(
        "", "--docs-dir", "-d",
        help="clean 文档目录，默认 docs/{source}/clean",
    ),
):
    """索引文档：加载 → 分块 → 向量化 → 写入 ChromaDB"""
    if not version:
        version = date.today().isoformat()

    clean_dir = Path(docs_dir) if docs_dir else Path(f"docs/{source}/clean")
    if not clean_dir.exists():
        logger.error("❌ 文档目录不存在: %s", clean_dir)
        raise typer.Exit(code=1)

    # 1. 加载
    logger.info("📖 加载文档: %s", clean_dir)
    docs = load_documents(source, clean_dir)
    logger.info("   共 %d 个文件", len(docs))

    # 2. 分块
    logger.info("✂️  分块中...")
    all_chunks = []
    for doc in docs:
        all_chunks.extend(chunk_document(doc))
    logger.info("   共 %d 个 chunk", len(all_chunks))

    # 3. 向量化
    logger.info("🧮 生成向量...")
    client = get_embedding_client()
    batch_size = 10

    for i in range(0, len(all_chunks), batch_size):
        batch = all_chunks[i : i + batch_size]
        texts = [c.text_for_embedding for c in batch]
        vectors = embed(client, texts)
        for chunk, vec in zip(batch, vectors):
            chunk.embedding = vec
        logger.info("   %d/%d", min(i + batch_size, len(all_chunks)), len(all_chunks))

    # 4. 写入 ChromaDB
    logger.info("💾 写入 ChromaDB...")
    store = VectorStore()
    store.delete_collection(source, version)
    count = store.insert(source, version, all_chunks)

    logger.info("")
    logger.info("✅ 完成: %s v%s — %d 页, %d chunks", source, version, len(docs), count)


@app.command()
def serve(
    transport: str = typer.Option(
        "streamable-http",
        "--transport", "-t",
        help="MCP transport: streamable-http | sse | stdio",
    ),
    host: str = typer.Option("127.0.0.1", "--host", "-h"),
    port: int = typer.Option(8000, "--port", "-p"),
):
    """启动 MCP Server，供 Claude Code 查询本地文档"""
    from tech_doc_mcp.mcp.server import mcp

    logger.info("🚀 MCP Server 启动: %s://%s:%s", transport, host, port)
    mcp.run(transport=transport, host=host, port=port)


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host", "-h"),
    port: int = typer.Option(8001, "--port", "-p"),
):
    """启动 Web 管理界面"""
    import uvicorn

    try:
        from tech_doc_mcp.web.app import create_app
    except ImportError:
        logger.warning("🚧 Web 管理界面尚未实现，敬请期待。")
        raise typer.Exit(code=1)

    web_app = create_app()
    logger.info("🌐 Web 管理界面: http://%s:%s", host, port)
    uvicorn.run(web_app, host=host, port=port)


if __name__ == "__main__":
    app()
