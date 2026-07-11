"""
FastAPI 文档爬虫（带 URL 过滤）
跳过社区/运营/历史页面，只保留技术参考内容
"""

import asyncio, re
from pathlib import Path
from urllib.parse import urlparse
import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md

BASE_URL = "https://fastapi.tiangolo.com"
OUTPUT_DIR = Path("docs/fastapi/raw")
CONCURRENCY = 5

# ── URL 过滤 ─────────────────────────────────────────
SKIP_PATTERNS = [
    "help-fastapi", "newsletter", "alternatives", "benchmarks",
    "contributing", "history-design-future", "management", "about",
    "fastapi-people", "external-links", "project-generation",
    "release-notes", "resources", "translations", "editor-support",
    "third-party-tools",  # 第三方工具推荐
]

def should_skip(url: str) -> bool:
    path = urlparse(url).path.strip("/")
    for pattern in SKIP_PATTERNS:
        if pattern in path:
            return True
    return False

# ── 爬取逻辑 ─────────────────────────────────────────

def get_sitemap_urls(xml: str) -> list[str]:
    soup = BeautifulSoup(xml, "xml")
    urls = [loc.text.strip() for loc in soup.find_all("loc")
            if loc.text.strip().startswith(BASE_URL)]
    kept, skipped = [], []
    for u in urls:
        (skipped if should_skip(u) else kept).append(u)
    print(f"📊 {len(urls)} 页 → 保留 {len(kept)}, 跳过 {len(skipped)}")
    for u in skipped:
        print(f"  ⏭️  {u}")
    return kept

def url_to_filepath(url: str) -> Path:
    path = urlparse(url).path.strip("/") or "index"
    return OUTPUT_DIR / f"{path}.md"

def extract_content(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one("article.md-content__inner") or \
              soup.select_one(".md-content") or soup.select_one("main")
    if not content:
        return ""
    for tag in content.select("nav, .md-source-file, script, style, .headerlink"):
        tag.decompose()
    return str(content)

def to_markdown(html: str, url: str) -> str:
    markdown = md(html, heading_style="ATX", strip=["img"])
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    return f"---\nsource: {url}\n---\n\n{markdown.strip()}"

async def crawl_page(client: httpx.AsyncClient, url: str, sem: asyncio.Semaphore) -> tuple[str, bool]:
    async with sem:
        try:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            content = extract_content(resp.text)
            if not content:
                print(f"  ⚠️  无内容: {url}")
                return url, False
            markdown = to_markdown(content, url)
            filepath = url_to_filepath(url)
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_text(markdown, encoding="utf-8")
            print(f"  ✅ {url} -> {filepath}")
            return url, True
        except Exception as e:
            print(f"  ❌ {url}: {e}")
            return url, False

async def main():
    print(f"📡 {BASE_URL}/sitemap.xml")
    async with httpx.AsyncClient(timeout=30) as c:
        resp = await c.get(f"{BASE_URL}/sitemap.xml"); resp.raise_for_status()
        urls = get_sitemap_urls(resp.text)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(CONCURRENCY)
    print(f"\n🕷️  开始爬取 {len(urls)} 页...\n")
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "tech-doc-mcp/0.1"}) as c:
        results = await asyncio.gather(*(crawl_page(c, u, sem) for u in urls))
    ok = sum(1 for _, v in results if v)
    print(f"\n🎯 {ok}/{len(urls)} 成功")

if __name__ == "__main__":
    asyncio.run(main())
