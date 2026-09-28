# Agent Project

两个可以真实运行的 agent 项目：CiteGuard 把文档问答拆成检索、生成、逐条核验，DataPilot 把自然语言问题变成只读 SQL 并执行。两者都能不接 Key 离线跑通，都有记录真实模型结果的评测报告，也都各有一个走 stdio 的 MCP server。

## 项目

### CiteGuard

CiteGuard is an evidence-grounded citation verification agent. 答案里每条结论都要指回原文片段，检索不到相关片段时直接返回证据不足，而不是靠模型常识补写。检索是 Okapi BM25 加一道相关性下限，另接了一条稠密通道与 RRF 融合（`CITEGUARD_RETRIEVER=auto|lexical|hybrid`），但拒答判据始终来自词面下限；核验对每个 claim 单独判定，引用的片段解析不到就判为不支撑。每次运行按官方单价折算 token 与费用，估算与实测分开标记。24 条评测样例，94 项单元测试。细节见 [citeguard-agent/README.md](citeguard-agent/README.md)。

### DataPilot

DataPilot is a safe SQL data analysis agent. 上传 CSV、XLSX 或 SQLite 后用自然语言提问，生成的 SQL 先过只读策略再执行，失败时带着真实报错重试，最后给出图表规格与结论。管线是一条 LangGraph `StateGraph`（7 个节点、两处条件边、一条 `execute → repair → execute` 回边），`trace.states` 记录的是这次运行真实访问过的节点序列。执行走内存 SQLite，不跑模型生成的 Python，连接用完即关。每次运行按官方单价折算 token 与费用，估算与实测分开标记。14 条评测样例，66 项单元测试，另有 `Dockerfile` 构建的离线镜像。细节见 [datapilot-agent/README.md](datapilot-agent/README.md)。

## 快速开始

两个项目都用 uv 管理依赖，各自单独跑的方式写在项目 README 里。仓库根目录另有五个批处理入口，都调用 `tools/deepseek_launcher.ps1`：

| 入口 | 作用 |
| --- | --- |
| `一键启动两个项目.bat` | 同时拉起两个项目的 API 与 Streamlit 界面 |
| `启动CiteGuard.bat` | 只启动 CiteGuard |
| `启动DataPilot.bat` | 只启动 DataPilot |
| `配置DeepSeek密钥.bat` | 交互式存一次 Key，用 Windows DPAPI 按当前用户加密 |
| `运行真实模型评测.bat` | 用真实 DeepSeek 依次跑两个项目的评测 |

离线运行不需要任何 Key，两个项目的 `.env.example` 都把 `MOCK` 默认为 `1`。端口分工固定：

| 项目 | API | Streamlit 界面 |
| --- | --- | --- |
| CiteGuard | 8765 | 8502 |
| DataPilot | 8766 | 8501 |

启动脚本在占用端口前会先探测，如果端口被别的服务占着会直接报错退出，而不是静默换端口。接 DeepSeek 时脚本把 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` 和 `MOCK=0` 注入子进程，退出时还原；未配置 Key 就要求先跑配置入口。

## 评估

两个项目各有一套样例，同一套样例分别用离线客户端和 `deepseek-chat` 跑了两轮，数字来自 `evals/run_eval.py` 的真实运行，分别写进 `evals/report.json` 与 `evals/report.live.json`：

| 项目 | 样例 | 单元测试 | `case_accuracy` mock | `case_accuracy` live | `tokens_total` mock | `cost_cny_total` mock | `mean_latency_ms` mock | `mean_latency_ms` live | `model_calls_total` mock | `model_calls_total` live |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CiteGuard | 24 | 94 | 1.0 | 1.0 | 56463 | 0.07148 | 0.654 | 4501.274 | 42 | 68 |
| DataPilot | 14 | 66 | 1.0 | 0.7857 | 4442 | 0.004865 | 2.189 | 569.787 | 17 | 17 |

mock 那列衡量的是管线本身——离线客户端用词面判定或脚本化探针替代模型——live 那列才是模型端到端表现。CiteGuard 两轮比率相同但模型调用数从 42 升到 68、延迟从 0.654 ms 变成 4501 ms，只有真实网络调用会带来这个量级的变化；DataPilot live 有 3 条样例未通过，两条是模型没照做破坏性语句、被自身改写成了只读查询，一条是聚合结果缺 `ORDER BY`，这些都逐条写在项目 README 里，没有被抹平。

token 与费用来自同一批运行结果：离线回放没有真实计费，token 数按官方字符换算比例在本机估算、按官方单价折算成元，报告里 `usage_measured` 为 `false`，只作量级参考；接入 DeepSeek 后改取响应体的 `usage` 字段、标记为实测，两者从不混用。mock 那列的延迟都是毫秒量级、随机器与负载浮动，`tools/check_eval_drift.py` 因此把它排除在逐字段比对之外。

两份 live 报告的 `mode` 字段都会被回读校验，不是 `live` 就直接判失败，所以不会出现以为是真实数字、其实是离线回放的误读。

## 持续集成

`.github/workflows/ci.yml` 四个作业，每个对应仓库里的一项实际声明：

| 作业 | 运行环境 | 内容 |
| --- | --- | --- |
| Python checks | ubuntu-latest，两个项目各一次 | 按 `uv.lock` 建环境（`--locked`，锁文件与 `pyproject.toml` 不一致即失败）、跑单元测试与端到端冒烟、重跑离线评测与已提交报告逐字段比对（延迟除外）、确认没有改写任何被跟踪文件 |
| DataPilot container | ubuntu-latest | `docker build` 真实构建镜像，按镜像自带的环境变量启动容器，再从容器外访问 8766 端口跑 16 项 HTTP 断言 |
| DeepSeek launcher | windows-latest | 用 Windows PowerShell 5.1 跑 `tests/test_deepseek_launcher.ps1` 的启动脚本断言 |
| No committed credentials | ubuntu-latest | 拦截形似 API Key 的字符串，以及被跟踪的 `.env` 文件 |

评测报告是 README 里那些数字的依据，`tools/check_eval_drift.py` 每次提交都会重跑一遍离线评测并比对，所以引用的数字必须持续可复现，改不动也糊弄不过去。容器作业意味着 DataPilot 的容器支持由每次提交的构建与运行结果证实，而不是写在 README 里的一句话。

## 目录结构

```
citeguard-agent/    CiteGuard：检索、核验、API、Streamlit 界面、MCP server
datapilot-agent/    DataPilot：LangGraph 编排、只读策略、执行、修复重试、API、界面、MCP server、Dockerfile
tools/              deepseek_launcher.ps1（启动与密钥）与 check_eval_drift.py（评测漂移）
tests/              启动脚本的断言
docs/superpowers/   两个项目的设计与实施记录
```

## 已知边界

CiteGuard 的检索是词面匹配，不做同义改写也不做向量召回，措辞与原文差得远的问句可能召回不到片段。DataPilot 面向单表分析，跨表关联不在范围内，策略用正则做模式拦截，是防线而不是形式化验证，不能替代数据库侧的权限控制。`citeguard-agent/src/citeguard/workflow.py` 仍按 `Path(__file__).parents[2]` 定位 `fixtures/`，只在源码目录下成立；DataPilot 已经改成从环境变量、模块位置逐级上溯、当前工作目录依次尝试，容器因此才能启动。

## 许可证

MIT，见 [LICENSE](LICENSE)。
