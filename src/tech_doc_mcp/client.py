"""
统一 API 客户端 — 所有 LLM / Embedding 调用的唯一入口
"""

from openai import OpenAI, AsyncOpenAI

from tech_doc_mcp.config import load_config, get_llm_config, get_embedding_config


def get_llm_client() -> AsyncOpenAI:
    """文档清洗用 LLM 客户端（异步）"""
    cfg = get_llm_config()
    return AsyncOpenAI(base_url=cfg["api_base"], api_key=cfg["api_key"])


def get_llm_model() -> str:
    return get_llm_config()["model"]


def get_llm_extra_body() -> dict | None:
    return get_llm_config().get("extra_body")


def get_embedding_client() -> OpenAI:
    """向量化客户端（同步）"""
    cfg = get_embedding_config()
    return OpenAI(base_url=cfg["api_base"], api_key=cfg["api_key"])


def get_embedding_model() -> str:
    return get_embedding_config()["model"]


def get_embedding_batch_size() -> int:
    return get_embedding_config().get("batch_size", 10)


def embed(client: OpenAI, texts: list[str]) -> list[list[float]]:
    """批量向量化，返回与 texts 一一对应的向量列表"""
    model = get_embedding_model()
    resp = client.embeddings.create(model=model, input=texts)
    return [d.embedding for d in resp.data]


def embed_one(client: OpenAI, text: str) -> list[float]:
    """单条向量化"""
    model = get_embedding_model()
    resp = client.embeddings.create(model=model, input=text)
    return resp.data[0].embedding
