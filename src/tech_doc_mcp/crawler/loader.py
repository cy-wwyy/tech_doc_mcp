"""
文档加载器 — 对应 LlamaIndex SimpleDirectoryReader

从本地 docs 目录读取 Markdown 文件，构造 SourceDocument 对象。
"""

import re
from pathlib import Path

from tech_doc_mcp.store.models import SourceDocument


def load_documents(source: str, docs_dir: Path) -> list[SourceDocument]:
    """从目录加载所有 Markdown 文件为 SourceDocument 列表

    对应 LlamaIndex:
        SimpleDirectoryReader("./data").load_data() → list[Document]

    Args:
        source:    文档源名称，如 "fastapi"
        docs_dir:  文档目录，如 Path("docs/fastapi/clean")

    Returns:
        SourceDocument 列表，按路径排序
    """
    md_files = sorted(docs_dir.glob("**/*.md"))
    documents = []

    for md_file in md_files:
        text = md_file.read_text(encoding="utf-8")
        rel_path = str(md_file.relative_to(docs_dir))

        # 解析 YAML frontmatter 作为 metadata
        metadata = _parse_frontmatter(text)
        # 去除 frontmatter，保留纯正文
        clean_text = _strip_frontmatter(text)

        doc_id = f"{source}::{rel_path}"
        doc_id = _normalize_id(doc_id)

        documents.append(
            SourceDocument(
                id=doc_id,
                source=source,
                path=rel_path,
                text=clean_text,
                metadata={
                    "file_name": md_file.name,
                    **metadata,
                },
            )
        )

    return documents


# ── 内部辅助 ───────────────────────────────────────────

def _parse_frontmatter(text: str) -> dict[str, str]:
    """解析 Markdown 文件开头的 YAML frontmatter (--- 包裹)

    示例:
        ---
        source: https://fastapi.tiangolo.com/tutorial/first-steps/
        title: First Steps
        ---
        # First Steps
        ...

    返回: {"source": "...", "title": "First Steps"}
    """
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not match:
        return {}

    frontmatter = match.group(1)
    metadata = {}
    for line in frontmatter.split("\n"):
        line = line.strip()
        if ":" in line and not line.startswith("#"):
            key, _, value = line.partition(":")
            metadata[key.strip()] = value.strip()
    return metadata


def _strip_frontmatter(text: str) -> str:
    """去除 YAML frontmatter，保留正文"""
    return re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.DOTALL)


def _normalize_id(id_str: str) -> str:
    """规范化 id：移除 .md 后缀，/index 简化为目录"""
    id_str = id_str.replace(".md", "")
    id_str = re.sub(r"/index$", "", id_str)
    return id_str
