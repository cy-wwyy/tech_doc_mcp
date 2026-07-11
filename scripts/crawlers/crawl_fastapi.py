"""
FastAPI 文档爬虫
FastAPI 使用 MkDocs Material 主题，从 sitemap 获取所有页面 URL，
提取正文内容并转换为 Markdown 保存。
"""

import asyncio
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md

BASE_URL = "https://fastapi.tiangolo.com"
SITEMAP_URL = f"{BASE_URL}/sitemap.xml"
OUTPUT_DIR = Path("docs/fastapi/raw")
CONCURRENCY = 5  # 并发数，友好爬取


def get_sitemap_urls(xml_content: str) -> list[str]:
    """从 sitemap.xml 提取所有 URL"""
    soup = BeautifulSoup(xml_content, "xml")
    urls = []
    for loc in soup.find_all("loc"):
        url = loc.text.strip()
        # 只保留文档页面（排除 assets 等）
        if url.startswith(BASE_URL):
            urls.append(url)
    return urls


def url_to_filepath(url: str) -> Path:
    """将 URL 转换为本地文件路径"""
    path = urlparse(url).path.strip("/")
    if not path:
        path = "index"
    return OUTPUT_DIR / f"{path}.md"


def extract_content(html: str) -> str:
    """从 MkDocs Material 页面提取正文内容"""
    soup = BeautifulSoup(html, "html.parser")

    # MkDocs Material 的正文在 article.md-content 或 .md-content 中
    content = soup.select_one("article.md-content") or soup.select_one(".md-content")

    if not content:
        # 回退：尝试获取 main 内容
        content = soup.select_one("main") or soup.select_one("body")

    if not content:
        return ""

    # 移除不需要的元素
    for tag in content.select("nav, .md-source-file, script, style, .headerlink"):
        tag.decompose()

    return str(content)


def html_to_markdown(html: str, page_url: str) -> str:
    """将 HTML 转换为 Markdown，添加元数据头"""
    markdown = md(html, heading_style="ATX", strip=["img"])

    # 清理多余空行
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)

    # 添加来源元数据
    header = f"---\nsource: {page_url}\n---\n\n"
    return header + markdown.strip()


async def crawl_page(client: httpx.AsyncClient, url: str, sem: asyncio.Semaphore) -> tuple[str, bool]:
    """爬取单个页面，返回 (url, success)"""
    async with sem:
        try:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            html = resp.text

            content = extract_content(html)
            if not content:
                print(f"  ⚠️  无法提取内容: {url}")
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
    print(f"📡 获取 sitemap: {SITEMAP_URL}")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(SITEMAP_URL)
        resp.raise_for_status()
        urls = get_sitemap_urls(resp.text)

    print(f"📄 共 {len(urls)} 个页面\n")

    # 创建输出目录
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "tech-doc-mcp/0.1"}) as client:
        tasks = [crawl_page(client, url, sem) for url in urls]
        results = await asyncio.gather(*tasks)

    success = sum(1 for _, ok in results if ok)
    failed = len(results) - success
    print(f"\n🎯 完成: {success} 成功, {failed} 失败")


if __name__ == "__main__":
    asyncio.run(main())
