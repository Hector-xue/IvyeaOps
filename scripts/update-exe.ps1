# IvyeaOps Windows x64 one-click updater.
#
# Safe update for GitHub Release ZIP installs:
#   1. stop the background server
#   2. download the latest IvyeaOps-Windows-x64.zip
#   3. copy new program files over the current folder
#   4. keep user data/config: data\, logs\, server\.env
#   5. restart IvyeaOpsServer.exe

param(
    [string]$DownloadUrl = "https://github.com/Hector-xue/IvyeaOps/releases/latest/download/IvyeaOps-Windows-x64.zip",
    [switch]$NonInteractive
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $RepoRoot

function Write-Info($msg) { Write-Host "[IvyeaOps] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[IvyeaOps] WARN: $msg" -ForegroundColor Yellow }
function Write-Fail($msg) {
    Write-Host "[IvyeaOps] ERROR: $msg" -ForegroundColor Red
    if (-not $NonInteractive -and $env:IVYEAOPS_NONINTERACTIVE -ne "1") {
        Read-Host "Press Enter to exit"
    }
    exit 1
}

function Stop-IvyeaOps {
    $StopScript = Join-Path $RepoRoot "scripts\stop-hidden.ps1"
    if (Test-Path $StopScript) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File $StopScript
        return
    }
    try {
        $conn = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($conn) { Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue }
    } catch {}
}

function Find-PackageRoot($ExtractDir) {
    $directExe = Join-Path $ExtractDir "IvyeaOpsServer.exe"
    if (Test-Path $directExe) { return $ExtractDir }

    $dirs = Get-ChildItem $ExtractDir -Directory
    foreach ($d in $dirs) {
        if (Test-Path (Join-Path $d.FullName "IvyeaOpsServer.exe")) { return $d.FullName }
    }
    return $null
}

Write-Host ""
Write-Host "=======================================================" -ForegroundColor Green
Write-Host "  IvyeaOps Windows x64 updater" -ForegroundColor Green
Write-Host "=======================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Keeps:   data\, logs\, server\.env"
Write-Host "  Updates: program files, frontend, scripts, docs"
Write-Host ""

$EnvFile = Join-Path $RepoRoot "server\.env"
$DataDir = Join-Path $RepoRoot "data"
if (-not (Test-Path $DataDir)) { New-Item -ItemType Directory -Force -Path $DataDir | Out-Null }

$TempRoot = Join-Path $env:TEMP ("IvyeaOpsUpdate-" + [Guid]::NewGuid().ToString("N"))
$ZipPath = Join-Path $TempRoot "IvyeaOps-Windows-x64.zip"
$ExtractDir = Join-Path $TempRoot "extract"
$EnvBackup = Join-Path $TempRoot "server.env.backup"

try {
    New-Item -ItemType Directory -Force -Path $TempRoot, $ExtractDir | Out-Null
    if (Test-Path $EnvFile) { Copy-Item $EnvFile $EnvBackup -Force }

    Write-Info "Stopping background service..."
    Stop-IvyeaOps

    Write-Info "Downloading latest Windows x64 package..."
    Invoke-WebRequest -Uri $DownloadUrl -OutFile $ZipPath -UseBasicParsing

    Write-Info "Extracting update package..."
    Expand-Archive -Path $ZipPath -DestinationPath $ExtractDir -Force
    $PackageRoot = Find-PackageRoot $ExtractDir
    if (-not $PackageRoot) { Write-Fail "Invalid update package: IvyeaOpsServer.exe not found." }

    Write-Info "Copying program files while keeping data and config..."
    $robocopyArgs = @(
        $PackageRoot,
        $RepoRoot,
        "/E",
        "/XD", "data", "logs", ".git",
        "/XF", ".env",
        "/R:2",
        "/W:1",
        "/NFL",
        "/NDL",
        "/NP"
    )
    & robocopy @robocopyArgs | Out-Host
    $rc = $LASTEXITCODE
    if ($rc -gt 7) { Write-Fail "File copy failed, robocopy exit code: $rc" }

    if ((Test-Path $EnvBackup) -and -not (Test-Path $EnvFile)) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $EnvFile) | Out-Null
        Copy-Item $EnvBackup $EnvFile -Force
    }

    $ServerExe = Join-Path $RepoRoot "IvyeaOpsServer.exe"
    if (-not (Test-Path $ServerExe)) { Write-Fail "IvyeaOpsServer.exe not found after update." }

    Write-Info "Starting IvyeaOps..."
    Start-Process -FilePath $ServerExe -WorkingDirectory $RepoRoot | Out-Null
    Write-Host ""
    Write-Info "Update complete. Data and config were preserved."
} catch {
    Write-Fail $_
} finally {
    try { Remove-Item -Recurse -Force $TempRoot -ErrorAction SilentlyContinue } catch {}
}
