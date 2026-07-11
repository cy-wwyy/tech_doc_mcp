# tech-doc-mcp

> 本地技术文档 MCP 服务器 —— 为 AI Agent 提供最新框架文档的实时混合搜索能力。

解决 LLM 训练数据滞后导致的过时 API 调用问题:把框架官方文档爬取到本地,经 LLM 清洗、分块、建立向量 + BM25 双索引,通过 [MCP](https://modelcontextprotocol.io) 协议暴露给 Claude Code 等 Agent,查询时返回最新、可溯源的文档片段。

## 特性

- **混合搜索** —— ChromaDB 语义检索 + BM25 关键词检索,RRF 融合 (k=60),兼顾语义相关与精确匹配
- **中英分词** —— jieba 分词,BM25 对中英文混合内容友好
- **面向 Agent 的极简工具** —— 只需 4 个 MCP 工具即可覆盖搜索/阅读/浏览
- **LLM 文档清洗** —— 自动把爬虫原始 HTML/Markdown 清洗为干净正文,3 并发 + 指数退避重试
- **多源多版本共存** —— 每个文档源独立 Collection,新旧版本并存,搜索取最新版本
- **OpenAI 兼容** —— LLM 与 Embedding 均走 OpenAI 兼容接口,可自由替换服务商

## 架构

```
文档处理管线:
  docs/{source}/raw/*.md          ← 爬虫原始输出
         │  cleaner.py (LLM 清洗)
         ▼
  docs/{source}/clean/*.md        ← 干净 Markdown
         │  loader → chunker → embedding → ChromaDB
         ▼
  data/chroma/                    ← 向量索引 + 全文存储

搜索链路 (Agent 视角):
  search_docs(query, source, keywords, limit)
    ├─ keywords → BM25 关键词搜索
    ├─ query    → ChromaDB 语义搜索
    └─ 两路 RRF 融合 → 按 path 去重 → top-K 返回
```

## 安装

需要 Python 3.12+ 和 [uv](https://github.com/astral-sh/uv)。

```bash
git clone git@github.com:cy-wwyy/tech_doc_mcp.git
cd tech_doc_mcp
uv sync
```

## 配置

复制模板并填入你的 API Key(支持 `${ENV_VAR}` 占位符):

```bash
cp config.yaml.example config.yaml   # LLM 与 Embedding 的 api_base/model/dimensions
cp .env.example .env                 # 填入 LLM_API_KEY / EMBEDDING_API_KEY
```

- **`llm`** —— 文档清洗用(如 DeepSeek、任意 OpenAI 兼容接口)
- **`embedding`** —— 向量化用,`dimensions` 需与所选模型匹配

## 使用

```bash
# 1. 爬取文档源 → docs/{source}/raw/
uv run python crawlers/<script>.py

# 2. LLM 清洗 raw/ → clean/
uv run python -m tech_doc_mcp.processor.cleaner <source_name>

# 3. 索引 clean/ → 分块 → embedding → ChromaDB
uv run tech-doc-mcp index <source> --version 0.1.0

# 4. 启动 MCP Server（默认 streamable-http://127.0.0.1:8000）
uv run tech-doc-mcp serve
```

### 接入 Claude Code

将本地 MCP Server 加入 Claude Code 的 MCP 配置后,Agent 即可调用以下工具:

| 工具 | 用途 |
|------|------|
| `list_sources()` | 列出已索引的文档源(name / version / chunks) |
| `search_docs(query, source, keywords=None, limit=10)` | 混合搜索,返回 rank / path / score / text |
| `read_doc(path, source)` | 读取完整页面(最多 8000 字符) |
| `list_docs(source, path=None)` | 浏览目录结构 |

## 技术栈

FastMCP · ChromaDB · rank-bm25 · jieba · OpenAI SDK · BeautifulSoup / markdownify · httpx · Typer

## 项目文档

- [`ROADMAP.md`](ROADMAP.md) —— 迭代路线图:已完成项 + 待处理项
- [`docs/DEVLOG.md`](docs/DEVLOG.md) —— 开发日志

## 说明

- 源文档(`docs/*/raw`、`docs/*/clean`)与索引数据(`data/`)不纳入版本库,可按上述流程重新生成。
- 项目处于 `0.1.0` 早期阶段,搜索质量验证体系、Web 管理界面、多源搜索等仍在规划中,详见 ROADMAP。

## License

MIT
