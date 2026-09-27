# DataPilot

DataPilot is a safe SQL data analysis agent. 上传 CSV、XLSX 或 SQLite 文件，用自然语言提问，它生成只读 SQL、执行、在失败时带着错误重试，最后给出图表规格与可核对的结论。

## 它做什么

一次完整运行包含四步：剖析字段画像、生成 SQL、通过只读策略后执行、把结果整理成图表规格与结论。生成的 SQL 不会直接执行——先过一遍策略检查，只允许单条以 `SELECT` 或 `WITH` 开头的语句；`INSERT`、`UPDATE`、`DELETE`、`DROP`、`CREATE`、`PRAGMA`、`ATTACH`、`COPY` 等写操作与文件读取函数（`read_csv`、`read_parquet`、`httpfs`、`glob`）一律拒绝，多语句同样拒绝。

执行走内存 SQLite：上传的数据被载入一张临时表，查询结束后连接立即关闭，不在磁盘留副本。DataPilot 不执行模型生成的 Python 代码——结论只由查询结果和字段画像推导。

结论带核验：只有 SQL 真正执行成功并返回列时，`verification.consistent` 才为真，`reason` 里会写明返回了多少行；失败时 reason 直接给出错误原因。

## 错误修复

SQL 执行出错时，错误信息与失败语句会一起回灌给模型，让它基于真实报错改写，而不是重新猜。修复循环最多跑 `MAX_RETRIES` 次（默认 2），改写结果与上一次相同时提前退出，避免空转。

策略拒绝和可修复错误是两回事：被策略拦下的查询属于预期行为，不做重试；只有真正的执行错误才进入修复循环。两次修复都不成功时，agent 返回失败状态并保留最后一条 SQL 与错误信息，不会静默返回一个空结果。

## 评估

`evals/` 下有 14 条样例：3 条分析类（模型生成 SQL 并执行）、8 条危险请求（应被策略拦下）、2 条需要修复才能成功、1 条无法修复（应在有限重试后终止）。`evals/run_eval.py` 真实跑完整管线，指标全部由运行结果统计得出。

最近一次离线运行（`mode: mock`，14 条样例）：

| 指标 | 值 |
| --- | --- |
| `case_accuracy` | 1.0 |
| `execution_success_rate` | 1.0 |
| `grounded_result_rate` | 1.0 |
| `unsafe_block_rate` | 1.0 |
| `repair_success_rate` | 1.0 |
| `bounded_failure_rate` | 1.0 |
| `p95_latency_ms` | 1.175 |
| `model_calls_total` | 17 |

离线模式下 mock 类样例使用确定性模板客户端，scripted 类样例使用脚本化 SQL 探针，所以这些数字衡量的是管线本身（只读策略、执行、修复重试、图表、结论、核验），不是模型写 SQL 的质量。带 Key 跑 `--live` 时，mock 类样例改由 DeepSeek 生成 SQL，`evals/report.json` 会如实记录 `mode` 与 `provider`。

单元测试 27 项：

```bash
uv run --extra dev pytest -q
```

## 运行

```bash
uv sync --extra dev
cp .env.example .env
```

离线运行把 `DATAPILOT_MOCK` 设为 `1`，不需要 Key。接 DeepSeek 时在 `.env` 填 `DEEPSEEK_API_KEY` 并把 `DATAPILOT_MOCK` 设为 `0`；密钥不会进入代码、日志或仓库。

API 默认监听 8766：

```bash
uv run --extra dev uvicorn datapilot.api:app --reload --port 8766
```

浏览器打开 `http://127.0.0.1:8766/docs` 可直接上传数据并提问，接口是 `/health`、`/profile`、`/ask` 三个：先 `POST /profile` 上传文件并看字段画像，再 `POST /ask` 提交问题。Streamlit 界面入口是 `src/datapilot/ui.py`，默认端口 8501，可以查看字段画像、生成的 SQL、结果表格与运行轨迹。仓库根目录的 `一键启动两个项目.bat` 会同时拉起两个项目。

支持 CSV、XLSX、SQLite，单次载入行数默认上限 1000，由 `MAX_ROWS` 控制。

## 边界

DataPilot 面向单表分析，跨表关联不在当前范围内。所有列在载入时按文本处理，数值运算依赖 SQL 里的显式 `CAST`。策略用正则做模式拦截，是防线而不是形式化验证，不能替代数据库侧的权限控制。分析结论只做结果级核对，不做业务因果推断。
