# DeepSeek Launcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add DPAPI-protected DeepSeek Key setup plus one-click and per-project Windows launchers for DataPilot and CiteGuard without placing plaintext secrets in the repository.

**Architecture:** A single repository-local `tools/deepseek_launcher.ps1` owns DPAPI save/load, environment injection, process startup, readiness checks, and browser opening. Four thin `.bat` files invoke it with explicit modes/projects. Existing Python apps continue reading `DEEPSEEK_API_KEY` from their inherited environment.

**Tech Stack:** Windows PowerShell 5.1-compatible scripting, Windows DPAPI via `ConvertFrom-SecureString`/`ConvertTo-SecureString`, existing project `.venv` Python executables, uvicorn, Streamlit, PowerShell smoke tests, Git ignore rules.

**Spec:** `docs/superpowers/specs/2026-09-10-deepseek-launcher-design.md`

## Global Constraints

- Store the encrypted credential at `%LOCALAPPDATA%\Agent Project\deepseek-key.dpapi`; never create a repository `.env` containing the real key.
- Never include the plaintext key in source, Git-tracked files, command-line arguments, logs, or error output.
- Use API ports 8766/DataPilot and 8765/CiteGuard; use Streamlit ports 8501/DataPilot and 8502/CiteGuard.
- Keep existing user modifications intact and limit changes to this feature’s scripts, tests, ignore files, and design/plan documentation.
- Do not delete batches of files or directories; any cleanup must target one explicitly named temporary file or process.

---

### Task 1: Add failing launcher contract tests

**Files:**
- Create: `tests/test_deepseek_launcher.ps1`

**Interfaces:**
- Consumes: expected functions from `tools/deepseek_launcher.ps1`: `Get-DeepSeekKeyPath`, `Convert-SecureStringToPlainText`, `Save-DeepSeekKey`, `Get-DeepSeekKey`, `Get-AgentDefinition`, `Test-AgentDefinition`.
- Produces: executable smoke-test contract that later tasks must satisfy.

- [x] **Step 1: Write the failing test**

Create a PowerShell script that resolves the repository root, dot-sources `tools/deepseek_launcher.ps1`, and uses explicit `Assert-True`/`Assert-Equal` helpers to check:

```powershell
$root = Split-Path -Parent $PSScriptRoot
$helper = Join-Path $root 'tools\deepseek_launcher.ps1'
. $helper

$definition = Get-AgentDefinition -Name 'DataPilot'
Assert-Equal $definition.ApiPort 8766 'DataPilot API port'
Assert-Equal $definition.UiPort 8501 'DataPilot UI port'
Assert-Equal (Get-AgentDefinition -Name 'CiteGuard').ApiPort 8765 'CiteGuard API port'

$secure = ConvertTo-SecureString 'launcher-test-key' -AsPlainText -Force
$plain = Convert-SecureStringToPlainText $secure
Assert-Equal $plain 'launcher-test-key' 'SecureString round trip'

$definition | Test-AgentDefinition
```

The test must also assert the DPAPI path is outside the repository and that an unknown project is rejected.

- [x] **Step 2: Run test to verify it fails**

Run: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests\test_deepseek_launcher.ps1`

Expected: FAIL because `tools\deepseek_launcher.ps1` and the required functions do not yet exist.

- [x] **Step 3: Commit**

Do not commit yet; keep the red test in the working tree for Task 2.

### Task 2: Implement DPAPI credential and launcher core

**Files:**
- Create: `tools/deepseek_launcher.ps1`
- Modify: `tests/test_deepseek_launcher.ps1`

**Interfaces:**
- Consumes: Task 1 contract tests.
- Produces: `Get-DeepSeekKeyPath`, `Save-DeepSeekKey`, `Get-DeepSeekKey`, `Get-AgentDefinition`, `Test-AgentDefinition`, `Start-AgentProject`, `Start-SelectedProjects`, and script parameters `-Mode Configure|Start` / `-Projects`.

- [x] **Step 1: Implement the minimum DPAPI and project-definition functions**

Implement Windows PowerShell-compatible functions:

```powershell
function Get-DeepSeekKeyPath { }
function Convert-SecureStringToPlainText([Security.SecureString] $SecureString) { }
function Save-DeepSeekKey([Security.SecureString] $SecureString) { }
function Get-DeepSeekKey { }
function Get-AgentDefinition([string] $Name) { }
function Test-AgentDefinition([pscustomobject] $Definition) { }
```

`Save-DeepSeekKey` must write `ConvertFrom-SecureString` output to a temporary file under the same directory, atomically move it to `%LOCALAPPDATA%\Agent Project\deepseek-key.dpapi`, and apply a current-user-only ACL where possible. It must reject an empty key without printing it. `Get-DeepSeekKey` must throw a safe message when the file is absent or cannot decrypt.

- [x] **Step 2: Run the contract test to verify it passes**

Run: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests\test_deepseek_launcher.ps1`

Expected: PASS for definitions, path safety, and SecureString round trip.

- [x] **Step 3: Implement process launch and readiness functions**

Add `Test-TcpPort`, `Test-HttpReady`, `Wait-HttpReady`, `Start-AgentProcess`, `Start-AgentProject`, and `Start-SelectedProjects`.

For each project definition, launch the venv Python executable with a child-only environment block:

```powershell
$startInfo = [Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $python
$startInfo.WorkingDirectory = $definition.Root
$startInfo.UseShellExecute = $false
$startInfo.EnvironmentVariables['DEEPSEEK_API_KEY'] = $plainTextKey
$startInfo.EnvironmentVariables['DEEPSEEK_BASE_URL'] = 'https://api.deepseek.com'
$startInfo.EnvironmentVariables['DEEPSEEK_MODEL'] = 'deepseek-chat'
$startInfo.EnvironmentVariables[$definition.MockVariable] = '0'
[Diagnostics.Process]::Start($startInfo)
```

The plaintext value is held only long enough to populate the child environment block; the launcher process environment is never modified. Probe existing API ports via `/health` and UI ports via HTTP; reuse matching healthy services and report unrelated port conflicts without killing processes. Open only the Streamlit URL after readiness.

- [x] **Step 4: Run the full PowerShell test and a syntax parse**

Run: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests\test_deepseek_launcher.ps1`

Run: `powershell.exe -NoProfile -Command "$tokens=$null; [System.Management.Automation.Language.Parser]::ParseFile('tools\deepseek_launcher.ps1',[ref]$tokens,[ref]$null) | Out-Null; if($tokens.Count -gt 0){exit 1}"`

Expected: both commands exit 0.

### Task 3: Add batch entry points and Git protections

**Files:**
- Create: `配置DeepSeek密钥.bat`
- Create: `启动DataPilot.bat`
- Create: `启动CiteGuard.bat`
- Create: `一键启动两个项目.bat`
- Modify: `.gitignore`
- Modify: `datapilot-agent/.gitignore`
- Modify: `citeguard-agent/.gitignore`
- Modify: `tests/test_deepseek_launcher.ps1`

**Interfaces:**
- Consumes: Task 2 script modes and project names.
- Produces: double-clickable user entry points and repository-wide secret ignore policy.

- [x] **Step 1: Add thin batch wrappers**

Each batch file must use `%~dp0`, call `powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\deepseek_launcher.ps1" ...`, preserve the helper exit code, and pause only on failure. Required calls:

```bat
配置DeepSeek密钥.bat       -> -Mode Configure
启动DataPilot.bat          -> -Mode Start -Projects DataPilot
启动CiteGuard.bat          -> -Mode Start -Projects CiteGuard
一键启动两个项目.bat       -> -Mode Start -Projects DataPilot,CiteGuard
```

- [x] **Step 2: Add ignore rules without hiding examples**

Add rules to the root and both project `.gitignore` files for `.env`, `.env.*` with `!.env.example`, `.streamlit/secrets.toml`, `*.dpapi`, `*.secret`, `*.secrets`, `*.key`, local secret/config directories, logs, and Python caches. Verify the rules do not ignore `.env.example`.

- [x] **Step 3: Extend the smoke test for entry points and ignore policy**

Assert all four batch files exist, reference `tools\deepseek_launcher.ps1`, and contain no `DEEPSEEK_API_KEY=` assignment or likely key prefix. Invoke `git check-ignore` for representative paths (`.env`, `datapilot-agent/.env.local`, `local-secrets/test.dpapi`, `.streamlit/secrets.toml`, `run.log`) and assert `.env.example` is not ignored.

- [x] **Step 4: Run static tests**

Run: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests\test_deepseek_launcher.ps1`

Run: `git check-ignore -v .env datapilot-agent/.env.local local-secrets/test.dpapi .streamlit/secrets.toml run.log; if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}`

Run: `git check-ignore .env.example datapilot-agent/.env.example citeguard-agent/.env.example; if($LASTEXITCODE -eq 0){exit 1}`

Expected: smoke test passes, sensitive paths are ignored, examples are not ignored.

### Task 4: Verify real integration and regression suites

**Files:**
- Modify: `tests/test_deepseek_launcher.ps1` only if a verified defect requires a test correction.

**Interfaces:**
- Consumes: completed launcher, batch files, and existing project applications.
- Produces: fresh evidence for DPAPI round trip, API health, Streamlit readiness, and existing pytest suites.

- [x] **Step 1: Run DPAPI round-trip with a temporary test credential**

Use a uniquely named temporary DPAPI path override supported by the helper test hook, save a non-secret placeholder such as `launcher-integration-test`, read it back, assert equality, then delete only that explicitly named temporary file. Never print the value.

- [x] **Step 2: Start both APIs with the test credential and verify health**

Launch each API through the helper’s project startup path with the temporary credential, poll `http://127.0.0.1:8766/health` and `http://127.0.0.1:8765/health`, and assert JSON contains `status=ok` and `provider=deepseek`. Start both Streamlit services, poll ports 8501 and 8502, and verify HTTP 200 before opening/validating URLs. Stop only PIDs created by this verification run.

- [x] **Step 3: Run project regression suites**

Run: `datapilot-agent\.venv\Scripts\python.exe -m pytest datapilot-agent\tests -q`

Run: `citeguard-agent\.venv\Scripts\python.exe -m pytest citeguard-agent\tests -q`

Expected: exit 0 with zero failures in each suite.

- [x] **Step 4: Perform final security scan and review diff**

Run: `rg -n --hidden -g '!**/.git/**' -g '!**/.venv/**' 'sk-[A-Za-z0-9]{20,}|DEEPSEEK_API_KEY\s*=\s*[^\r\n#]+|deepseek-key\.dpapi' .`

Expected: no real key material; only intentional helper path/name references and empty examples/test placeholders may remain. Review `git diff --stat` and `git diff --check` before reporting completion.
