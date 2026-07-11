"""
分块干跑检查(不嵌入)—— 看切分粒度是否合理、有无过短、合并是否生效。

用法:uv run python eval/chunk_inspect.py
"""

from __future__ import annotations

import statistics
from pathlib import Path

from tech_doc_mcp.crawler.loader import load_documents
from tech_doc_mcp.processor.chunker import _strip_code_blocks, chunk_document

SOURCES = ["fastapi", "nextjs", "sqlmodel", "tailwindcss"]
SIZES = [500, 1024, 2000]


def chunk_all(size):
    chunks = []
    for src in SOURCES:
        clean = Path(f"docs/{src}/clean")
        if not clean.exists():
            continue
        for d in load_documents(src, clean):
            for c in chunk_document(d, chunk_size=size):
                chunks.append(c)
    return chunks


def pctl(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


def main():
    print(f"源: {SOURCES}\n")
    print(f"{'size':>5} {'chunks':>7} {'去代码长度: min':>14} {'p10':>5} {'中位':>5} {'均值':>6} {'p90':>5} {'max':>6} {'<0.3size':>9} {'<80字':>7}")
    per_size = {}
    for size in SIZES:
        chunks = chunk_all(size)
        per_size[size] = chunks
        lens = [len(_strip_code_blocks(c.text)) for c in chunks]  # 去代码长度(chunk_size 语义)
        short = sum(1 for x in lens if x < size * 0.3)
        tiny = sum(1 for x in lens if x < 80)
        print(f"{size:>5} {len(chunks):>7} {min(lens):>14} {pctl(lens,0.1):>5} "
              f"{int(statistics.median(lens)):>5} {int(statistics.mean(lens)):>6} "
              f"{pctl(lens,0.9):>5} {max(lens):>6} {short:>9} {tiny:>7}")

    # 抽看 size=1024 下最短的 8 个 chunk（判断是否合理/合并有没有漏）
    print("\n=== size=1024 最短的 8 个 chunk(去代码长度) ===")
    cs = per_size[1024]
    ranked = sorted(cs, key=lambda c: len(_strip_code_blocks(c.text)))
    for c in ranked[:8]:
        ln = len(_strip_code_blocks(c.text))
        snippet = c.text.replace("\n", " ")[:90]
        print(f"  [{ln:>3}字|全文{len(c.text):>4}] {c.path}::{c.chunk_index}  «{snippet}»")


if __name__ == "__main__":
    main()
