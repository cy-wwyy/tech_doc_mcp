# 项目开发日志

> 2026-07-08 ~ 2026-07-09，两日从审查到重构的完整记录。

---

## 一、起点：8 小时 vibe coding 的产物

项目初版通过 AI 辅助编码 8 小时完成，1,359 行代码，19 个 Python 文件，7 个功能模块。核心搜索链路完整可用：爬取 → LLM 清洗 → 分块 → ChromaDB 向量索引 → BM25+语义 RRF 混合搜索。

首次审查发现 3 个 bug、5 个设计缺陷、6 个功能缺失项。但随着逐项验证，发现部分"bug"实际不成立。

---

## 二、踩过的坑 & 修复过程

### 坑 1："cleaner 丢失 frontmatter" → 伪 bug

**审查标注**：cleaner.py 清洗前去掉了 YAML frontmatter，LLM 输出不会补回，导致 loader 加载时 metadata 为空。

**验证结论**：爬虫脚本（crawl_fastmcp.py）根本不写 frontmatter，raw/ 文件从头到尾就是纯 Markdown。那行正则什么都没去掉——是一行死代码。CLAUDE.md 写的"clean/ 文件以 frontmatter 开头"是设计意图，不是已实现的功能。

**教训**：不要根据文档推断代码行为，要验证实际数据。

### 坑 2：chunk overlap 膨胀 → 重写切分逻辑

**现象**：`_apply_overlap` 在前一个 chunk 尾部取 overlap 拼到当前 chunk 前，但不从尾部截断。chunk 越往后越长。

**修复过程**：
1. 第一版 → 只对人工边界加 overlap（段落边界不加）
2. AI 测试发现 → 语义搜索好于混合搜索，keyword 路是负收益
3. 最终版 → 重写整个切分策略，overlap 完全移除

**最终方案**：
- 代码块保护（占位符替换，在 `_split_by_headings` 和 `_split_long_section` 两层保护）
- `text_for_embedding` 字段：去掉代码块的纯文本（生成向量用），`text` 保留原文（Agent 消费用）
- 短 section 合并：去代码后 <300 字向后合并（上限 800），首个 section 前向合并
- 阶梯切分：≤1000 不切 / 1000-1600 中点切 2 段 / >1600 每 ~600 切
- 子 chunk 带父标题（`re.search` 提取第一个 `##` 标题）
- 寻找最佳切分点：优先段落边界 → 句子边界 → 行边界，跳过代码块

**效果**：FastAPI 从 9,088 chunk 降至 1,091（↓88%），SQLModel 从 4,946 降至 425（↓91%）。碎片率从 62% 降至 10%。

### 坑 3：`_text_id` 用 `text[:100]` → 改用 `path::chunk_index`

不同 chunk 前 100 字符可能相同，RRF 去重时错误合并。改为 `path::chunk_index` 组合。

### 坑 4：BM25 全量加载 → 改成按需过滤+排序

最初 BM25 在 `HybridSearcher.__init__` 时从 ChromaDB 全量拉数据建内存索引。讨论后改为：ChromaDB `where_document` 先过滤 → BM25 对过滤结果排序 → RRF 融合。但实测发现 keyword+semantic 混合不如纯语义（见下文）。

### 坑 5：keyword+语义 RRF 混合反而更差 → 降权

**测试数据**：
- 纯语义：4/5 命中，排序合理
- 混合：3/5 命中，http-basic-auth.md（不相关）挤入，simple-oauth2.md（相关）丢失

**原因**：Agent 查询是精心构造的英文技术描述，语义 embedding 能完美匹配。BM25 用字面匹配引入噪音。

**修复**：
- RRF 语义权重 0.7 / 关键词权重 0.3
- k=30（而非 k=60），提高分数区分度
- search_docs docstring 引导 Agent：概念搜索不要传 keywords，只在查精确 API 符号时传

### 坑 6：RRF k 值的直觉陷阱

> "k 越大，rank 差异带来的分数梯度越明显" — **这是错的。**

k=30: rank1=0.0323, rank2=0.0313 → 差 0.0010  
k=60: rank1=0.0164, rank2=0.0161 → 差 0.0003

k 的作用是平滑——防止单路排第一的压倒一切。k 越大越平滑。最终选用 k=30。

### 坑 7：grep_docs 用 Python regex 全量扫描

用户发现 ChromaDB 的 `get` + `where_document={"$contains": keyword}` 可以替代 Python regex 遍历。删掉 60 行遍历代码，改为 1 次 ChromaDB 内部全文索引调用。

### 坑 8：module-level init 导致 import 时崩溃

server.py 在模块加载时执行 `config = load_config()`。config.yaml 缺失则 import 失败。改为惰性初始化（`_get_store()` / `_get_embed_client()`）。

### 坑 9：Collection 命名承载业务逻辑

collection name `docs_{source}_{version}` 用字符串切割反推 source/version。source 名含下划线（如 `google_cloud`）会解析错误。修复：`get_or_create_collection` 存储 `metadata={"source": ..., "version": ...}`，`list_sources` 优先读 metadata。

### 坑 10：API 调用分散

三处各自实例化 OpenAI 客户端（cleaner.py / server.py / cli/main.py）。统一为 `client.py`：`get_llm_client()` / `get_embedding_client()` / `embed()` / `embed_one()`。

### 坑 11：文档截断

代码中两处硬截断：`read_doc` 8000 字符、cleaner LLM 输入 15000 字符。两个都去掉——Agent 需要完整页面，LLM context 容量充足。

### 坑 12：ChromaDB `$and` 语法

`collection.get(where={"a": 1, "b": 2})` → 报错。多条件必须用 `{"$and": [{"a": 1}, {"b": 2}]}`。

### 坑 13：子 chunk 标题被 overlap 覆盖

`_split_long_section` 给子 chunk 前拼了父标题，但 `chunk_document` 的 overlap 逻辑在标题前面又拼了一段前文尾缀，把标题埋了。修复：带 `## ` 开头的 chunk 跳过 overlap。

---

## 三、架构决策

### 决策 1：Agent 和 MCP 的边界

| 决策 | 理由 |
|------|------|
| Agent 传 `source`，MCP 不选源 | Agent 知道用户意图，自己判断搜哪个源 |
| `list_sources` 返回激活的源列表 | Agent 首次调用获取菜单，后续自己组合 |
| `search_docs` 唯一入口 | Agent 不需要区分 keyword/semantic，MCP 内部混合 |
| Agent 提供 keywords（不是 MCP 用 LLM 提取） | Agent 本身有 LLM 能力，MCP 不做重复工作 |
| 多源联合搜索 Agent 侧处理 | Agent 调两次 search_docs 自己融合，MCP 不管 |

### 决策 2：保留向量搜索，不纯 BM25

BM25 无法处理同义词（`login` vs `sign-in`）和概念性聚合（`file upload` 跨 `UploadFile`/`File`/`bytes`）。语义搜索成本低（一次 API 调用），兜底价值高。

### 决策 3：不用 LangChain/LlamaIndex

项目代码量 1,359 行，LangChain 依赖树比整个项目大 100 倍。自己写的 chunker、BM25、RRF 融合——每个都是面试时能展开讲的点，也是理解底层的途径。`tiktoken` 同样拒绝：技术文档有明确的 `##` 标题边界，按 token 等宽切分不如按语义单元切。

### 决策 4：代码块在 embedding 中去掉，在存储中保留

代码块对语义 embedding 是噪音（模型训练数据是自然语言，看不懂代码）。但 Agent 需要完整代码示例。方案：`text_for_embedding`（去代码）用于 embedding API，`text`（含代码）存入 ChromaDB 返回给 Agent。

### 决策 5：搜索结果附带上下文 chunk

Agent 拿到匹配 chunk 后还需要判断上下文，不应再调一次 `read_doc`。每个结果直接附带后续 2 个 chunk。

---

## 四、技术选型回顾

| 组件 | 选择 | 原因 |
|------|------|------|
| 向量库 | ChromaDB | 本地持久化，零配置 |
| 分词 | jieba | 中英混合文档搜索 |
| Embedding | 阿里云 text-embedding-v4 | dim=1024，中文友好 |
| LLM 清洗 | DeepSeek V4 Flash | 性价比 |
| MCP 框架 | FastMCP | 最快的 MCP Server 实现 |
| BM25 库 | rank-bm25 | 轻量，API 简洁 |

---

## 五、最终 API

```python
# Agent 首次调用
list_sources() → name/version/chunks

# 搜索（唯一入口）
search_docs(
    query="how to implement OAuth2 authentication",  # 语义描述
    source="fastapi",                                 # 文档源
    keywords=["OAuth2PasswordBearer", "Depends"],     # 可选，仅精确 API 查询时传
    limit=10,
) → rank/path/score/text/context_1/context_2

# 辅助
read_doc(path, source)    # 读取完整页面
list_docs(source, path)   # 浏览目录
```

---

## 六、削减对比

| 指标 | 初版 | 重构后 |
|------|------|--------|
| 源码行数 | ~1,359 | ~1,100 |
| MCP 工具数 | 5 | 4（合并 search_docs + grep_docs） |
| Chunk 数 (FastAPI) | 9,088 | 1,091 |
| Chunk 数 (SQLModel) | 4,946 | 425 |
| 碎片率 (<200 字) | 62% | ~10% |
| API 客户端实例化点 | 3 处 | 1 处 (`client.py`) |
| 模块级副作用 | 1 处 (server.py) | 0 |
