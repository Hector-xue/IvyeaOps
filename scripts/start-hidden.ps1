# IvyeaOps hidden Windows launcher.
# Starts the FastAPI backend in the background, writes logs/PID, then opens the browser.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ServerDir = Join-Path $RepoRoot "server"
$DataDir = Join-Path $RepoRoot "data"
$LogsDir = Join-Path $RepoRoot "logs"
$PidFile = Join-Path $DataDir "ivyeaops.pid"
$OutLog = Join-Path $LogsDir "ivyeaops.out.log"
$ErrLog = Join-Path $LogsDir "ivyeaops.err.log"
$ServerExe = Join-Path $RepoRoot "IvyeaOpsServer.exe"
$VenvPy = Join-Path $ServerDir ".venv\Scripts\python.exe"
$Url = "http://127.0.0.1:8001"

function Test-IvyeaOpsRunning {
    try {
        $r = Invoke-WebRequest "$Url/api/health" -TimeoutSec 2 -UseBasicParsing
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Open-IvyeaOps {
    Start-Process $Url | Out-Null
}

if (-not (Test-Path $ServerExe) -and -not (Test-Path $VenvPy)) {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        "IvyeaOps 尚未安装完成。请先双击『安装 IvyeaOps.bat』，或下载 Windows x64 免 Python 版。",
        "IvyeaOps",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
    exit 1
}

New-Item -ItemType Directory -Force -Path $DataDir, $LogsDir | Out-Null

if (Test-IvyeaOpsRunning) {
    Open-IvyeaOps
    exit 0
}

# Drop stale PID files before starting a fresh backend.
if (Test-Path $PidFile) {
    try {
        $oldPid = [int](Get-Content $PidFile -Raw)
        $oldProc = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
        if (-not $oldProc) { Remove-Item $PidFile -Force -ErrorAction SilentlyContinue }
    } catch {
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }
}

# Start hidden: no console window stays in the taskbar. Logs are written to logs\.
if (Test-Path $ServerExe) {
    $proc = Start-Process `
        -FilePath $ServerExe `
        -WorkingDirectory $RepoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $OutLog `
        -RedirectStandardError $ErrLog `
        -PassThru
} else {
    $proc = Start-Process `
        -FilePath $VenvPy `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8001") `
        -WorkingDirectory $ServerDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput $OutLog `
        -RedirectStandardError $ErrLog `
        -PassThru
}

Set-Content -Path $PidFile -Value $proc.Id -Encoding ascii

# Wait briefly for startup; open the browser as soon as health is ready.
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    if (Test-IvyeaOpsRunning) {
        Open-IvyeaOps
        exit 0
    }
    if ($proc.HasExited) { break }
}

# If health did not answer yet but the process is still alive, open the page anyway;
# the browser will load once uvicorn finishes booting.
if (-not $proc.HasExited) {
    Open-IvyeaOps
    exit 0
}

Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.MessageBox]::Show(
    "IvyeaOps 启动失败，请查看 logs\ivyeaops.err.log。",
    "IvyeaOps",
    [System.Windows.Forms.MessageBoxButtons]::OK,
    [System.Windows.Forms.MessageBoxIcon]::Error
) | Out-Null
exit 1
