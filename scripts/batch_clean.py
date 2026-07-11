"""批量清洗 fastapi + sqlmodel 的 raw/ 到 clean/"""
import asyncio, sys, time
sys.path.insert(0, "src")
from tech_doc_mcp.processor.cleaner import clean_source

async def main():
    for source in ["fastapi", "sqlmodel"]:
        print(f"\n{'='*50}")
        print(f"🧹 清洗 {source}...")
        print(f"{'='*50}")
        t0 = time.time()
        stats = await clean_source(source, concurrency=3)
        elapsed = time.time() - t0
        print(f"\n📊 {source}: total={stats['total']} "
              f"✅{stats['success']} ⏭️{stats['skipped']} ❌{stats['failed']} "
              f"({elapsed:.0f}s)")

asyncio.run(main())
