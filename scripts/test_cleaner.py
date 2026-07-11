"""测试 cleaner — 抽 3 篇清洗，看效果"""
import asyncio
import sys
sys.path.insert(0, "src")

from pathlib import Path
from tech_doc_mcp.processor.cleaner import clean_one
from tech_doc_mcp.config import load_config, get_llm_config
from openai import AsyncOpenAI

config = load_config()
llm = get_llm_config(config)
client = AsyncOpenAI(base_url=llm["api_base"], api_key=llm["api_key"])
extra_body = llm.get("extra_body")

samples = [
    "docs/fastapi/raw/features.md",
    "docs/fastapi/raw/async.md",
    "docs/fastapi/raw/tutorial/first-steps.md",
]

async def main():
    for path in samples:
        raw_text = Path(path).read_text(encoding="utf-8")
        print(f"\n{'='*60}")
        print(f"📄 {path}  ({len(raw_text)} chars)")
        print(f"{'='*60}")

        result = await clean_one(client, llm["model"], raw_text, extra_body)

        if result.strip() == "[SKIP]":
            print("⏭️  SKIP")
        else:
            print(result[:500])
            if len(result) > 500:
                print(f"\n... (共 {len(result)} chars)")

asyncio.run(main())
