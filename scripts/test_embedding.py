"""测试 Embedding API — 抽取 FastAPI 几篇文章验证"""
import sys
sys.path.insert(0, "src")

from pathlib import Path
from openai import OpenAI
from tech_doc_mcp.config import load_config, get_embedding_config

config = load_config()
ec = get_embedding_config(config)

client = OpenAI(base_url=ec["api_base"], api_key=ec["api_key"])

# 抽 3 篇短文测试
samples = [
    "docs/fastapi/raw/features.md",
    "docs/fastapi/raw/async.md",
    "docs/fastapi/raw/tutorial/first-steps.md",
]

for path in samples:
    text = Path(path).read_text(encoding="utf-8")
    # 取前 500 字符测试
    snippet = text[:500]

    print(f"\n{'='*60}")
    print(f"📄 {path} ({len(text)} chars)")
    print(f"--- 前 100 字符 ---")
    print(snippet[:100].replace('\n', '\\n'))

    try:
        resp = client.embeddings.create(
            model=ec["model"],
            input=snippet,
        )
        emb = resp.data[0].embedding
        print(f"✅ embedding: {len(emb)} 维, 前5维: {emb[:5]}")
        print(f"   tokens used: {resp.usage.total_tokens}")
    except Exception as e:
        print(f"❌ {e}")

print("\n🎯 测试完成")
