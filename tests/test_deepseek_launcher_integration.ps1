$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$helper = Join-Path $root 'tools\deepseek_launcher.ps1'
$testKeyPath = Join-Path ([IO.Path]::GetTempPath()) "agent-project-launcher-integration-$PID.dpapi"
$oldOverride = $env:AGENT_PROJECT_DEEPSEEK_KEY_PATH
$createdPids = New-Object Collections.Generic.List[int]

function Assert-Integration {
    param(
        [Parameter(Mandatory = $true)]
        [bool] $Condition,

        [Parameter(Mandatory = $true)]
        [string] $Message
    )

    if (-not $Condition) {
        throw "Integration assertion failed: $Message"
    }
    Write-Host "PASS: $Message"
}

$env:AGENT_PROJECT_DEEPSEEK_KEY_PATH = $testKeyPath
try {
    . $helper

    foreach ($port in @(8765, 8766, 8501, 8502)) {
        Assert-Integration (-not (Test-TcpPort -Port $port)) "port $port is free before the test"
    }

    $testValue = 'launcher-' + 'integration-' + 'test'
    $secure = ConvertTo-SecureString $testValue -AsPlainText -Force
    try {
        Save-DeepSeekKey -SecureString $secure
    }
    finally {
        $secure.Dispose()
    }

    $loaded = Get-DeepSeekKey
    try {
        $plain = Convert-SecureStringToPlainText -SecureString $loaded
        Assert-Integration ($plain -eq $testValue) 'DPAPI credential round trip'
    }
    finally {
        $plain = $null
        $loaded.Dispose()
    }

    $results = @(Start-SelectedProjects -Projects @('DataPilot', 'CiteGuard') -NoBrowser -HiddenWindows -TimeoutSeconds 45)
    Assert-Integration ($results.Count -eq 2) 'both projects report ready'
    foreach ($result in $results) {
        foreach ($processId in $result.CreatedPids) {
            $createdPids.Add([int] $processId)
        }
    }
    Assert-Integration ($createdPids.Count -eq 4) 'two API and two UI processes were created'

    $dataPilotHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8766/health' -TimeoutSec 5
    Assert-Integration ($dataPilotHealth.status -eq 'ok' -and $dataPilotHealth.provider -eq 'deepseek') 'DataPilot API uses DeepSeek mode'
    $citeGuardHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/health' -TimeoutSec 5
    Assert-Integration ($citeGuardHealth.status -eq 'ok' -and $citeGuardHealth.provider -eq 'deepseek') 'CiteGuard API uses DeepSeek mode'

    Assert-Integration (Test-HttpReady -Uri 'http://127.0.0.1:8501/') 'DataPilot Streamlit page is reachable'
    Assert-Integration (Test-HttpReady -Uri 'http://127.0.0.1:8502/') 'CiteGuard Streamlit page is reachable'
}
finally {
    foreach ($processId in $createdPids) {
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $testKeyPath) {
        Remove-Item -LiteralPath $testKeyPath -Force
    }
    $env:AGENT_PROJECT_DEEPSEEK_KEY_PATH = $oldOverride
}
