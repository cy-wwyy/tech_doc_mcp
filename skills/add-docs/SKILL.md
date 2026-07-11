---
name: add-docs
description: "添加文档源 — 支持框架名称、URL 两种输入，自动爬取并保存原始数据。用法: /add-docs <name|url>"
---

# /add-docs — 文档数据获取

## 输入

收到用户请求后，按以下优先级判断：

| 优先级 | 用户输入特征 | 行为 | 示例 |
|---|---|---|---|
| 1 | 包含 URL | 直接用该 URL 爬取 | `/add-docs https://react.dev` |
| 2 | 包含明确的框架/工具名称 | 用 Claude 自身知识推断官网 URL | `/add-docs fastapi` → 推断为 `https://fastapi.tiangolo.com` |

**模糊输入必须二次确认：**

以下情况必须追问用户，不允许猜测：
- 框架名拼写错漏（如 "fastPI"、"reactt"）→ 追问："你是指 FastAPI 还是其他？"
- 名称有歧义（如 "react" 可能指 react.dev 或 reactnative.dev）→ 追问："你指的是 React 还是 React Native？"
- 无法判断是框架名还是路径（如 "docs"）→ 追问
- 完全不知道是什么 → 追问："请提供该框架的文档官网 URL"


## 工作流

### 阶段 1: 分析站点 + 过滤非技术页面

**Claude 做的事情（2-3 次请求）：**

1. 获取 `sitemap.xml` 或解析首页导航，得到完整页面列表
2. **分析 URL 路径，判断页面类型，过滤掉对编码参考价值不大的内容：**

   | 保留（技术参考） | 跳过（社区/运营/历史） |
   |---|---|
   | `/tutorial/` `/guide/` `/docs/` `/learn/` 等教学类 | `/help/` `/sponsor/` `/donate/` |
   | `/reference/` `/api/` `/modules/` 等 API 参考类 | `/newsletter/` `/blog/` `/changelog/` |
   | `/advanced/` `/deep-dive/` `/cookbook/` 等进阶类 | `/release-notes/` `/releases/` `/versions/` |
   | `/deployment/` `/config/` `/cli/` 等实操类 | `/about/` `/team/` `/history/` |
   | `/getting-started/` `/quickstart/` `/install/` 等入门类 | `/benchmarks/` `/alternatives/` `/comparison/` |
   | `/how-to/` `/examples/` `/recipes/` 等示例类 | `/contributing/` `/code-of-conduct/` |
   | `/concepts/` `/core/` `/basics/` 等概念类 | `/community/` `/showcase/` `/testimonials/` |

   **原则：保留开发者写代码时可能参考的内容，跳过运营推广类页面。**

   如果不确定某类页面的价值，**宁可保留**——后续 LLM 清洗时还会二次筛选。

3. 访问 2-3 个代表性页面，分析 HTML 结构：
   - 哪个 CSS 选择器包含正文？
   - 需要跳过哪些元素（导航/侧边/页脚）？
   - 站点类型？（MkDocs / Docusaurus / Sphinx / 自定义）
4. 输出：**过滤后的 URL 列表** + CSS 选择器 + 排除元素列表

### 阶段 2: 生成爬虫脚本

**Claude 生成一个独立脚本，在终端运行（不进 Claude 上下文）：**

脚本做这些事：
1. `httpx.AsyncClient` 并发爬取所有页面 HTML（并发 5，间隔 1s+）
2. 用阶段 1 确定的选择器提取正文 HTML
3. 移除导航/侧边栏/页脚等元素
4. 保存提取后的原始内容到 `docs/{name}/raw/`
5. 打印进度（✅ 第 N 页, ⚠ 跳过某个页面）

关键：这个脚本**在终端运行**，不在 Claude 上下文中。


### 阶段 3: 验证

1. 抽样检查 2-3 个 `raw/` 文件的提取质量
2. 报告：站点总页面数 → 过滤后保留数 → 爬取成功/失败数

## 约束

- Claude 只做分析 + 生成脚本，不做重复体力活
- 爬虫脚本并发 ≤5，间隔 ≥1 秒（友好爬取）
- 提取结果保存到 `docs/{name}/raw/`，原始数据不丢
- 如果用户未指定 `--name`，从 URL 域名或框架名推断
