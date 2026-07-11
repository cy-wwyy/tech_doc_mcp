# eval —— 搜索质量评测体系

把"搜索好不好"从主观判断变成可排名、可回归的数字。落地 ROADMAP D3。
设计与选参依据见 [`../docs/search-fusion-redesign.md`](../docs/search-fusion-redesign.md)。

## 文件

| 文件 | 作用 |
|------|------|
| `testset.yaml` | 人工标注的查询集:`query` + `keywords` + `gold`(期望命中的页面 path) |
| `harness.py` | 网格搜索:对每套融合配置算 nDCG@10 / MRR / Recall@5,输出 `results.md` 排行榜 |
| `judge.py` | LLM-as-judge:对决赛圈配置独立打分(graded nDCG),交叉验证人工 gold |
| `recall_gap.py` | 召回缺口诊断:量化有多少 gold 落在语义候选之外(boost-only 边界) |
| `results.md` | 网格排行榜(生成物) |
| `.cache/` | embedding + 候选 + judge 判分缓存(gitignore,可重生成) |

## 运行

```bash
uv run python eval/harness.py            # 网格搜索;缓存缺失时自动调 API 构建
uv run python eval/harness.py --rebuild  # 强制重建候选缓存(改了文档/重索引后必须)
uv run python eval/judge.py              # LLM 交叉验证(需先跑过 harness)
uv run python eval/recall_gap.py         # 召回缺口诊断
```

## 加测试查询

在 `testset.yaml` 追加一条,`gold` 填该 query 期望命中的页面 path(可多个)。
尽量覆盖:单/多关键词、以及"关键词在代码里高频"的对抗样本。

## 两种使用场景(注意不对称)

**① 迭代融合方式 —— 干净直接。** 文档没变,候选缓存复用,只换 `fuse` 逻辑重跑即可 A/B,零额外维护。

**② 改了文档 —— 能用,但有 3 个"静默陷阱"(务必知道):**

1. **缓存不会自动失效。** 候选缓存按 `(source, version, query)` 哈希;若**重索引了同一 version**,内容变了但哈希没变 → 仍用旧候选算分。**重索引后必须 `--rebuild`。**
2. **version 是钉死的。** testset 里写死了版本号(如 `0.139.0`);索引了新版本要手动改,否则测的是老版本。
3. **gold 路径会漂移(最阴)。** 文档重构时某 gold 页被改名/拆分 → 那条 gold 永远命中不了 → nDCG 掉,但**不是搜索变差,是标准答案失效了**,数字会骗你。

## 待做的加固(TODO,尚未实现)

为让"改文档后重跑"也像"改融合"一样可信,可加:

- **gold 预检**:harness 开跑前校验每个 gold path 在对应 collection 真实存在,不存在就大声报错,而非静默算 miss(直接防上面第 3 点)。
- **缓存指纹**:候选缓存 key 里加 `collection.count()`(或 ids 指纹),数量变了自动失效,省掉手动 `--rebuild`(防第 1 点)。
- **version 支持 `latest`**:testset 用 `latest` 时自动解析到最新已索引版本(缓解第 2 点)。
- **评测集扩充**:当前 25 条、gold 单人起草,是质量链上最薄一环;按真实查询分布补样本、gold 双人校验,尺子越准决策越稳。
