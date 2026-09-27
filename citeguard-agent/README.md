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
| `mean_latency_ms` | 0.471 | 4501.274 |
| `p95_latency_ms` | 0.956 | 8025.011 |
| `model_calls_total` | 42 | 68 |

`mean_support_rate` 是 21 条可答案例（各 1.0）与 3 条证据不足案例（各 0.0）汇总的结果，两种模式下都是 21/24，不是异常值。

两组比率相同，不代表 live 没有真的调用模型：`model_calls_total` 从 42 升到 68，`mean_latency_ms` 从 0.471 变成 4501，只有真实网络调用会带来这个量级的变化。比率不变来自指标口径——离线客户端每条可答案例只写 1 条 claim（合计 21 条），真实模型在同样的样例上写了 47 条 claim（全部被所指片段支撑，`unsupported_claims_total` 仍为 0），而 `support_rate` 衡量的是结论里未被支撑 claim 的占比，claim 变多不改变取值。引用条数也不一样：mock 41 条、live 25 条，live 的 25 条全部解析回原文。

`verification_probe` 在两种模式下都通过：探针给出 1 条悬空引用（`missing:999`）和 2 条无支撑 claim，核验器把它们判成 1 supported / 2 unsupported，并把 `citation_resolvability` 拉到 0.5。live 模式下这一步由 DeepSeek 完成，说明真实模型同样不会把悬空引用当成有效证据。

离线模式用词面判定代替模型判定，所以 mock 那列衡量的是检索、核验与统计管线本身；live 那列才是 DeepSeek 的端到端表现。

复现真实模型数字只需要两步：在仓库根目录双击 `配置DeepSeek密钥.bat` 存一次 Key（Windows DPAPI 按当前用户加密，明文不落盘），再双击 `运行真实模型评测.bat`。后者等价于

```powershell
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File tools\deepseek_launcher.ps1 -Mode Eval
```

它会解密 Key、把 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` 与 `CITEGUARD_MOCK=0` 注入当前进程，用本项目 venv 跑 `evals/run_eval.py --live`，结果写到 `evals/report.live.json`，不覆盖上面这份 mock 基线；结束时无论成败都会还原进程内的环境变量。脚本会读回报告校验 `mode`，只要不是 `live` 就直接判失败退出，因此不存在以为是真实数字、其实是离线回放的误读。每次运行都会真实调用 DeepSeek 并覆盖 `evals/report.live.json`。

单元测试 43 项，覆盖分词、BM25 排序、相关性下限、核验状态流转、API、配置与 MCP server：

```bash
uv run --extra dev pytest -q
```

## 运行

```bash
uv sync --extra dev
cp .env.example .env
```

离线运行把 `CITEGUARD_MOCK` 设为 `1`，不需要任何 Key。接 DeepSeek 时在 `.env` 填 `DEEPSEEK_API_KEY`，并把 `CITEGUARD_MOCK` 设为 `0`。密钥不会进入代码、日志或仓库。

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
| `search_documents` | `query`（必填）、`limit`（1–50，默认 5） | 按 BM25 排序的片段，含 `id`、`document_id` 与 `text` |
| `get_chunk` | `chunk_id`（必填） | 该片段全文；id 不存在时返回 `found=false`，不抛异常 |

两个工具都只是同一份索引上的只读适配器：索引由 `src/citeguard/state.py` 统一持有，HTTP API 与 MCP 读写的是同一个对象，所以 `POST /ingest` 上传的文档对 MCP 工具同样可见。这里刻意没有「验证结论」工具——核验依赖 agent 的多步流程，MCP 侧只交出原文片段，不代替 agent 给出裁决。

```bash
uv sync --extra mcp
uv run --extra mcp python -m citeguard.mcp_server
```

客户端配置里把 command 指向该解释器、args 写 `-m citeguard.mcp_server` 即可。MCP 是可选依赖：`mcp==1.13.1` 并同时锁 `sse-starlette==2.4.1`，因为 `mcp` 默认会拉进 sse-starlette 3.x，进而升到与本项目 `fastapi` 不兼容的 starlette。

`tests/test_mcp_server.py` 有 12 项测试，用官方 `ClientSession` 走真实协议，覆盖工具名与 JSON Schema、结构化返回、`limit` 越界与缺参被 schema 拦住、未知工具与未知 chunk id 的区分。其中一项用 stdio 客户端拉起子进程 `python -m citeguard.mcp_server`，确保这里写的启动命令真的可用。

## 边界

CiteGuard 只回答语料里能找到依据的问题，这也正是它会把无关问题判成 `insufficient_evidence` 的原因——这个行为有专门的测试与样例在盯。检索是词面匹配，不做同义改写，也不做向量召回，措辞与原文差得远的问句可能召回不到片段。延迟指标已经分两组记录下来：离线基线是确定性的 0.471 ms，接入 DeepSeek 后是 4501 ms 均值、8025 ms p95，真实延迟由模型侧决定。
