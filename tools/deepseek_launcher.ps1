[CmdletBinding()]
param(
    [ValidateSet('Configure', 'Start', 'Eval')]
    [string] $Mode = 'Start',

    [string[]] $Projects = @(),

    [switch] $NoBrowser,

    [switch] $HiddenWindows,

    [string] $EvalReport = 'evals\report.live.json',

    [ValidateRange(1, 300)]
    [int] $TimeoutSeconds = 60
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$script:RepositoryRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))

function Get-DeepSeekKeyPath {
    [CmdletBinding()]
    param()

    if (-not [string]::IsNullOrWhiteSpace($env:AGENT_PROJECT_DEEPSEEK_KEY_PATH)) {
        return [IO.Path]::GetFullPath($env:AGENT_PROJECT_DEEPSEEK_KEY_PATH)
    }

    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        throw 'Unable to locate the Windows local application data directory.'
    }

    return Join-Path $env:LOCALAPPDATA 'Agent Project\deepseek-key.dpapi'
}

function Convert-SecureStringToPlainText {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [Security.SecureString] $SecureString
    )

    $bstr = [IntPtr]::Zero
    try {
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureString)
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    }
    finally {
        if ($bstr -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        }
    }
}

function Set-PrivateFileAcl {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path
    )

    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent().User
        $acl = New-Object Security.AccessControl.FileSecurity
        $acl.SetOwner($identity)
        $acl.SetAccessRuleProtection($true, $false)
        $rule = New-Object Security.AccessControl.FileSystemAccessRule(
            $identity,
            [Security.AccessControl.FileSystemRights]::FullControl,
            [Security.AccessControl.AccessControlType]::Allow
        )
        [void] $acl.AddAccessRule($rule)
        Set-Acl -LiteralPath $Path -AclObject $acl
    }
    catch {
        Write-Warning 'The key is DPAPI-encrypted, but its file permissions could not be restricted.'
    }
}

function Save-DeepSeekKey {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [Security.SecureString] $SecureString
    )

    $plainText = $null
    $temporaryPath = $null
    try {
        $plainText = Convert-SecureStringToPlainText -SecureString $SecureString
        if ([string]::IsNullOrWhiteSpace($plainText)) {
            throw 'DeepSeek Key cannot be empty.'
        }

        $path = Get-DeepSeekKeyPath
        $directory = Split-Path -Parent $path
        if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
            [void] (New-Item -ItemType Directory -Path $directory -Force)
        }

        $encrypted = ConvertFrom-SecureString -SecureString $SecureString
        $temporaryPath = "$path.$PID.$([Guid]::NewGuid().ToString('N')).tmp"
        [IO.File]::WriteAllText($temporaryPath, $encrypted, [Text.Encoding]::ASCII)

        if (Test-Path -LiteralPath $path -PathType Leaf) {
            [IO.File]::Replace($temporaryPath, $path, $null)
        }
        else {
            [IO.File]::Move($temporaryPath, $path)
        }
        $temporaryPath = $null
        Set-PrivateFileAcl -Path $path
    }
    finally {
        $plainText = $null
        if ($temporaryPath -and (Test-Path -LiteralPath $temporaryPath)) {
            Remove-Item -LiteralPath $temporaryPath -Force
        }
    }
}

function Get-DeepSeekKey {
    [CmdletBinding()]
    param()

    $path = Get-DeepSeekKeyPath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw 'DeepSeek Key is not configured. Run the key configuration batch file first.'
    }

    try {
        $encrypted = [IO.File]::ReadAllText($path, [Text.Encoding]::ASCII).Trim()
        if ([string]::IsNullOrWhiteSpace($encrypted)) {
            throw 'empty credential'
        }
        return ConvertTo-SecureString -String $encrypted
    }
    catch {
        throw 'DeepSeek Key cannot be decrypted. Sign in as the Windows user that saved it, then configure it again.'
    }
}

function Get-AgentDefinition {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name
    )

    switch ($Name.Trim().ToLowerInvariant()) {
        'datapilot' {
            $root = Join-Path $script:RepositoryRoot 'datapilot-agent'
            return [pscustomobject]@{
                Name          = 'DataPilot'
                Root          = $root
                Python        = Join-Path $root '.venv\Scripts\python.exe'
                ApiModule     = 'datapilot.api:app'
                ApiTitle      = 'DataPilot'
                ApiPort       = 8766
                UiScript      = 'src\datapilot\ui.py'
                UiMarker      = 'datapilot\ui.py'
                UiPort        = 8501
                MockVariable  = 'DATAPILOT_MOCK'
            }
        }
        'citeguard' {
            $root = Join-Path $script:RepositoryRoot 'citeguard-agent'
            return [pscustomobject]@{
                Name          = 'CiteGuard'
                Root          = $root
                Python        = Join-Path $root '.venv\Scripts\python.exe'
                ApiModule     = 'citeguard.api:app'
                ApiTitle      = 'CiteGuard'
                ApiPort       = 8765
                UiScript      = 'src\citeguard\ui.py'
                UiMarker      = 'citeguard\ui.py'
                UiPort        = 8502
                MockVariable  = 'CITEGUARD_MOCK'
            }
        }
        default {
            throw "Unsupported project name: $Name"
        }
    }
}

function Test-AgentDefinition {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true, ValueFromPipeline = $true)]
        [pscustomobject] $Definition
    )

    process {
        if (-not (Test-Path -LiteralPath $Definition.Root -PathType Container)) {
            throw "Project directory was not found: $($Definition.Root)"
        }
        if (-not (Test-Path -LiteralPath $Definition.Python -PathType Leaf)) {
            throw "$($Definition.Name) virtual environment was not found: $($Definition.Python)"
        }
        $uiPath = Join-Path $Definition.Root $Definition.UiScript
        if (-not (Test-Path -LiteralPath $uiPath -PathType Leaf)) {
            throw "$($Definition.Name) Streamlit entry point was not found: $uiPath"
        }
        return $true
    }
}

function Get-AgentServiceArguments {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject] $Definition,

        [Parameter(Mandatory = $true)]
        [ValidateSet('Api', 'Ui')]
        [string] $Service
    )

    if ($Service -eq 'Api') {
        return @(
            '-m', 'uvicorn', $Definition.ApiModule,
            '--host', '127.0.0.1',
            '--port', [string] $Definition.ApiPort
        )
    }

    return @(
        '-m', 'streamlit', 'run', $Definition.UiScript,
        '--server.address', '127.0.0.1',
        '--server.port', [string] $Definition.UiPort,
        '--server.headless', 'true'
    )
}

function Resolve-AgentProjectNames {
    [CmdletBinding()]
    param(
        [string[]] $Projects
    )

    $requested = New-Object Collections.Generic.List[string]
    foreach ($item in @($Projects)) {
        if ($null -eq $item) {
            continue
        }
        foreach ($part in $item.Split(',')) {
            $name = $part.Trim()
            if (-not [string]::IsNullOrWhiteSpace($name)) {
                $requested.Add($name)
            }
        }
    }

    if ($requested.Count -eq 0) {
        $requested.Add('DataPilot')
        $requested.Add('CiteGuard')
    }

    $resolved = New-Object Collections.Generic.List[string]
    foreach ($name in $requested) {
        $canonicalName = (Get-AgentDefinition -Name $name).Name
        if (-not $resolved.Contains($canonicalName)) {
            $resolved.Add($canonicalName)
        }
    }
    return $resolved.ToArray()
}

function Test-TcpPort {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateRange(1, 65535)]
        [int] $Port
    )

    $client = New-Object Net.Sockets.TcpClient
    try {
        $asyncResult = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $asyncResult.AsyncWaitHandle.WaitOne(500)) {
            return $false
        }
        $client.EndConnect($asyncResult)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Test-HttpReady {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $Uri
    )

    try {
        $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 400
    }
    catch {
        return $false
    }
}

function Wait-HttpReady {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $Uri,

        [ValidateRange(1, 300)]
        [int] $TimeoutSeconds = 60
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if (Test-HttpReady -Uri $Uri) {
            return $true
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    return $false
}

function Get-ListeningProcessCommandLine {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [int] $Port
    )

    try {
        $connection = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop |
            Select-Object -First 1
        if ($null -eq $connection) {
            return $null
        }
        $process = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $($connection.OwningProcess)" -ErrorAction Stop
        return $process.CommandLine
    }
    catch {
        return $null
    }
}

function Test-AgentApiReady {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject] $Definition
    )

    try {
        $rootResponse = Invoke-RestMethod -Uri "http://127.0.0.1:$($Definition.ApiPort)/" -TimeoutSec 2
        $healthResponse = Invoke-RestMethod -Uri "http://127.0.0.1:$($Definition.ApiPort)/health" -TimeoutSec 2
        $nameMatches = [string] $rootResponse.name -like "$($Definition.Name)*"
        return $nameMatches -and $healthResponse.status -eq 'ok' -and $healthResponse.provider -eq 'deepseek'
    }
    catch {
        return $false
    }
}

function Wait-AgentApiReady {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject] $Definition,

        [ValidateRange(1, 300)]
        [int] $TimeoutSeconds = 60
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if (Test-AgentApiReady -Definition $Definition) {
            return $true
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    return $false
}

function Test-AgentUiReady {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject] $Definition
    )

    $uri = "http://127.0.0.1:$($Definition.UiPort)/"
    if (-not (Test-HttpReady -Uri $uri)) {
        return $false
    }

    $commandLine = Get-ListeningProcessCommandLine -Port $Definition.UiPort
    if ([string]::IsNullOrWhiteSpace($commandLine)) {
        return $false
    }
    $normalized = $commandLine.Replace('/', '\').ToLowerInvariant()
    return $normalized.Contains($Definition.UiMarker.ToLowerInvariant())
}

function ConvertTo-ProcessArgumentString {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string[]] $Arguments
    )

    $quotedArguments = foreach ($argument in @($Arguments)) {
        $text = [string] $argument
        if ($text -match '[\s"]') {
            '"' + $text.Replace('"', '\"') + '"'
        }
        else {
            $text
        }
    }
    return ($quotedArguments -join ' ')
}

function New-AgentProcessStartInfo {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject] $Definition,

        [Parameter(Mandatory = $true)]
        [ValidateSet('Api', 'Ui')]
        [string] $Service,

        [Parameter(Mandatory = $true)]
        [Security.SecureString] $DeepSeekKey,

        [switch] $HiddenWindows
    )

    [void] (Test-AgentDefinition -Definition $Definition)
    $plainText = $null
    try {
        $plainText = Convert-SecureStringToPlainText -SecureString $DeepSeekKey
        if ([string]::IsNullOrWhiteSpace($plainText)) {
            throw 'DeepSeek Key cannot be empty.'
        }

        $startInfo = New-Object Diagnostics.ProcessStartInfo
        $startInfo.FileName = $Definition.Python
        $startInfo.WorkingDirectory = $Definition.Root
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = [bool] $HiddenWindows
        $startInfo.Arguments = ConvertTo-ProcessArgumentString -Arguments (Get-AgentServiceArguments -Definition $Definition -Service $Service)

        $childEnvironment = @{
            DEEPSEEK_API_KEY  = $plainText
            DEEPSEEK_BASE_URL = 'https://api.deepseek.com'
            DEEPSEEK_MODEL    = 'deepseek-chat'
        }
        $childEnvironment[$Definition.MockVariable] = '0'
        foreach ($entry in $childEnvironment.GetEnumerator()) {
            $startInfo.EnvironmentVariables[$entry.Key] = [string] $entry.Value
        }
        return $startInfo
    }
    finally {
        $plainText = $null
    }
}

function Start-AgentProcess {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject] $Definition,

        [Parameter(Mandatory = $true)]
        [ValidateSet('Api', 'Ui')]
        [string] $Service,

        [Parameter(Mandatory = $true)]
        [Security.SecureString] $DeepSeekKey,

        [switch] $HiddenWindows
    )

    [void] (Test-AgentDefinition -Definition $Definition)
    $startInfo = New-AgentProcessStartInfo -Definition $Definition -Service $Service -DeepSeekKey $DeepSeekKey -HiddenWindows:$HiddenWindows
    $process = New-Object Diagnostics.Process
    try {
        $process.StartInfo = $startInfo
        if (-not $process.Start()) {
            throw "Unable to start $($Definition.Name) $Service process."
        }
        return $process
    }
    catch {
        $process.Dispose()
        throw
    }
}

function Stop-CreatedProcesses {
    [CmdletBinding()]
    param(
        [Diagnostics.Process[]] $Processes
    )

    foreach ($process in @($Processes)) {
        if ($null -eq $process) {
            continue
        }
        try {
            if (-not $process.HasExited) {
                Stop-Process -Id $process.Id -Force -ErrorAction Stop
            }
        }
        catch {
            Write-Warning "Unable to stop process $($process.Id) after startup failure."
        }
    }
}

function Start-AgentProject {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name,

        [Parameter(Mandatory = $true)]
        [Security.SecureString] $DeepSeekKey,

        [switch] $NoBrowser,

        [switch] $HiddenWindows,

        [ValidateRange(1, 300)]
        [int] $TimeoutSeconds = 60
    )

    $definition = Get-AgentDefinition -Name $Name
    [void] (Test-AgentDefinition -Definition $definition)

    $apiPortInUse = Test-TcpPort -Port $definition.ApiPort
    $uiPortInUse = Test-TcpPort -Port $definition.UiPort
    $reuseApi = $apiPortInUse -and (Test-AgentApiReady -Definition $definition)
    $reuseUi = $uiPortInUse -and (Test-AgentUiReady -Definition $definition)

    if ($apiPortInUse -and -not $reuseApi) {
        throw "$($definition.Name) API port $($definition.ApiPort) is already in use by another or non-DeepSeek service."
    }
    if ($uiPortInUse -and -not $reuseUi) {
        throw "$($definition.Name) UI port $($definition.UiPort) is already in use by another service."
    }

    $created = New-Object Collections.Generic.List[Diagnostics.Process]
    try {
        if ($reuseApi) {
            Write-Host "$($definition.Name) API is already running on port $($definition.ApiPort)."
        }
        else {
            Write-Host "Starting $($definition.Name) API on port $($definition.ApiPort)..."
            $created.Add((Start-AgentProcess -Definition $definition -Service 'Api' -DeepSeekKey $DeepSeekKey -HiddenWindows:$HiddenWindows))
            if (-not (Wait-AgentApiReady -Definition $definition -TimeoutSeconds $TimeoutSeconds)) {
                throw "$($definition.Name) API did not become ready within $TimeoutSeconds seconds."
            }
        }

        if ($reuseUi) {
            Write-Host "$($definition.Name) UI is already running on port $($definition.UiPort)."
        }
        else {
            Write-Host "Starting $($definition.Name) UI on port $($definition.UiPort)..."
            $created.Add((Start-AgentProcess -Definition $definition -Service 'Ui' -DeepSeekKey $DeepSeekKey -HiddenWindows:$HiddenWindows))
            $uiUri = "http://127.0.0.1:$($definition.UiPort)/"
            if (-not (Wait-HttpReady -Uri $uiUri -TimeoutSeconds $TimeoutSeconds)) {
                throw "$($definition.Name) UI did not become ready within $TimeoutSeconds seconds."
            }
        }

        $uiUrl = "http://127.0.0.1:$($definition.UiPort)/"
        if (-not $NoBrowser) {
            [void] (Start-Process $uiUrl)
        }

        return [pscustomobject]@{
            Name = $definition.Name
            ApiUrl = "http://127.0.0.1:$($definition.ApiPort)/docs"
            UiUrl = $uiUrl
            CreatedPids = @($created | ForEach-Object Id)
            ReusedApi = $reuseApi
            ReusedUi = $reuseUi
        }
    }
    catch {
        Stop-CreatedProcesses -Processes $created.ToArray()
        throw
    }
}

function Start-SelectedProjects {
    [CmdletBinding()]
    param(
        [string[]] $Projects,

        [switch] $NoBrowser,

        [switch] $HiddenWindows,

        [ValidateRange(1, 300)]
        [int] $TimeoutSeconds = 60
    )

    $projectNames = @(Resolve-AgentProjectNames -Projects $Projects)
    $deepSeekKey = Get-DeepSeekKey
    $results = New-Object Collections.Generic.List[object]
    $errors = New-Object Collections.Generic.List[string]
    try {
        foreach ($projectName in $projectNames) {
            try {
                $result = Start-AgentProject -Name $projectName -DeepSeekKey $deepSeekKey -NoBrowser:$NoBrowser -HiddenWindows:$HiddenWindows -TimeoutSeconds $TimeoutSeconds
                $results.Add($result)
                Write-Host "$projectName is ready: $($result.UiUrl)"
            }
            catch {
                $errors.Add("$projectName`: $($_.Exception.Message)")
                Write-Error -Exception $_.Exception -Message "$projectName failed to start." -ErrorAction Continue
            }
        }
    }
    finally {
        $deepSeekKey.Dispose()
    }

    if ($errors.Count -gt 0) {
        throw "One or more projects failed to start: $($errors -join '; ')"
    }
    return $results.ToArray()
}

function Set-DeepSeekKeyInteractive {
    [CmdletBinding()]
    param()

    Write-Host 'Paste the shared DeepSeek Key. Input is hidden.'
    $secureKey = Read-Host -Prompt 'DeepSeek Key' -AsSecureString
    try {
        Save-DeepSeekKey -SecureString $secureKey
        Write-Host "DeepSeek Key was saved with Windows DPAPI at: $(Get-DeepSeekKeyPath)"
    }
    finally {
        $secureKey.Dispose()
    }
}

function Get-EvalEnvironmentNames {
    [CmdletBinding()]
    param()

    return @(
        'DEEPSEEK_API_KEY',
        'DEEPSEEK_BASE_URL',
        'DEEPSEEK_MODEL',
        'CITEGUARD_MOCK',
        'DATAPILOT_MOCK'
    )
}

function Test-EvalReportIsLive {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path,

        [Parameter(Mandatory = $true)]
        [string] $ProjectName
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$ProjectName did not write an evaluation report at $Path."
    }

    try {
        $reportText = [IO.File]::ReadAllText($Path, [Text.Encoding]::UTF8)
        $report = $reportText | ConvertFrom-Json
    }
    catch {
        throw "$ProjectName wrote an unreadable evaluation report at $Path."
    }

    $mode = $null
    if ($report.PSObject.Properties['mode']) {
        $mode = $report.mode
    }
    if ($mode -ne 'live') {
        throw "$ProjectName evaluation reported mode '$mode' instead of 'live'. The report was produced without a working DeepSeek Key."
    }

    return $true
}

function Invoke-AgentEval {
    [CmdletBinding()]
    param(
        [string[]] $Projects,

        [string] $ReportPath = 'evals\report.live.json'
    )

    $projectNames = @(Resolve-AgentProjectNames -Projects $Projects)
    $deepSeekKey = Get-DeepSeekKey

    $environmentNames = Get-EvalEnvironmentNames
    $savedEnvironment = @{}
    foreach ($name in $environmentNames) {
        $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, [EnvironmentVariableTarget]::Process)
    }

    $plainText = $null
    $results = New-Object Collections.Generic.List[object]
    $errors = New-Object Collections.Generic.List[string]
    try {
        $plainText = Convert-SecureStringToPlainText -SecureString $deepSeekKey
        if ([string]::IsNullOrWhiteSpace($plainText)) {
            throw 'DeepSeek Key cannot be empty.'
        }

        [Environment]::SetEnvironmentVariable('DEEPSEEK_API_KEY', $plainText, [EnvironmentVariableTarget]::Process)
        [Environment]::SetEnvironmentVariable('DEEPSEEK_BASE_URL', 'https://api.deepseek.com', [EnvironmentVariableTarget]::Process)
        [Environment]::SetEnvironmentVariable('DEEPSEEK_MODEL', 'deepseek-chat', [EnvironmentVariableTarget]::Process)
        [Environment]::SetEnvironmentVariable('CITEGUARD_MOCK', '0', [EnvironmentVariableTarget]::Process)
        [Environment]::SetEnvironmentVariable('DATAPILOT_MOCK', '0', [EnvironmentVariableTarget]::Process)

        foreach ($projectName in $projectNames) {
            $definition = Get-AgentDefinition -Name $projectName
            [void] (Test-AgentDefinition -Definition $definition)

            $evalScript = Join-Path $definition.Root 'evals\run_eval.py'
            if (-not (Test-Path -LiteralPath $evalScript -PathType Leaf)) {
                throw "$($definition.Name) evaluation script was not found: $evalScript"
            }

            $reportFile = Join-Path $definition.Root $ReportPath
            Write-Host "Running the $($definition.Name) live evaluation..."

            Push-Location -LiteralPath $definition.Root
            try {
                & $definition.Python $evalScript '--live' '--output' $reportFile
                $exitCode = $LASTEXITCODE
            }
            finally {
                Pop-Location
            }

            if ($exitCode -ne 0) {
                $errors.Add("$($definition.Name) evaluation exited with code $exitCode")
                continue
            }

            try {
                Test-EvalReportIsLive -Path $reportFile -ProjectName $definition.Name
            }
            catch {
                $errors.Add($_.Exception.Message)
                continue
            }

            $results.Add([pscustomobject]@{
                Name       = $definition.Name
                ReportPath = $reportFile
            })
            Write-Host "$($definition.Name) live report written to $reportFile"
        }
    }
    finally {
        foreach ($name in $environmentNames) {
            [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], [EnvironmentVariableTarget]::Process)
        }
        $plainText = $null
        $deepSeekKey.Dispose()
    }

    if ($errors.Count -gt 0) {
        throw "One or more live evaluations failed: $($errors -join '; ')"
    }
    return $results.ToArray()
}

function Invoke-DeepSeekLauncher {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('Configure', 'Start', 'Eval')]
        [string] $Mode,

        [string[]] $Projects,

        [switch] $NoBrowser,

        [switch] $HiddenWindows,

        [string] $EvalReport = 'evals\report.live.json',

        [int] $TimeoutSeconds = 60
    )

    if ($Mode -eq 'Configure') {
        Set-DeepSeekKeyInteractive
        return
    }

    if ($Mode -eq 'Eval') {
        [void] (Invoke-AgentEval -Projects $Projects -ReportPath $EvalReport)
        return
    }

    [void] (Start-SelectedProjects -Projects $Projects -NoBrowser:$NoBrowser -HiddenWindows:$HiddenWindows -TimeoutSeconds $TimeoutSeconds)
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        Invoke-DeepSeekLauncher -Mode $Mode -Projects $Projects -NoBrowser:$NoBrowser -HiddenWindows:$HiddenWindows -EvalReport $EvalReport -TimeoutSeconds $TimeoutSeconds
        exit 0
    }
    catch {
        Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }
}
