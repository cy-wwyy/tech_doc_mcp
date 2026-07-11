#!/usr/bin/env python3
"""
shadcn/ui 文档爬虫

站点: https://ui.shadcn.com/docs
策略: 从 llms.txt 获取 URL 列表，每个页面追加 .md 获取 Markdown

并发 5，间隔 1s。
"""

import asyncio
import sys
from pathlib import Path

import httpx

BASE = "https://ui.shadcn.com"
LLMS_URL = f"{BASE}/llms.txt"
OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "shadcn" / "raw"

CONCURRENCY = 5
DELAY = 1.0
TIMEOUT = 30.0


def get_urls() -> list[str]:
    """从 llms.txt 解析文档 URL 列表"""
    import re
    resp = httpx.get(LLMS_URL, timeout=TIMEOUT, follow_redirects=True)
    resp.raise_for_status()
    urls = sorted(set(re.findall(rf"{re.escape(BASE)}/docs/[^\s)\]]+", resp.text)))
    print(f"llms.txt → {len(urls)} pages")
    return urls


def url_to_path(url: str) -> Path:
    """https://ui.shadcn.com/docs/components/accordion
    → docs/shadcn/raw/docs/components/accordion.md"""
    path = url[len(BASE):].lstrip("/").rstrip("/") or "index"
    return OUT_DIR / f"{path}.md"


async def fetch_one(
    client: httpx.AsyncClient, sem: asyncio.Semaphore,
    url: str, idx: int, total: int,
) -> bool:
    async with sem:
        await asyncio.sleep(DELAY)
        try:
            md_url = f"{url}.md"
            resp = await client.get(md_url, timeout=TIMEOUT, follow_redirects=True)
            if resp.status_code != 200:
                print(f"⚠  [{idx}/{total}] HTTP {resp.status_code} {md_url}")
                return False

            content = resp.text.strip()
            if len(content) < 30:
                print(f"⚠  [{idx}/{total}] 内容过短 {md_url}")
                return False

            # 添加 frontmatter
            parts = url[len(BASE):].rstrip("/").split("/")
            title = parts[-1].replace("-", " ").title() if parts else ""
            out = url_to_path(url)

            frontmatter = f"---\ntitle: {title}\nurl: {url}\n---\n\n"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(frontmatter + content, encoding="utf-8")

            rel = out.relative_to(OUT_DIR.parent.parent.parent)
            print(f"✅ [{idx}/{total}] {rel}")
            return True

        except Exception as e:
            print(f"⚠  [{idx}/{total}] 失败 {url}: {e}")
            return False


async def main() -> None:
    urls = get_urls()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(CONCURRENCY)
    total = len(urls)

    async with httpx.AsyncClient(
        headers={"User-Agent": "tech-doc-mcp/add-docs"},
        timeout=httpx.Timeout(TIMEOUT),
    ) as client:
        results = await asyncio.gather(
            *(fetch_one(client, sem, u, i + 1, total) for i, u in enumerate(urls))
        )

    ok = sum(results)
    print(f"\n完成: 成功 {ok}/{total}，失败 {total - ok}")
    print(f"输出目录: {OUT_DIR}")
    if ok < total:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
