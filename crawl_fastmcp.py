#!/usr/bin/env python3
"""
FastMCP 文档爬虫 —— 由 /add-docs skill 生成。

站点: https://gofastmcp.com (Mintlify)
策略: Mintlify 为每个页面提供 `{url}.md` 原生 Markdown 端点，
      直接抓取即可拿到干净 Markdown，无需 HTML 提取 / CSS 选择器。

- 从 sitemap.xml 获取页面列表
- 排除 /v2/ 旧版本快照（陈旧的重复内容）
- 抓取每页的 .md 端点，剥离开头的 "Documentation Index" 样板块
- 保存到 docs/fastmcp/raw/，保留 URL 目录层级
- 并发 5，每请求间隔 ≥1s（友好爬取）

在终端运行:  python crawl_fastmcp.py
"""
import asyncio
import re
import sys
from pathlib import Path
from xml.etree import ElementTree

import httpx

BASE = "https://gofastmcp.com"
SITEMAP = f"{BASE}/sitemap.xml"
OUT_DIR = Path(__file__).parent / "docs" / "fastmcp" / "raw"

CONCURRENCY = 5
DELAY = 1.2          # 每个请求前的间隔（秒）
TIMEOUT = 30.0

# 开头固定样板块（每个 .md 页面都有），需剥离
BOILERPLATE_HEAD = "> ## Documentation Index"


def get_page_urls() -> list[str]:
    """拉取 sitemap，返回需爬取的页面 URL（排除 /v2/ 旧版快照）。"""
    resp = httpx.get(SITEMAP, timeout=TIMEOUT, follow_redirects=True)
    resp.raise_for_status()
    root = ElementTree.fromstring(resp.text)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = [loc.text.strip() for loc in root.iterfind(".//sm:loc", ns)]

    kept, skipped = [], 0
    for u in urls:
        path = u[len(BASE):].lstrip("/")
        if path.split("/", 1)[0] == "v2":   # 排除旧版本快照
            skipped += 1
            continue
        kept.append(u)
    print(f"sitemap 共 {len(urls)} 页 -> 保留 {len(kept)}，排除 /v2/ {skipped} 页")
    return sorted(kept)


def strip_boilerplate(md: str) -> str:
    """剥离开头的 'Documentation Index' 引用块及其后空行。"""
    if not md.lstrip().startswith(BOILERPLATE_HEAD):
        return md
    lines = md.splitlines()
    i = 0
    # 跳过开头的空行
    while i < len(lines) and not lines[i].strip():
        i += 1
    # 跳过连续的引用块行（以 > 开头）
    while i < len(lines) and lines[i].lstrip().startswith(">"):
        i += 1
    # 跳过其后紧邻的空行
    while i < len(lines) and not lines[i].strip():
        i += 1
    return "\n".join(lines[i:]).strip() + "\n"


def url_to_path(url: str) -> Path:
    """https://gofastmcp.com/clients/auth/bearer -> docs/fastmcp/raw/clients/auth/bearer.md"""
    rel = url[len(BASE):].lstrip("/") or "index"
    return OUT_DIR / f"{rel}.md"


async def fetch_one(client: httpx.AsyncClient, sem: asyncio.Semaphore,
                    url: str, idx: int, total: int) -> bool:
    md_url = f"{url}.md"
    async with sem:
        await asyncio.sleep(DELAY)
        try:
            resp = await client.get(md_url, timeout=TIMEOUT, follow_redirects=True)
            if resp.status_code != 200:
                print(f"⚠  [{idx}/{total}] 跳过 {md_url} (HTTP {resp.status_code})")
                return False
            content = strip_boilerplate(resp.text)
            if len(content.strip()) < 30:
                print(f"⚠  [{idx}/{total}] 跳过 {md_url} (内容过短)")
                return False
            out = url_to_path(url)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content, encoding="utf-8")
            print(f"✅ [{idx}/{total}] {out.relative_to(OUT_DIR.parent.parent.parent)}")
            return True
        except Exception as e:
            print(f"⚠  [{idx}/{total}] 失败 {md_url}: {e}")
            return False


async def main() -> None:
    urls = get_page_urls()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(CONCURRENCY)
    total = len(urls)
    async with httpx.AsyncClient(headers={"User-Agent": "tech-doc-mcp/add-docs"}) as client:
        results = await asyncio.gather(
            *(fetch_one(client, sem, u, i + 1, total) for i, u in enumerate(urls))
        )
    ok = sum(results)
    print(f"\n完成: 成功 {ok} / {total}，失败 {total - ok}")
    print(f"输出目录: {OUT_DIR}")
    if ok < total:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
