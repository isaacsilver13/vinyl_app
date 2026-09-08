[CmdletBinding()]
param(
    [ValidateSet("app", "api", "both")]
    [string]$Service = "app",
    [switch]$StatusOnly,
    [switch]$Deploy,
    [switch]$SkipHealth
)

$ErrorActionPreference = "Stop"

if ($StatusOnly -and $Deploy) {
    throw "Choose either -StatusOnly or -Deploy, not both."
}

$AppRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ApiRoot = (Resolve-Path (Join-Path $AppRoot "..\vinyl_api")).Path

$Targets = @{
    app = @{
        Name = "vinyl-catalog"
        Root = $AppRoot
        Config = Join-Path $AppRoot "fly.toml"
        HealthUrl = "https://vinyl-catalog.fly.dev/"
    }
    api = @{
        Name = "vinyl-api"
        Root = $ApiRoot
        Config = Join-Path $ApiRoot "fly.toml"
        HealthUrl = "https://vinyl-api.fly.dev/health"
    }
}

if (-not (Get-Command flyctl -ErrorAction SilentlyContinue)) {
    throw "flyctl was not found on PATH. Install it or run this wrapper in a Fly-enabled shell."
}

function Invoke-Fly {
    param(
        [string]$WorkingDirectory,
        [string[]]$Arguments
    )

    $previousGoDebug = $env:GODEBUG
    $env:GODEBUG = "http2client=0"
    Push-Location $WorkingDirectory
    try {
        & flyctl @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "flyctl $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
        if ($null -eq $previousGoDebug) {
            Remove-Item Env:GODEBUG -ErrorAction SilentlyContinue
        }
        else {
            $env:GODEBUG = $previousGoDebug
        }
    }
}

function Test-Health {
    param(
        [hashtable]$Target
    )

    try {
        $response = Invoke-WebRequest -Uri $Target.HealthUrl -Method Get -UseBasicParsing -TimeoutSec 30
        if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 400) {
            throw "HTTP status $($response.StatusCode)"
        }
        Write-Host "Health OK: $($Target.HealthUrl) [$($response.StatusCode)]"
    }
    catch {
        throw "Health check failed for $($Target.HealthUrl): $($_.Exception.Message)"
    }
}

$TargetNames = if ($Service -eq "both") { @("app", "api") } else { @($Service) }
foreach ($TargetName in $TargetNames) {
    $target = $Targets[$TargetName]
    if (-not (Test-Path $target.Config)) {
        throw "Fly config not found: $($target.Config)"
    }

    Write-Host "=== ${TargetName}: $($target.Name) ==="
    Invoke-Fly -WorkingDirectory $target.Root -Arguments @("status", "--app", $target.Name, "--config", $target.Config)

    if (-not $SkipHealth) {
        Test-Health -Target $target
    }

    if ($Deploy) {
        Write-Host "Deploying $($target.Name) with remote builder..."
        Invoke-Fly -WorkingDirectory $target.Root -Arguments @("deploy", "--remote-only", "--config", $target.Config)
        if (-not $SkipHealth) {
            Test-Health -Target $target
        }
    }
}

Write-Host "Vinyl $Service operation completed."
