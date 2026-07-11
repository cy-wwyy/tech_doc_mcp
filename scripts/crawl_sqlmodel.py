"""
SQLModel 文档爬虫 — 自动生成版本
站点: MkDocs Material, 63 页, 选择器: article.md-content__inner
"""

import asyncio
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md

BASE_URL = "https://sqlmodel.tiangolo.com"
SITEMAP_URL = f"{BASE_URL}/sitemap.xml"
OUTPUT_DIR = Path("docs/sqlmodel/raw")
CONCURRENCY = 5


def get_sitemap_urls(xml_content: str) -> list[str]:
    soup = BeautifulSoup(xml_content, "xml")
    return [loc.text.strip() for loc in soup.find_all("loc")
            if loc.text.strip().startswith(BASE_URL)]


def url_to_filepath(url: str) -> Path:
    path = urlparse(url).path.strip("/")
    return OUTPUT_DIR / (f"{path}.md" if path else "index.md")


def extract_content(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for sel in ["article.md-content__inner", ".md-content", "main"]:
        content = soup.select_one(sel)
        if content:
            break
    if not content:
        return ""
    for tag in content.select("nav, .md-source-file, script, style, .headerlink"):
        tag.decompose()
    return str(content)


def html_to_markdown(html: str, page_url: str) -> str:
    markdown = md(html, heading_style="ATX", strip=["img"])
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    return f"---\nsource: {page_url}\n---\n\n{markdown.strip()}"


async def crawl_page(client: httpx.AsyncClient, url: str, sem: asyncio.Semaphore) -> tuple[str, bool]:
    async with sem:
        try:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            content = extract_content(resp.text)
            if not content:
                print(f"  ⚠️  无内容: {url}")
                return url, False
            markdown = html_to_markdown(content, url)
            filepath = url_to_filepath(url)
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_text(markdown, encoding="utf-8")
            print(f"  ✅ {url} -> {filepath}")
            return url, True
        except Exception as e:
            print(f"  ❌ {url}: {e}")
            return url, False


async def main():
    print(f"📡 {SITEMAP_URL}")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(SITEMAP_URL)
        resp.raise_for_status()
        urls = get_sitemap_urls(resp.text)
    print(f"📄 {len(urls)} 页\n")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "tech-doc-mcp/0.1"}) as client:
        results = await asyncio.gather(*(crawl_page(client, u, sem) for u in urls))

    ok = sum(1 for _, v in results if v)
    print(f"\n🎯 {ok}/{len(urls)} 成功, {len(urls)-ok} 失败")


if __name__ == "__main__":
    asyncio.run(main())
