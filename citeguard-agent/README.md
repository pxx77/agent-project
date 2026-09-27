# CiteGuard

CiteGuard is an evidence-grounded citation verification agent. 它把文档问答拆成检索、生成、逐条核验三步：答案里每一条结论都必须能指回原文片段，检索不到相关片段时直接返回「证据不足」，而不是靠模型常识补写。

## 它做什么

上传一组文档并提问，CiteGuard 返回带引用的答案，同时给出每条引用的核验结果。核验逐条进行：模型对每个 claim 单独判断它是否被所指片段支撑，`support_rate` 由这些判定统计得出，不是写死的常数。如果 claim 引用的片段在检索结果里解析不到，该条直接判为 `unsupported`，并把 `citation_resolvability` 拉低——悬空引用不会被当成有效证据。

四种终态：

| 状态 | 触发条件 |
| --- | --- |
| `success` | 所有 claim 都被引用片段支撑 |
| `partial_support` | 既有被支撑的 claim，也有找不到支撑的 |
| `unsupported` | 所有 claim 都找不到支撑 |
| `insufficient_evidence` | 检索阶段就没有相关片段，直接返回证据不足 |

## 检索

`src/citeguard/retrieval.py` 实现 Okapi BM25：`k1=1.5`、`b=0.75`、Lucene 风格的 `+1` IDF 平滑，TF 饱和与文档长度归一化都在。中英混排按英文整词（长度 2 以上）加中日韩重叠 2-gram 切分，所以中文问句不需要外部分词器。

BM25 会给任何共享一个词的片段非零分数，光靠排序无法把无关问题挡在外面，因此索引另设一道相关性下限：查询词不少于 3 个时，片段至少命中其中 2 个；命中的词还要覆盖查询词 IDF 总量的 15% 以上。覆盖率的分母只统计真正出现在语料里的查询词——语料中根本不存在的词不可能被任何片段覆盖，把它们算进分母会让门槛随问句长度漂移。

除词面通道外还有一条稠密通道。`src/citeguard/embeddings.py` 提供两个同接口的编码器：`HashingEmbedder` 在本地把词元与字符 n-gram 哈希成 512 维稀疏向量，不需要 Key、不需要下载模型，离线评测与 CI 因此能真正跑到融合路径；`HttpEmbedder` 调用任意 OpenAI 兼容的 `/embeddings` 端点（DeepSeek 只提供对话补全、不提供 embedding 模型，所以这里对接的是 SiliconFlow、DashScope、OpenAI、Ollama、vLLM 之类）。向量按 `{index: weight}` 稀疏存储并做 L2 归一化，两种编码器共用同一段余弦相似度代码。

`HybridIndex` 用 RRF（`RRF_K=60`，Cormack 等 2009）融合两路排名，而不是把 BM25 分数与余弦相似度直接相加——两者量纲不可比，直接相加会由其中一路主导。融合后仍由词面下限决定拒答：稠密通道只补充词面没召回、但相似度达标的片段，`admit_dense_only` 默认关闭，所以多一条通道不会把「证据不足」的问题放行。

`CITEGUARD_RETRIEVER` 选择策略（默认 `auto`）：

| 取值 | 行为 |
| --- | --- |
| `lexical` | 只用 BM25 |
| `hybrid` | 融合 BM25 与配置的稠密编码器 |
| `auto`（默认） | 配了真实 embedding 端点就用 `hybrid`，否则退回 `lexical` |

`auto` 这条规则是实测结论，不是偏好，依据在下表。

### 词面 vs 融合：实测对比

`evals/run_retrieval_ablation.py` 在 24 条样例、5 个片段上让同一条端到端管线跑四种策略。指标口径直接复用 `evals/run_eval.py` 的 `evaluate()`，所以这里的数字与 `report.json` 逐字段可比；结果写进 `evals/retrieval_ablation.json`，脚本内没有任何硬编码分数。

| 策略 | `case_accuracy` | `cross_document_coverage` | `insufficient_evidence_rate` | `tokens_total` | `cost_cny_total` |
| --- | --- | --- | --- | --- | --- |
| `lexical`（当前默认） | 1.0 | 1.0 | 1.0 | 56463 | 0.07148 |
| `dense` | 0.8333 | 0.6667 | 0.0 | 65401 | 0.08046 |
| `hybrid` | 0.9583 | 0.6667 | 1.0 | 56056 | 0.070946 |
| `hybrid+augment` | 0.9583 | 0.6667 | 1.0 | 61145 | 0.076035 |

三条结论都是本仓库的实测，不是通用断言：

- 离线哈希编码器与 BM25 看的是同一类信号，融合不带来语义增益，还会在 `cross-document-audit-retention` 上打乱 BM25 已排序正确的 top-5，`case_accuracy` 因此从 1.0 降到 0.9583、`cross_document_coverage` 从 1.0 降到 0.6667。所以 `auto` 在离线配置下不打开融合。
- 稠密通道单独使用时没有拒答判据：3 个无关问题全部被作答，`insufficient_evidence_rate` 为 0。这正是保留词面下限作为拒答门槛的理由。
- 失去拒答能力也更贵：`dense` 比 `lexical` 多约 15.8% token、多约 12.6% 费用——拒答省掉了「回答 + 逐条核验」两次调用。

接入真实 embedding 服务（`CITEGUARD_EMBEDDING_PROVIDER=http`）后稠密通道才承载语义信息，届时可用 `CITEGUARD_RETRIEVER=hybrid` 启用融合；本仓库没有在该配置下测过，因此这里不给数。`tests/test_retrieval_accuracy.py` 把上面这些结论固化成断言，改动编码器或融合参数后默认路径若退化，会先在那里被拦住。

## 评估

`evals/` 下有 24 条样例，其中 21 条可回答、3 条应判为证据不足，覆盖单文档事实、跨文档对比、无答案问题三类。`evals/run_eval.py` 真实调用 agent 并统计指标，没有任何硬编码数值；跨文档样例只有在引用覆盖全部应引文档时才算正确，答一半不算过。

同一套 24 条样例跑了两轮，两组数字分别来自 `evals/report.json`（`mode: mock`）与 `evals/report.live.json`（`mode: live`，`deepseek-chat`）：

| 指标 | mock | live |
| --- | --- | --- |
| `case_accuracy` | 1.0 | 1.0 |
| `answerable_grounded_rate` | 1.0 | 1.0 |
| `insufficient_evidence_rate` | 1.0 | 1.0 |
| `cross_document_coverage` | 1.0 | 1.0 |
| `citation_resolvability` | 1.0 | 1.0 |
| `mean_support_rate` | 0.875 | 0.875 |
| `unsupported_claims_total` | 0 | 0 |
| `mean_latency_ms` | 0.654 | 4501.274 |
| `p95_latency_ms` | 0.973 | 8025.011 |
| `model_calls_total` | 42 | 68 |

`mean_support_rate` 是 21 条可答案例（各 1.0）与 3 条证据不足案例（各 0.0）汇总的结果，两种模式下都是 21/24，不是异常值。

两组比率相同，不代表 live 没有真的调用模型：`model_calls_total` 从 42 升到 68，`mean_latency_ms` 从 0.654 变成 4501，只有真实网络调用会带来这个量级的变化。mock 那列的延迟是本机的亚毫秒级读数，随机器与负载浮动，`tools/check_eval_drift.py` 因此把它排除在逐字段比对之外——它在这里的作用只是与 live 形成对照，不是一个稳定指标。比率不变来自指标口径——离线客户端每条可答案例只写 1 条 claim（合计 21 条），真实模型在同样的样例上写了 47 条 claim（全部被所指片段支撑，`unsupported_claims_total` 仍为 0），而 `support_rate` 衡量的是结论里未被支撑 claim 的占比，claim 变多不改变取值。引用条数也不一样：mock 41 条、live 25 条，live 的 25 条全部解析回原文。

`verification_probe` 在两种模式下都通过：探针给出 1 条悬空引用（`missing:999`）和 2 条无支撑 claim，核验器把它们判成 1 supported / 2 unsupported，并把 `citation_resolvability` 拉到 0.5。live 模式下这一步由 DeepSeek 完成，说明真实模型同样不会把悬空引用当成有效证据。

离线模式用词面判定代替模型判定，所以 mock 那列衡量的是检索、核验与统计管线本身；live 那列才是 DeepSeek 的端到端表现。

### Token 与费用

`src/citeguard/usage.py` 在每次调用后记账。`TokenUsage` 记录 prompt / completion token、调用次数与缓存命中拆分，并带一个 `measured` 布尔量：线上运行取 DeepSeek 响应体的 `usage` 字段（`measured=true`），离线回放没有真实计费，token 数按官方字符换算比例（1 个英文字符 ≈ 0.3 token，1 个中日韩字符 ≈ 0.6）在本机估算并标为 `measured=false`。这两件事必须一眼可辨，所以从不混用：`merge_usage` 对 `measured` 取合取，一次估算足以把整轮标成估算；估算一律按未命中的档位计价，不替调用方假设一个它并未获得的缓存折扣。

费用按 `pricing_model` 的官方单价折算，价格表以数据形式写在 `usage.py` 里，连同 `PRICE_SOURCE`（`https://api-docs.deepseek.com/zh-cn/quick_start/pricing`）、`PRICE_VERIFIED_ON`（`2026-09-28`）、币种与计量单位（`per_million_tokens`）一起导出，报价因此不会变成一个没有出处的数字。`price_for` 遇到价格表里没有的模型返回 `None` 而不是 `0.0`——「未计价」不该被读成「免费」，接口与界面据此显示「未计价」。已下线的 `deepseek-chat`、`deepseek-reasoner`（`2026/07/24 23:59` 北京时间停用）在表里指向 flash 的同一份单价，改一次价格不会只改到一个名字。

用量与费用同时出现在三个出口：`/health` 回传价格口径（模型、币种、单位、来源、核验日期），`/ask` 的 trace 带 `usage`、`billing_model` 与 `cost_cny`，Streamlit 界面显示本次 token 用量与本次费用，并标出「实测」还是「估算」。评测报告的 `usage` 块给出全量汇总，`retrieval` 块说明这组数字由哪个检索器产出并把策略对比指向 `evals/retrieval_ablation.json`——只指路、不抄数字，免得两份报告各说各话。

本仓库 `evals/report.json`（`mode: mock`）中 24 条样例合计 42 次模型调用、56463 token、0.07148 元，折合每条样例 0.002978 元，`usage_measured` 为 `false`，即上述估算口径，只能作量级参考。

复现真实模型数字只需要两步：在仓库根目录双击 `配置DeepSeek密钥.bat` 存一次 Key（Windows DPAPI 按当前用户加密，明文不落盘），再双击 `运行真实模型评测.bat`。后者等价于

```powershell
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File tools\deepseek_launcher.ps1 -Mode Eval
```

它会解密 Key、把 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` 与 `CITEGUARD_MOCK=0` 注入当前进程，用本项目 venv 跑 `evals/run_eval.py --live`，结果写到 `evals/report.live.json`，不覆盖上面这份 mock 基线；结束时无论成败都会还原进程内的环境变量。脚本会读回报告校验 `mode`，只要不是 `live` 就直接判失败退出，因此不存在以为是真实数字、其实是离线回放的误读。每次运行都会真实调用 DeepSeek 并覆盖 `evals/report.live.json`。

单元测试 94 项，覆盖分词、BM25 排序、相关性下限、向量与融合检索、价格表与成本核算、核验状态流转、API、配置与 MCP server：

```bash
uv run --extra dev pytest -q
```

## 运行

```bash
uv sync --extra dev
cp .env.example .env
```

离线运行把 `CITEGUARD_MOCK` 设为 `1`，不需要任何 Key。接 DeepSeek 时在 `.env` 填 `DEEPSEEK_API_KEY`，并把 `CITEGUARD_MOCK` 设为 `0`。密钥不会进入代码、日志或仓库。

检索相关开关也走环境变量，`.env.example` 里已列全：`CITEGUARD_RETRIEVER` 选策略，`CITEGUARD_EMBEDDING_PROVIDER` 与 `CITEGUARD_EMBEDDING_DIM` 配稠密编码器，`CITEGUARD_EMBEDDING_BASE_URL` / `_MODEL` / `_API_KEY` 只在 `provider=http` 时需要，`CITEGUARD_MIN_COVERAGE` 是词面相关性下限。默认值即离线可跑的一套：哈希编码器 + `auto`（解析为 BM25），不需要 Key 也不下载模型。

API 默认监听 8765：

```bash
uv run --extra dev uvicorn citeguard.api:app --reload --port 8765
```

浏览器打开 `http://127.0.0.1:8765/docs` 可以直接上传文档并提问，接口只有 `/health`、`/ingest`、`/ask` 三个。Streamlit 界面入口是 `src/citeguard/ui.py`，端口 8502。仓库根目录的 `一键启动两个项目.bat` 会同时拉起 CiteGuard 与 DataPilot；Key 由 `tools/deepseek_launcher.ps1` 用 Windows DPAPI 加密存在用户目录下，不落在仓库里。

支持上传 TXT、Markdown、PDF、DOCX，单个文件默认上限 10 MB。文档切块默认 700 字符、重叠 100 字符。

## MCP

`src/citeguard/mcp_server.py` 是一个真实的 MCP server，通过 stdio 暴露两个只读工具：

| 工具 | 参数 | 返回 |
| --- | --- | --- |
| `search_documents` | `query`（必填）、`limit`（1–50，默认 5） | 按当前检索器排序的片段（默认配置下即 BM25），含 `id`、`document_id` 与 `text` |
| `get_chunk` | `chunk_id`（必填） | 该片段全文；id 不存在时返回 `found=false`，不抛异常 |

两个工具都只是同一份索引上的只读适配器：索引由 `src/citeguard/state.py` 统一持有，HTTP API 与 MCP 读写的是同一个对象，所以 `POST /ingest` 上传的文档对 MCP 工具同样可见。这里刻意没有「验证结论」工具——核验依赖 agent 的多步流程，MCP 侧只交出原文片段，不代替 agent 给出裁决。

```bash
uv sync --extra mcp
uv run --extra mcp python -m citeguard.mcp_server
```

客户端配置里把 command 指向该解释器、args 写 `-m citeguard.mcp_server` 即可。MCP 是可选依赖：`mcp==1.13.1` 并同时锁 `sse-starlette==2.4.1`，因为 `mcp` 默认会拉进 sse-starlette 3.x，进而升到与本项目 `fastapi` 不兼容的 starlette。

`tests/test_mcp_server.py` 有 12 项测试，用官方 `ClientSession` 走真实协议，覆盖工具名与 JSON Schema、结构化返回、`limit` 越界与缺参被 schema 拦住、未知工具与未知 chunk id 的区分。其中一项用 stdio 客户端拉起子进程 `python -m citeguard.mcp_server`，确保这里写的启动命令真的可用。

## 边界

CiteGuard 只回答语料里能找到依据的问题，这也正是它会把无关问题判成 `insufficient_evidence` 的原因——这个行为有专门的测试与样例在盯。默认检索走词面匹配。仓库自带的离线编码器是哈希编码器，本质上仍是词面信号，所以默认路径不做同义改写、也不做语义召回，措辞与原文差得远的问句可能召回不到片段；稠密通道与 RRF 融合已经接好，但本仓库的对比是在离线编码器上做的，没有真实 embedding 服务下的数字，所以 `auto` 默认不打开它。延迟指标已经分两组记录下来：离线基线是亚毫秒级（`report.json` 记为 0.654 ms 均值），接入 DeepSeek 后是 4501 ms 均值、8025 ms p95，真实延迟由模型侧决定。
