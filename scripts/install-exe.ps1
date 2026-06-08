# IvyeaOps Windows x64 免 Python 安装器
#
# 用于 GitHub Release 的 IvyeaOps-Windows-x64.zip：
#   1. 不安装 Python / Node
#   2. 生成 server\.env（随机密钥 + 管理员密码；密码留空则自动生成并显示）
#   3. 创建桌面快捷方式（后台启动，不常驻终端窗口）
#   4. 可选立即启动

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $RepoRoot

function Write-Info($msg) { Write-Host "[IvyeaOps] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[IvyeaOps] 注意: $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "[IvyeaOps] 错误: $msg" -ForegroundColor Red; Read-Host "按回车退出"; exit 1 }

$ServerExe = "$RepoRoot\IvyeaOpsServer.exe"
$Launcher = "$RepoRoot\启动 IvyeaOps (后台).vbs"
$Stopper = "$RepoRoot\停止 IvyeaOps.bat"
$EnvFile = "$RepoRoot\server\.env"

if (-not (Test-Path $ServerExe)) { Write-Fail "未找到 IvyeaOpsServer.exe。请确认下载的是 IvyeaOps-Windows-x64.zip。" }
if (-not (Test-Path "$RepoRoot\client\dist\index.html")) { Write-Fail "未找到 client\dist\index.html，发行包不完整。" }
if (-not (Test-Path "$RepoRoot\server")) { New-Item -ItemType Directory -Path "$RepoRoot\server" | Out-Null }
if (-not (Test-Path "$RepoRoot\data")) { New-Item -ItemType Directory -Path "$RepoRoot\data" | Out-Null }

if (Test-Path $EnvFile) {
    Write-Warn ".env 已存在 — 跳过生成（如需重置请删除后重跑）。"
} else {
    Write-Info "生成 server\.env..."
    $SecretBytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($SecretBytes)
    $Secret = [Convert]::ToBase64String($SecretBytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')

    Write-Host ""
    Write-Host "  设置网页管理员密码（直接回车 = 自动随机生成并显示）。"
    $SecurePw = Read-Host "  管理员密码" -AsSecureString
    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecurePw)
    try { $Pw = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr) } finally { [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    $Generated = $false
    if ([string]::IsNullOrWhiteSpace($Pw)) {
        $PwBytes = New-Object byte[] 12
        [System.Security.Cryptography.RandomNumberGenerator]::Fill($PwBytes)
        $Pw = [Convert]::ToBase64String($PwBytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
        $Generated = $true
    }

    @"
# 由 scripts\install-exe.ps1 生成，可按需修改。完整说明见 docs\CONFIG.md。
IVYEA_OPS_HOST=127.0.0.1
IVYEA_OPS_PORT=8001
IVYEA_OPS_DEV=0

# 会话签名密钥（保密，设好后不要改，否则所有人退出登录）
IVYEA_OPS_SECRET=$Secret

IVYEA_OPS_USER=admin
# Windows x64 免 Python 版不调用 bcrypt CLI；后端启动时会在内存中哈希此密码。
ADMIN_PASSWORD=$Pw

IVYEA_OPS_ALLOWED_ORIGINS=http://127.0.0.1:8001
"@ | Out-File -FilePath $EnvFile -Encoding utf8

    Write-Info "  server\.env 已创建。"
    if ($Generated) {
        Write-Host ""
        Write-Host "  ★ 已自动生成管理员密码：$Pw" -ForegroundColor Yellow
        Write-Host "    用户名 admin，请记下来；可在网页「系统配置 → 账号安全」里修改。" -ForegroundColor Yellow
    }
}

try {
    $Desktop = [Environment]::GetFolderPath("Desktop")
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut("$Desktop\IvyeaOps.lnk")
    $Shortcut.TargetPath = $Launcher
    $Shortcut.WorkingDirectory = $RepoRoot
    $Shortcut.Description = "启动 IvyeaOps 工作台"
    $ShortcutIcon = "$RepoRoot\client\public\favicon.ico"
    if (Test-Path $ShortcutIcon) { $Shortcut.IconLocation = $ShortcutIcon }
    $Shortcut.Save()
    Write-Info "  桌面快捷方式已创建：IvyeaOps"
} catch {
    Write-Warn "桌面快捷方式创建失败（不影响使用），可手动双击「启动 IvyeaOps (后台).vbs」。"
}

Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
Write-Host "  IvyeaOps Windows x64 安装完成！" -ForegroundColor Green
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
Write-Host ""
Write-Host "  以后双击桌面「IvyeaOps」即可后台启动，不会常驻终端窗口。"
Write-Host "  需要停止后台服务时，双击「停止 IvyeaOps.bat」。"
Write-Host "  如需排错，看 logs\ivyeaops.err.log / logs\ivyeaops.out.log。"
Write-Host ""
$go = Read-Host "现在就启动吗？(Y/n)"
if ($go -ne "n" -and $go -ne "N") {
    Start-Process -FilePath $Launcher
}
