"""
文档分块器 — 对应 LlamaIndex MarkdownNodeParser

将 SourceDocument 按 Markdown 结构切分为可检索的 DocChunk。

分块策略:
    1. 按 ## 标题边界切分（代码块保护）
    2. 短 section 合并（去代码后 <300 字向后合并，上限 800）
    3. 阶梯切分：≤1000 不切 / 1000-1600 中点切 2 段 / >1600 每 ~600 切
    4. 子 chunk 带父标题，不在自然边界之外加 overlap
"""

import re
from tech_doc_mcp.store.models import SourceDocument, DocChunk




def chunk_document(
    doc: SourceDocument,
    chunk_size: int = 1024,
) -> list[DocChunk]:
    """将单个 SourceDocument 切分为 DocChunk 列表

    Args:
        doc:        源文档
        chunk_size: 目标字符数（去代码后），默认 1024

    Returns:
        DocChunk 列表
    """
    # 第一步：按 ## 标题边界切分 → 合并短 section
    sections = _split_by_headings(doc.text)
    sections = _merge_short_sections(sections)

    # 第二步：阶梯切分
    all_texts: list[str] = []
    for section in sections:
        if _should_skip(section):
            continue
        for text in _split_long_section(section, chunk_size):
            if not text.strip():
                continue
            all_texts.append(text)

    # 第三步：构建 DocChunk
    chunks: list[DocChunk] = []
    for i, text in enumerate(all_texts):
        full_text = text.strip()
        chunks.append(
            DocChunk(
                id=f"{doc.id}::{i}",
                document_id=doc.id,
                source=doc.source,
                path=doc.path,
                chunk_index=i,
                text=full_text,
                text_for_embedding=_strip_code_blocks(full_text),
                metadata={**doc.metadata},
            )
        )

    return chunks


# ── 第一步：按标题切分 ─────────────────────────────────

def _split_by_headings(text: str) -> list[str]:
    """按 ## / ### 标题边界将文本切为段落

    关键规则：
        - 代码块(```...```)内的 # 不算标题，不在这里切分
        - 保留标题文本作为每个 section 的开头

    Args:
        text: Markdown 原文

    Returns:
        按标题分割的段落列表
    """
    # 用占位符保护代码块
    code_blocks = []

    def _hide_code(m: re.Match) -> str:
        code_blocks.append(m.group())
        return f"__CODE_BLOCK_{len(code_blocks) - 1}__"

    protected = re.sub(r"```[\s\S]*?```", _hide_code, text)

    # 按 ## 标题切分（不包含 # 一级标题，因为一个文件只有一个）
    # 使用正向前瞻 (?=^##\s) 保留分隔符
    sections = re.split(r"(?=^##\s)", protected, flags=re.MULTILINE)

    # 恢复代码块
    restored = []
    for section in sections:
        for i, block in enumerate(code_blocks):
            section = section.replace(f"__CODE_BLOCK_{i}__", block)
        restored.append(section)

    return [s for s in restored if s.strip()]


# ── 第二步：阶梯切分超长段落 ──────────────────────────

def _find_best_split(text: str, target: int, margin: int = 300) -> int:
    r"""在 target ± margin 范围内找最佳切分点

    优先级: 段落(\n\n) > 句子(。！？\s+[A-Z一-鿿]) > 行边界(\n)
    返回字符索引位置。
    """
    left = max(0, target - margin)
    right = min(len(text), target + margin)
    region = text[left:right]
    candidates: list[tuple[int, int, int]] = []  # (priority, distance, pos)

    # Priority 0: paragraph boundary
    for m in re.finditer(r"\n\s*\n", region):
        pos = left + m.start()
        if pos > 0:
            candidates.append((0, abs(pos - target), pos))

    # Priority 1: sentence boundary
    for m in re.finditer(r"[.!?。！？]\s+[A-Z一-鿿]", region):
        pos = left + m.start() + 1
        if pos > 0:
            candidates.append((1, abs(pos - target), pos))

    # Priority 2: line boundary
    for m in re.finditer(r"\n", region):
        pos = left + m.start()
        if pos > 0:
            candidates.append((2, abs(pos - target), pos))

    if not candidates:
        return target

    candidates.sort(key=lambda x: (x[0], x[1]))
    return candidates[0][2]


def _split_long_section(section: str, max_chars: int = 1024) -> list[str]:
    """阶梯式切分超长段落

    保护代码块 → 按去代码后长度分级:
        ≤1000 → 不切
        1000-1600 → 中点找边界，切 2 段
        >1600 → 每 ~600 字找边界切
    """
    # 保护代码块，在纯文本上做切分决策
    code_blocks: list[str] = []

    def _hide(m: re.Match) -> str:
        code_blocks.append(m.group())
        return f"\n__CB{len(code_blocks) - 1}__\n"

    protected = re.sub(r"```[\s\S]*?```", _hide, section)
    content_len = len(protected)

    # 提取父标题（section 内第一个 ## 标题），切分后拼到子 chunk 前面
    heading_match = re.search(r"(##\s+.+?)\n\s*\n", protected)
    heading = heading_match.group(1) if heading_match else ""

    if content_len <= 1000:
        parts = [protected]
    elif content_len <= 1600:
        target = content_len // 2
        split_at = _find_best_split(protected, target, margin=400)
        parts = [protected[:split_at].strip(), protected[split_at:].strip()]
    else:
        parts = []
        remaining = protected
        while len(remaining) > 1000:
            target = min(600, len(remaining))
            split_at = _find_best_split(remaining, target, margin=300)
            if split_at <= 0:
                split_at = target
            parts.append(remaining[:split_at].strip())
            remaining = remaining[split_at:].strip()
        if remaining.strip():
            parts.append(remaining.strip())

    # 第 2 个及以后的 chunk 前面拼父标题
    if heading and len(parts) > 1:
        for j in range(1, len(parts)):
            parts[j] = heading + "\n\n" + parts[j]

    # 恢复代码块
    result = []
    for part in parts:
        for i, block in enumerate(code_blocks):
            part = part.replace(f"__CB{i}__", block)
        stripped = part.strip()
        if stripped:
            result.append(stripped)
    return result if result else [section]


# ── 辅助 ──────────────────────────────────────────────

def _merge_short_sections(
    sections: list[str],
    min_chars: int = 300,
    max_chars: int = 800,
) -> list[str]:
    """将去代码后 <300 字符的短 section 合并到上一个（不超 800）"""
    if len(sections) <= 1:
        return sections

    merged = [sections[0]]
    for s in sections[1:]:
        s_len = len(_strip_code_blocks(s))
        prev_len = len(_strip_code_blocks(merged[-1]))
        if s_len < min_chars and prev_len + s_len <= max_chars:
            merged[-1] = merged[-1] + "\n\n" + s
        else:
            merged.append(s)

    # 首个 section 无上一邻居可合并，单独检查：短的话向前合并（不限 max_chars，交给阶梯切分处理）
    if len(merged) >= 2 and len(_strip_code_blocks(merged[0])) < min_chars:
        merged[1] = merged[0] + "\n\n" + merged[1]
        merged = merged[1:]

    return merged


def _strip_code_blocks(text: str) -> str:
    """去除 ``` 代码块，保留行内代码和标题/文本"""
    return re.sub(r"```[\s\S]*?```", "", text)


def _should_skip(text: str) -> bool:
    """跳过内容过少或纯空白的段落"""
    stripped = re.sub(r"\s+", "", text)
    return len(stripped) < 10
