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

最近一次离线运行（`mode: mock`，24 条样例）：

| 指标 | 值 |
| --- | --- |
| `case_accuracy` | 1.0 |
| `answerable_grounded_rate` | 1.0 |
| `insufficient_evidence_rate` | 1.0 |
| `cross_document_coverage` | 1.0 |
| `citation_resolvability` | 1.0 |
| `mean_support_rate` | 0.875 |
| `unsupported_claims_total` | 0 |
| `p95_latency_ms` | 0.956 |
| `model_calls_total` | 42 |

`mean_support_rate` 是 21 条可答案例（各 1.0）与 3 条证据不足案例（各 0.0）的加权结果，不是异常值。离线模式用词面判定代替模型判定，所以这些数字衡量的是检索、核验与统计管线本身，不代表 DeepSeek 的判断质量；真实模型数字需要在有 Key 的环境跑 `--live`。

复现真实模型数字只需要两步：在仓库根目录双击 `配置DeepSeek密钥.bat` 存一次 Key（Windows DPAPI 按当前用户加密，明文不落盘），再双击 `运行真实模型评测.bat`。后者等价于

```powershell
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File tools\deepseek_launcher.ps1 -Mode Eval
```

它会解密 Key、把 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` 与 `CITEGUARD_MOCK=0` 注入当前进程，用本项目 venv 跑 `evals/run_eval.py --live`，结果写到 `evals/report.live.json`，不覆盖上面这份 mock 基线；结束时无论成败都会还原进程内的环境变量。脚本会读回报告校验 `mode`，只要不是 `live` 就直接判失败退出，因此不存在以为是真实数字、其实是离线回放的误读。每次运行会真实调用 DeepSeek，离线基线的 `model_calls_total` 是 42。

单元测试 31 项，覆盖分词、BM25 排序、相关性下限、核验状态流转、API 与配置：

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

## 边界

CiteGuard 只回答语料里能找到依据的问题，这也正是它会把无关问题判成 `insufficient_evidence` 的原因——这个行为有专门的测试与样例在盯。检索是词面匹配，不做同义改写，也不做向量召回，措辞与原文差得远的问句可能召回不到片段。延迟指标来自离线 mock，不代表接入真实模型后的响应时间。
