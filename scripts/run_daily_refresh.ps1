param(
    [switch]$Force = $true,
    [switch]$NoAlerts = $false
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VinylDir = Resolve-Path (Join-Path $ScriptDir "..")

Push-Location $VinylDir
try {
    $argsList = @("refresh_all.py")
    if ($Force) { $argsList += "--force" }
    if ($NoAlerts) { $argsList += "--no-alerts" }

    Write-Host "Running daily refresh in $VinylDir ..."
    & python @argsList
    if ($LASTEXITCODE -ne 0) {
        throw "refresh_all.py exited with code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
