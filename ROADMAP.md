# 项目迭代路线图

> 2026-07-09 重构后更新。已完成项标记 ✅。

---

## 已解决

### ✅ B2. chunk overlap 膨胀
chunker 重构：overlap 仅施加于同一 section 内的非首个 chunk（人工边界），从原文本读 overlap 不级联。

### ✅ B3. _text_id 用 text[:100]
改用 `path::chunk_index` 去重，两路结果均有此信息。

### ✅ 搜索架构重构
- 单一入口 `search_docs`，`keywords` 参数独立传入
- 语义主干 + 关键词 presence 饱和乘性 boost-only 融合

### ✅ 融合重构:剔除 BM25（2026-07-11，详见 `docs/search-fusion-redesign.md`）
- 根因:向量基于去代码正文，BM25 搜含代码全文，API 标识符 89–100% 在代码块，TF 打分被代码符号高频膨胀 → 污染语义
- 方案:删 BM25（`rank-bm25`+`jieba`）；关键词改 presence + 饱和；boost-only 只重排语义候选、不引入新页
- 用数据敲定:`eval/` 评测集(25 query,人工 gold)+ 网格搜索 202 配置 + LLM-judge 交叉验证 → 乘性 `sem·(1+λ·kw)`、k1=2.0、λ=0.5、n=30
- 边界:关键词只在代码的召回缺口，实测本语料 0 缺口（gold 100% 在语义 top-30）

### ✅ D1. server.py 模块级初始化
改为惰性初始化（`_get_store()` / `_get_embed_client()`）。

### ✅ D2. Collection 命名承载业务逻辑
`get_or_create_collection` 存储 `metadata={"source": ..., "version": ...}`。
`list_sources` 优先读 metadata，回退解析 name。

### ✅ 统一 API 客户端
新增 `client.py`，`cleaner.py` / `server.py` / `cli/main.py` 统一调 `get_llm_client()` / `get_embedding_client()`。

### ✅ cleaner.py 加 LLM 调用重试
指数退避，最多 3 次。

### ✅ MCP Tool docstring 面向 Agent 重写
`search_docs` / `read_doc` / `list_sources` / `list_docs` 描述何时用、怎么传参、返回格式。

### ✅ 模型/CLI 清理
`models.py` 删 `datetime` import 和未使用字段，`cli/main.py` 用 `client.py`，加 `py.typed`。

### ✅ `grep_docs` 不再暴露为 MCP Tool
Agent 通过 `search_docs` 的 `keywords` 参数实现关键词搜索。

---

## 待处理

### 🟡 D3. 搜索质量验证体系（基础已落地）
**优先级**: 高
- ✅ 评测集 `eval/testset.yaml`（25 query，人工 gold）+ 网格搜索 `eval/harness.py` + LLM-judge `eval/judge.py` + 召回缺口 `eval/recall_gap.py`
- ✅ 已用它敲定融合方案（纯语义 vs 混合对比,见融合重构）
- 待办:扩充评测集覆盖面、加入回归门槛（改动后自动重跑对比基线）

### D4. Web 管理界面
**优先级**: 中
- 激活/停用文档源和版本
- 搜索调试面板
- CLI 的 `web` 命令目前是占位符

### D5. 多源搜索支持
**优先级**: 中
- `search_docs` 的 `source` 当前只能传单个，Agent 跨源搜索需多次调用
- 可选：支持 `source="*"` 跨所有激活源搜索

### D6. 增量索引
**优先级**: 低
- 当前每次 `index` 删全量重建
- 检测 `clean/` 目录变化，只索引新增/修改文件

### D7. 单元测试
**优先级**: 低（当前阶段）
- chunker / search / loader 基础测试

### D8. pyproject.toml 添加 publish 配置
**优先级**: 低
- PyPI 发布前的元数据完善

---

## 架构（重构后）

```
Agent                              MCP Server
─────                              ──────────
list_sources() ──────────────────→ 返回 name/version/chunks
                                   ↓
search_docs(query,source,          ChromaDB query (语义) → top-N 候选
            keywords,limit) ─────→ 候选内关键词 presence+饱和计分
                                   ↓
                                  乘性 boost 融合 → 返回结果
                                   ↓
read_doc(path,source) ──────────→ 读取 clean/*.md
```

```
search_docs 内部流程:
  query         → ChromaDB 语义搜索 → top-N 候选(主干)
  keywords 有值 → 候选完整文本(含代码) presence + 饱和计分(boost-only)
  乘性融合 sem·(1+λ·kw) → 去重(top-K by path) → 返回
```
