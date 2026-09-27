# DeepSeek Key 与双项目启动器设计

## 目标

为 `datapilot-agent` 和 `citeguard-agent` 提供一次配置、长期复用的 DeepSeek Key 体验：首次运行 `配置DeepSeek密钥.bat` 粘贴 Key，之后双击启动脚本即可分别或同时启动两个项目的 FastAPI 与 Streamlit 服务。明文 Key 不进入项目源码、`.env`、Git 或命令行参数。

## 现状与约束

- DataPilot 已有 FastAPI 入口 `datapilot.api:app` 与 Streamlit 入口 `src/datapilot/ui.py`。
- CiteGuard 已有 FastAPI 入口 `citeguard.api:app` 与 Streamlit 入口 `src/citeguard/ui.py`。
- 两个项目均通过 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` 读取模型配置。
- 每个项目已有 `.venv`，运行时优先使用项目内 `.venv\Scripts\python.exe`，不依赖全局 Python/uv。
- 固定端口：CiteGuard API `8765`、Streamlit `8502`；DataPilot API `8766`、Streamlit `8501`。
- 当前仓库存在用户既有未提交修改；本次变更只触及新增启动/密钥脚本、共享 Python helper、`.gitignore` 和本设计/计划文档。

## 方案

### 1. 凭据存储

在仓库外保存 `%LOCALAPPDATA%\Agent Project\deepseek-key.dpapi`。配置脚本调用共享 PowerShell helper：

1. 通过 `Read-Host -AsSecureString` 接收 Key，避免回显。
2. 将 `SecureString` 用当前 Windows 用户上下文的 `ConvertFrom-SecureString` 序列化；该格式由 DPAPI 保护，只能由同一用户上下文解密。
3. 使用临时文件写入后原子替换目标文件，并设置仅当前用户可读的 ACL（尽力执行，ACL 失败时报告警告而不输出 Key）。
4. 不在日志、错误消息或命令行参数中打印 Key。

启动 helper 读取该文件，调用 `ConvertTo-SecureString` 和 `Marshal\::SecureStringToBSTR` 在内存中恢复明文，并将其仅放入待启动 Python 子进程的环境块 `DEEPSEEK_API_KEY`。helper 自身不把 Key 写回磁盘；子进程继承后由项目现有 `Settings.from_env()` 使用。

### 2. 启动编排

共享 PowerShell helper 接收一个或多个项目名：

- 校验目录、入口文件、项目虚拟环境和端口可用性。
- 为每个项目启动两个独立 Python 子进程：一个 `python -m uvicorn ... --host 127.0.0.1 --port <api-port>`，一个 `python -m streamlit run ... --server.address 127.0.0.1 --server.port <ui-port> --server.headless true`。
- 设置项目模式变量为真实模型模式（`DATAPILOT_MOCK=0` 或 `CITEGUARD_MOCK=0`），同时注入共享 DeepSeek 配置。
- 通过 `ProcessStartInfo.EnvironmentVariables` 仅构造子进程环境块；启动器自身的进程环境不会写入明文 Key。隐藏模式使用 `CreateNoWindow`。
- 轮询 Streamlit 端口，服务就绪后调用 `Start-Process` 打开 `http://127.0.0.1:<port>`。
- 已有服务可复用：端口已被本项目占用时不重复启动；端口被其他进程占用时明确失败。
- 启动一个项目失败不会泄露 Key；总启动器会汇总错误并保留已成功启动的服务。

### 3. 用户入口

- `配置DeepSeek密钥.bat`：调用配置 helper，支持首次设置和覆盖更新。
- `启动DataPilot.bat`：只启动 DataPilot API/UI，并打开 `http://127.0.0.1:8501`。
- `启动CiteGuard.bat`：只启动 CiteGuard API/UI，并打开 `http://127.0.0.1:8502`。
- `一键启动两个项目.bat`：依次启动两个项目并打开两个 Streamlit 页面；不要求用户输入命令或 Key。

批处理文件只负责定位自身目录、调用 PowerShell helper 和暂停显示错误；核心逻辑集中在仓库内一个无密钥的 `tools\deepseek_launcher.ps1`，便于测试与维护。

## 安全边界

- DPAPI 文件位于 `%LOCALAPPDATA%`，不在仓库目录，且路径通过 `.gitignore` 和项目 `.gitignore` 双重忽略作为额外防护。
- `.env`、`.env.*`（保留 `.env.example`）、`.streamlit\secrets.toml`、密钥/凭据命名文件、日志和 Python 缓存均忽略。
- 不生成 `.env`，不把解密后的 Key 写入 PowerShell 脚本、批处理脚本、临时文本或日志。
- 启动器使用 `ProcessStartInfo.EnvironmentVariables` 构造子进程环境；绝不把 Key 拼入命令字符串、启动器环境或磁盘文件。

## 错误处理与用户体验

- 未配置 Key：提示“请先双击 配置DeepSeek密钥.bat”，返回非零退出码。
- 找不到 `.venv\Scripts\python.exe`：提示项目路径和建议命令，返回非零退出码。
- 端口占用：显示具体端口和项目名；不终止未知进程。
- 服务启动超时：显示对应 URL 与日志窗口提示，继续处理其他项目。
- 配置成功只显示“已保存/已更新（DPAPI）”，不显示 Key 内容或长度。

## 测试与验证

- PowerShell 静态检查：脚本语法解析、关键路径存在性、批处理引用目标存在。
- DPAPI 往返测试：使用临时 Windows 用户上下文可用的测试 Key，验证保存后可解密为原值；测试完成删除明确的临时测试文件。
- 安全扫描：仓库内搜索常见 Key 前缀和 `DEEPSEEK_API_KEY=` 明文赋值，仅允许 `.env.example` 的空值示例与测试代码中的非敏感占位符。
- Git 规则：`git check-ignore` 验证 `.env`、DPAPI 文件名、`.streamlit/secrets.toml`、日志等命中忽略规则，`.env.example` 不被忽略。
- 应用集成：使用已保存测试凭据启动两个 API，轮询 `/health` 确认服务分别返回 `provider=deepseek`；启动 Streamlit 后检查两页面端口可访问。验证结束关闭本次启动的进程，不触碰其他进程。
- 回归测试：分别运行两个项目现有 `pytest` 测试套件。

## 非目标

- 不修改两个项目的业务 API、UI 或模型客户端实现。
- 不实现跨 Windows 用户共享、云端密钥同步或系统服务安装。
- 不自动申请/轮换 DeepSeek Key；更新仍由用户重新运行配置脚本完成。
