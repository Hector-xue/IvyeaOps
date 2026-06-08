# IvyeaOps optional component installer (Windows / PowerShell 5.1+)
#
# Components:
#   hermes  - official Hermes Agent installer
#   gbrain  - Bun + GBrain CLI + ~/brain initialization
#   all     - hermes + gbrain

param(
    [ValidateSet("all", "hermes", "gbrain", "status")]
    [string]$Component = "all"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Info($msg) { Write-Host "[IvyeaOps] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[IvyeaOps] 注意: $msg" -ForegroundColor Yellow }
function Test-Cmd($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }
function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $extras = @(
        "$env:USERPROFILE\.bun\bin",
        "$env:USERPROFILE\.hermes\bin",
        "$env:USERPROFILE\.local\bin"
    )
    $env:Path = (($extras + $machine + $user) -join ";")
}

function Show-Status {
    Refresh-Path
    $hermes = Get-Command hermes -ErrorAction SilentlyContinue
    $bun = Get-Command bun -ErrorAction SilentlyContinue
    $gbrain = Get-Command gbrain -ErrorAction SilentlyContinue
    Write-Host "Hermes: $(if ($hermes) { $hermes.Source } else { '未安装' })"
    Write-Host "Bun:    $(if ($bun) { $bun.Source } else { '未安装' })"
    Write-Host "GBrain: $(if ($gbrain) { $gbrain.Source } else { '未安装' })"
    Write-Host "Brain:  $env:USERPROFILE\brain"
}

function Install-Hermes {
    Refresh-Path
    if (Test-Cmd "hermes") {
        Write-Info "Hermes 已安装：$((Get-Command hermes).Source)"
        return
    }
    Write-Info "安装 Hermes Agent（官方 Windows 安装器）..."
    Invoke-Expression (Invoke-RestMethod "https://hermes-agent.nousresearch.com/install.ps1")
    Refresh-Path
    if (Test-Cmd "hermes") {
        Write-Info "Hermes 安装完成：$((Get-Command hermes).Source)"
    } else {
        Write-Warn "Hermes 安装器已运行，但当前会话暂未发现 hermes 命令。请重开 IvyeaOps 或重新检测。"
    }
}

function Install-GBrain {
    Refresh-Path
    if (-not (Test-Cmd "bun")) {
        Write-Info "安装 Bun（GBrain 需要）..."
        Invoke-Expression (Invoke-RestMethod "https://bun.sh/install.ps1")
        $env:Path = "$env:USERPROFILE\.bun\bin;" + $env:Path
        Refresh-Path
    } else {
        Write-Info "Bun 已安装：$((Get-Command bun).Source)"
    }

    $bun = Get-Command bun -ErrorAction SilentlyContinue
    if (-not $bun) {
        $fallback = "$env:USERPROFILE\.bun\bin\bun.exe"
        if (Test-Path $fallback) { $bun = Get-Item $fallback }
    }
    if (-not $bun) { throw "未找到 bun，无法安装 GBrain。" }

    Write-Info "安装/更新 GBrain..."
    & $bun.Source install -g github:garrytan/gbrain
    if ($LASTEXITCODE -ne 0) { throw "bun install -g github:garrytan/gbrain 失败。" }
    Refresh-Path

    $gbrain = Get-Command gbrain -ErrorAction SilentlyContinue
    if (-not $gbrain) {
        $fallback = "$env:USERPROFILE\.bun\bin\gbrain.exe"
        if (Test-Path $fallback) { $gbrain = Get-Item $fallback }
    }
    if (-not $gbrain) { throw "GBrain 安装后仍未找到 gbrain 命令。" }

    $brain = "$env:USERPROFILE\brain"
    if (-not (Test-Path $brain)) { New-Item -ItemType Directory -Path $brain | Out-Null }
    Push-Location $brain
    try { & $gbrain.Source init --pglite 2>$null } catch { Write-Warn "gbrain init 可稍后重试：$_" }
    Pop-Location
    Write-Info "GBrain 安装完成：$($gbrain.Source)"
    Write-Info "Brain Root：$brain"
}

if ($Component -eq "status") { Show-Status; exit 0 }
if ($Component -eq "all" -or $Component -eq "hermes") { Install-Hermes }
if ($Component -eq "all" -or $Component -eq "gbrain") { Install-GBrain }
Show-Status
