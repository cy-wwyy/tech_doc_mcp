"""
文档清洗器 — 调用 LLM 将 raw/ 中的半成品清洗为干净 Markdown

流程:
    docs/{source}/raw/*.md  →  LLM 清洗  →  docs/{source}/clean/*.md
"""

import asyncio
from pathlib import Path

from tech_doc_mcp.client import get_llm_client, get_llm_model, get_llm_extra_body
from tech_doc_mcp.logging import get_logger

logger = get_logger(__name__)

CLEAN_PROMPT = """你是一个技术文档清洗器。将以下从网页提取的内容转为干净的 Markdown。

规则：
- 保留所有技术信息、代码、API 文档、参数说明、函数签名，不得删改或改写
- 代码块用 ``` 包裹，标注语言
- 保留标题层级（# → ## → ###）
- 删除 HTML 标签（<font>、<span>、<u>、<b>、<div> 等）但保留内部文字
- 广告、赞助商信息、评论区、导航链接列表 → 删除
- 页面顶部文档元数据 → 删除
- 页面顶部/底部的"上一页/下一页"导航文字 → 删除
- 孤立的 "Tip"、"Note"、"Info"、"Warning" → 保留内容，转为 **提示：** 格式
- 如果整页都是导航/广告/赞助/非技术内容，只输出 [SKIP]
- 只输出最终 Markdown，不要解释或说明

原始内容：
{raw_content}"""

MAX_RETRIES = 3


async def clean_one(text: str) -> str:
    """清洗单页文档（含重试）"""
    client = get_llm_client()
    model = get_llm_model()
    extra_body = get_llm_extra_body()

    prompt = CLEAN_PROMPT.format(raw_content=text)

    for attempt in range(MAX_RETRIES):
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                extra_body=extra_body or {},
            )
            return resp.choices[0].message.content or ""
        except Exception:
            if attempt == MAX_RETRIES - 1:
                raise
            await asyncio.sleep(2 ** attempt)


async def clean_source(
    source: str,
    concurrency: int = 3,
) -> dict:
    """清洗整个文档源的 raw/ 到 clean/

    Args:
        source:      文档源名称，如 "fastapi"
        concurrency: LLM 并发数

    Returns:
        {"total": N, "success": N, "skipped": N, "failed": N}
    """
    raw_dir = Path(f"docs/{source}/raw")
    clean_dir = Path(f"docs/{source}/clean")

    if not raw_dir.exists():
        raise FileNotFoundError(f"raw 目录不存在: {raw_dir}")

    files = sorted(raw_dir.glob("**/*.md"))
    if not files:
        return {"total": 0, "success": 0, "skipped": 0, "failed": 0}

    clean_dir.mkdir(parents=True, exist_ok=True)

    sem = asyncio.Semaphore(concurrency)
    stats = {"total": len(files), "success": 0, "skipped": 0, "failed": 0}

    async def process(md_file: Path) -> None:
        async with sem:
            rel_path = md_file.relative_to(raw_dir)
            out_path = clean_dir / rel_path

            try:
                text = md_file.read_text(encoding="utf-8")
                result = await clean_one(text)

                if result.strip() == "[SKIP]":
                    stats["skipped"] += 1
                    logger.info("⏭️  SKIP: %s", rel_path)
                    return

                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(result.strip(), encoding="utf-8")
                stats["success"] += 1
                logger.info("✅ %s", rel_path)

            except Exception as e:
                stats["failed"] += 1
                logger.error("❌ %s: %s", rel_path, e)

    tasks = [process(f) for f in files]
    await asyncio.gather(*tasks)

    return stats


# ── CLI 入口 ──────────────────────────────────────────

if __name__ == "__main__":
    import sys
    source = sys.argv[1] if len(sys.argv) > 1 else "fastapi"
    result = asyncio.run(clean_source(source))
    logger.info("")
    logger.info(
        "📊 %s: total=%d success=%d skipped=%d failed=%d",
        source, result["total"], result["success"], result["skipped"], result["failed"],
    )
