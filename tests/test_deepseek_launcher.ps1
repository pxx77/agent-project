$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$script:Assertions = 0
$root = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$helper = Join-Path $root 'tools\deepseek_launcher.ps1'

function Assert-True {
    param(
        [Parameter(Mandatory = $true)]
        [bool] $Condition,

        [Parameter(Mandatory = $true)]
        [string] $Message
    )

    $script:Assertions++
    if (-not $Condition) {
        throw "Assertion failed: $Message"
    }
}

function Assert-Equal {
    param(
        [AllowNull()]
        $Actual,

        [AllowNull()]
        $Expected,

        [Parameter(Mandatory = $true)]
        [string] $Message
    )

    $script:Assertions++
    if ($Actual -ne $Expected) {
        throw "Assertion failed: $Message. Expected '$Expected', got '$Actual'."
    }
}

function Assert-Throws {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock] $Action,

        [Parameter(Mandatory = $true)]
        [string] $Message
    )

    $script:Assertions++
    try {
        & $Action
    }
    catch {
        return
    }

    throw "Assertion failed: $Message"
}

. $helper

$dataPilot = Get-AgentDefinition -Name 'DataPilot'
Assert-Equal $dataPilot.ApiPort 8766 'DataPilot API port is stable'
Assert-Equal $dataPilot.UiPort 8501 'DataPilot UI port is stable'
Assert-Equal $dataPilot.ApiModule 'datapilot.api:app' 'DataPilot API entry point is correct'
Assert-True (Test-Path -LiteralPath $dataPilot.Python) 'DataPilot uses its project virtual environment'
Assert-True ($dataPilot | Test-AgentDefinition) 'DataPilot definition validates'

$citeGuard = Get-AgentDefinition -Name 'CiteGuard'
Assert-Equal $citeGuard.ApiPort 8765 'CiteGuard API port is stable'
Assert-Equal $citeGuard.UiPort 8502 'CiteGuard UI port is stable'
Assert-Equal $citeGuard.ApiModule 'citeguard.api:app' 'CiteGuard API entry point is correct'
Assert-True (Test-Path -LiteralPath $citeGuard.Python) 'CiteGuard uses its project virtual environment'
Assert-True ($citeGuard | Test-AgentDefinition) 'CiteGuard definition validates'

$apiArguments = Get-AgentServiceArguments -Definition $dataPilot -Service 'Api'
Assert-Equal ($apiArguments -join '|') '-m|uvicorn|datapilot.api:app|--host|127.0.0.1|--port|8766' 'DataPilot API launch command is correct'
$uiArguments = Get-AgentServiceArguments -Definition $citeGuard -Service 'Ui'
Assert-Equal ($uiArguments -join '|') '-m|streamlit|run|src\citeguard\ui.py|--server.address|127.0.0.1|--server.port|8502|--server.headless|true' 'CiteGuard UI launch command is correct'

$selectedProjects = @(Resolve-AgentProjectNames -Projects @('DataPilot,CiteGuard'))
Assert-Equal $selectedProjects.Count 2 'Combined launcher selects two projects'
Assert-Equal $selectedProjects[0] 'DataPilot' 'Combined launcher starts DataPilot'
Assert-Equal $selectedProjects[1] 'CiteGuard' 'Combined launcher starts CiteGuard'

$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
try {
    $listener.Start()
    $listeningPort = ([Net.IPEndPoint] $listener.LocalEndpoint).Port
    Assert-True (Test-TcpPort -Port $listeningPort) 'Listening TCP port is detected'
}
finally {
    $listener.Stop()
}
Assert-True (-not (Test-TcpPort -Port $listeningPort)) 'Closed TCP port is reported as available'

$defaultKeyPath = [IO.Path]::GetFullPath((Get-DeepSeekKeyPath))
$repoPrefix = $root.TrimEnd('\') + '\'
Assert-True (-not $defaultKeyPath.StartsWith($repoPrefix, [StringComparison]::OrdinalIgnoreCase)) 'Default credential path stays outside the repository'
Assert-Throws { Get-AgentDefinition -Name 'UnknownAgent' } 'Unknown projects are rejected'

$secure = ConvertTo-SecureString 'launcher-test-key' -AsPlainText -Force
try {
    $plain = Convert-SecureStringToPlainText -SecureString $secure
    Assert-Equal $plain 'launcher-test-key' 'SecureString conversion preserves the key'
}
finally {
    $plain = $null
    $secure.Dispose()
}

$launcherContent = Get-Content -LiteralPath $helper -Raw
Assert-True ($launcherContent -notmatch "'RunService'") 'Launcher does not advertise an unimplemented RunService mode'

$parentKeyBefore = [Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY', [EnvironmentVariableTarget]::Process)
$childEnvironmentSecure = ConvertTo-SecureString 'launcher-child-only-key' -AsPlainText -Force
try {
    $childStartInfo = New-AgentProcessStartInfo -Definition $dataPilot -Service 'Api' -DeepSeekKey $childEnvironmentSecure
    Assert-Equal ([Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY', [EnvironmentVariableTarget]::Process)) $parentKeyBefore 'Launcher process environment is not modified with the key'
    Assert-Equal $childStartInfo.EnvironmentVariables['DEEPSEEK_API_KEY'] 'launcher-child-only-key' 'Child process receives the decrypted key directly'
}
finally {
    $childEnvironmentSecure.Dispose()
}

$oldOverride = $env:AGENT_PROJECT_DEEPSEEK_KEY_PATH
$testKeyPath = Join-Path ([IO.Path]::GetTempPath()) "agent-project-launcher-$PID.dpapi"
$env:AGENT_PROJECT_DEEPSEEK_KEY_PATH = $testKeyPath
try {
    Assert-Throws { Get-DeepSeekKey } 'Missing encrypted credential is rejected'

    $testSecure = ConvertTo-SecureString 'launcher-dpapi-roundtrip' -AsPlainText -Force
    try {
        Save-DeepSeekKey -SecureString $testSecure
    }
    finally {
        $testSecure.Dispose()
    }

    Assert-True (Test-Path -LiteralPath $testKeyPath -PathType Leaf) 'Encrypted credential file is created'
    $ciphertext = Get-Content -LiteralPath $testKeyPath -Raw
    Assert-True (-not $ciphertext.Contains('launcher-dpapi-roundtrip')) 'Encrypted file does not contain the plaintext key'

    $loadedSecure = Get-DeepSeekKey
    try {
        $loadedPlain = Convert-SecureStringToPlainText -SecureString $loadedSecure
        Assert-Equal $loadedPlain 'launcher-dpapi-roundtrip' 'DPAPI credential round trip succeeds'
    }
    finally {
        $loadedPlain = $null
        $loadedSecure.Dispose()
    }
}
finally {
    if (Test-Path -LiteralPath $testKeyPath) {
        Remove-Item -LiteralPath $testKeyPath -Force
    }
    $env:AGENT_PROJECT_DEEPSEEK_KEY_PATH = $oldOverride
}

$batchFiles = @(
    '6YWN572uRGVlcFNlZWvlr4bpkqUuYmF0',
    '5ZCv5YqoRGF0YVBpbG90LmJhdA==',
    '5ZCv5YqoQ2l0ZUd1YXJkLmJhdA==',
    '5LiA6ZSu5ZCv5Yqo5Lik5Liq6aG555uuLmJhdA=='
) | ForEach-Object {
    [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($_))
}
foreach ($batchFile in $batchFiles) {
    $batchPath = Join-Path $root $batchFile
    Assert-True (Test-Path -LiteralPath $batchPath -PathType Leaf) "$batchFile exists"
    $batchContent = Get-Content -LiteralPath $batchPath -Raw
    Assert-True ($batchContent -match 'tools\\deepseek_launcher\.ps1') "$batchFile delegates to the shared launcher"
    Assert-True ($batchContent -notmatch 'DEEPSEEK_API_KEY\s*=') "$batchFile does not embed a key assignment"
    Assert-True ($batchContent -notmatch 'sk-[A-Za-z0-9]{20,}') "$batchFile does not contain likely key material"
}

function Test-GitIgnored {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path
    )

    & git -C $root check-ignore --quiet -- $Path
    return $LASTEXITCODE -eq 0
}

foreach ($sensitivePath in @(
    '.env',
    'datapilot-agent/.env.local',
    'citeguard-agent/.env.production',
    'local-secrets/test.dpapi',
    '.streamlit/secrets.toml',
    'run.log'
)) {
    Assert-True (Test-GitIgnored -Path $sensitivePath) "$sensitivePath is ignored by Git"
}
foreach ($examplePath in @(
    '.env.example',
    'datapilot-agent/.env.example',
    'citeguard-agent/.env.example'
)) {
    Assert-True (-not (Test-GitIgnored -Path $examplePath)) "$examplePath remains trackable"
}

Write-Host "PASS: $script:Assertions launcher assertions"
