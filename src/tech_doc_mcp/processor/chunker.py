"""
文档分块器 — 对应 LlamaIndex MarkdownNodeParser

将 SourceDocument 按 Markdown 结构切分为可检索的 DocChunk。

分块策略（所有长度阈值由 chunk_size 派生，默认 1024）:
    1. 按 ## 标题边界切分（代码块保护）
    2. 短 section 合并（去代码后 <0.3×size 优先并入下一节，无视上限，超长交给阶梯切分）
    3. 阶梯切分：≤size 不切 / size~1.6×size 中点切 2 段 / >1.6×size 每 ~0.6×size 切
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
    sections = _merge_short_sections(sections, chunk_size)

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


def _split_long_section(section: str, chunk_size: int = 1024) -> list[str]:
    """阶梯式切分超长段落（阈值全部由 chunk_size 派生）

    保护代码块 → 按去代码后长度分级:
        ≤ chunk_size            → 不切
        chunk_size ~ 1.6×       → 中点找边界，切 2 段
        > 1.6×                  → 每 ~0.6× 找边界切
    """
    no_split = chunk_size              # ≤ 此值不切
    two_split = int(chunk_size * 1.6)  # ≤ 此值中点切 2 段
    step = int(chunk_size * 0.6)       # 阶梯切分的目标步长
    mid_margin = int(chunk_size * 0.4)
    step_margin = int(chunk_size * 0.3)

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

    if content_len <= no_split:
        parts = [protected]
    elif content_len <= two_split:
        target = content_len // 2
        split_at = _find_best_split(protected, target, margin=mid_margin)
        parts = [protected[:split_at].strip(), protected[split_at:].strip()]
    else:
        parts = []
        remaining = protected
        while len(remaining) > no_split:
            target = min(step, len(remaining))
            split_at = _find_best_split(remaining, target, margin=step_margin)
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
    chunk_size: int = 1024,
) -> list[str]:
    """短 section 优先并入**下一节**（阈值由 chunk_size 派生，无视上限）

    短节阈值 = 0.3×chunk_size。短节通常是引导后文的标题/导语（如 `## Examples`），
    应当带着后面的内容走 → 前向携带合并：短节暂存，前缀到下一节；连续短节累积。
    末尾残留的短节(无下一节)回退并入上一节；并后即便超长，`_split_long_section`
    会按 chunk_size 重新拆回。
    """
    min_chars = int(chunk_size * 0.3)
    if len(sections) <= 1:
        return sections

    result: list[str] = []
    carry = ""  # 待并入下一节的短节前缀
    for s in sections:
        s2 = (carry + s) if carry else s
        carry = ""
        if len(_strip_code_blocks(s2)) < min_chars:
            carry = s2 + "\n\n"  # 仍太短 → 继续携带到下一节
        else:
            result.append(s2)

    if carry:  # 末尾几节都短，无下一节可并
        tail = carry.rstrip()
        if result:
            result[-1] = result[-1] + "\n\n" + tail
        else:
            result.append(tail)

    return result


def _strip_code_blocks(text: str) -> str:
    """去除 ``` 代码块，保留行内代码和标题/文本"""
    return re.sub(r"```[\s\S]*?```", "", text)


def _should_skip(text: str) -> bool:
    """跳过内容过少或纯空白的段落"""
    stripped = re.sub(r"\s+", "", text)
    return len(stripped) < 10
