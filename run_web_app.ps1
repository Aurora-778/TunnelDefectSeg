param(
    [string]$HostAddress = '127.0.0.1',
    [int]$Port = 8000,
    [string]$PythonExe = 'python',
    [switch]$NoBrowser,
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Url = "http://${HostAddress}:$Port/"
$HealthUrl = "${Url}api/health"

function Test-WebAppHealth {
    param([string]$HealthEndpoint)
    try {
        $response = Invoke-WebRequest -Uri $HealthEndpoint -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Start-BrowserWhenReady {
    param(
        [string]$HealthEndpoint,
        [string]$AppUrl
    )

    Start-Job -ScriptBlock {
        param($HealthEndpoint, $AppUrl)
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Seconds 1
            try {
                $response = Invoke-WebRequest -Uri $HealthEndpoint -UseBasicParsing -TimeoutSec 2
                if ($response.StatusCode -eq 200) {
                    Start-Process $AppUrl
                    return
                }
            } catch {
            }
        }
        Start-Process $AppUrl
    } -ArgumentList $HealthEndpoint, $AppUrl | Out-Null
}

Set-Location $RepoRoot

Write-Host "Repo root: $RepoRoot"
Write-Host "Web URL:   $Url"
Write-Host "Python:    $PythonExe"
Write-Host ""

if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot 'web_app.py'))) {
    throw "web_app.py not found under repo root: $RepoRoot"
}

if ($CheckOnly) {
    Write-Host "Check only mode."
    Write-Host "Health endpoint available: $(Test-WebAppHealth $HealthUrl)"
    exit 0
}

if (Test-WebAppHealth $HealthUrl) {
    Write-Host "Web app is already running."
    if (-not $NoBrowser) {
        Start-Process $Url
    }
    exit 0
}

if (-not $NoBrowser) {
    Start-BrowserWhenReady -HealthEndpoint $HealthUrl -AppUrl $Url
}

Write-Host "Starting web app. Keep this window open while using the detector."
Write-Host "Press Ctrl+C to stop the server."
Write-Host ""

& $PythonExe 'web_app.py' --host $HostAddress --port $Port
exit $LASTEXITCODE
