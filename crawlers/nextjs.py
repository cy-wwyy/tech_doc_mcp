#!/usr/bin/env python3
"""
Next.js 文档爬虫

站点: https://nextjs.org/docs (自定义 Next.js 站点)
策略: sitemap → 过滤 docs + learn 页面 → 提取 HTML → 保存 raw Markdown

过滤规则:
  - 保留: /docs/ /learn/
  - 跳过: /blog/ /conf/ /showcase/ /examples/

内容提取:
  - 标题: <h1>
  - 正文: <div class="prose">
  - 转为 Markdown，保留代码块和标题层级

并发 5，间隔 1.2s。
"""

import asyncio
import re
import sys
from pathlib import Path
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md

BASE = "https://nextjs.org"
SITEMAP = f"{BASE}/sitemap.xml"
OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "nextjs" / "raw"

CONCURRENCY = 5
DELAY = 1.2
TIMEOUT = 30.0

SKIP_PREFIXES = ("/blog/", "/conf/", "/showcase/", "/examples/", "/podcast/")


def get_page_urls() -> list[str]:
    """拉取 sitemap，过滤 docs + learn 页面"""
    resp = httpx.get(SITEMAP, timeout=TIMEOUT, follow_redirects=True)
    resp.raise_for_status()
    root = ElementTree.fromstring(resp.text)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = [loc.text.strip() for loc in root.iterfind(".//sm:loc", ns)]

    kept, skipped = [], 0
    for u in urls:
        path = u[len(BASE):].lstrip("/")
        if any(path.startswith(p) for p in SKIP_PREFIXES):
            skipped += 1
            continue
        if path.startswith(("docs/", "learn/")):
            kept.append(u)
        else:
            skipped += 1

    print(f"sitemap 共 {len(urls)} 页 → 保留 {len(kept)}，跳过 {skipped}")
    return sorted(kept)


def extract_content(html: str) -> str | None:
    """提取页面标题 + 正文 prose，转为 Markdown"""
    soup = BeautifulSoup(html, "lxml")

    # 标题
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else ""

    # 正文（prose 容器）
    prose = soup.find("div", class_="prose")
    if not prose:
        return None

    # 移除代码块中的复制按钮和行号
    for el in prose.select("[class*='copy'], [class*='line-number'], .header, .sr-only"):
        el.decompose()

    content_html = str(prose)
    content_md = md(content_html, heading_style="atx", code_language="auto")

    # 避免 markdownify 在代码块前后插入多余空行
    content_md = re.sub(r"\n{3,}", "\n\n", content_md)

    # 拼 frontmatter
    lines = ["---", f"title: {title}", f"source: {BASE}/docs", "---", "", content_md]
    return "\n".join(lines)


def url_to_path(url: str) -> Path:
    """https://nextjs.org/docs/app/getting-started/installation
    → docs/nextjs/raw/app/getting-started/installation.md"""
    path = url[len(BASE):].lstrip("/").rstrip("/") or "index"
    return OUT_DIR / f"{path}.md"


async def fetch_one(
    client: httpx.AsyncClient, sem: asyncio.Semaphore,
    url: str, idx: int, total: int,
) -> bool:
    async with sem:
        await asyncio.sleep(DELAY)
        try:
            resp = await client.get(url, timeout=TIMEOUT, follow_redirects=True)
            if resp.status_code != 200:
                print(f"⚠  [{idx}/{total}] HTTP {resp.status_code} {url}")
                return False

            content = extract_content(resp.text)
            if not content or len(content.strip()) < 50:
                print(f"⚠  [{idx}/{total}] 内容不足 {url}")
                return False

            out = url_to_path(url)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content, encoding="utf-8")

            rel = out.relative_to(OUT_DIR.parent.parent.parent)
            print(f"✅ [{idx}/{total}] {rel}")
            return True

        except Exception as e:
            print(f"⚠  [{idx}/{total}] 失败 {url}: {e}")
            return False


async def main() -> None:
    urls = get_page_urls()
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
