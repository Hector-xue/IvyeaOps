# IvyeaOps 一键安装（Windows / PowerShell 5.1+）
#
# 做的事：
#   1. 自动检测 Python 3.9+ 和 Node 18+；缺失则用 winget 自动安装
#   2. 创建独立虚拟环境 server\.venv 并安装后端依赖
#   3. 构建前端
#   4. 生成 server\.env（随机密钥 + 管理员密码哈希；密码留空则自动生成并显示）
#   5. 创建桌面快捷方式（指向随仓库提供的「启动 IvyeaOps.bat」）
#   6. 可选：立即启动
#
# 用法：双击根目录的「安装 IvyeaOps.bat」，或：
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $RepoRoot

function Write-Info($msg) { Write-Host "[IvyeaOps] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[IvyeaOps] 注意: $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "[IvyeaOps] 错误: $msg" -ForegroundColor Red; Read-Host "按回车退出"; exit 1 }

function Test-Cmd($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

function Refresh-Path {
    # 把机器/用户 PATH 重新读进当前会话，让刚装好的工具立即可见。
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = ($machine, $user -join ";")
}

function Find-Python {
    foreach ($bin in @("python", "python3", "py")) {
        if (-not (Test-Cmd $bin)) { continue }
        try {
            $ver = & $bin --version 2>&1
            if ($ver -match "Python (\d+)\.(\d+)") {
                if ([int]$Matches[1] -gt 3 -or ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -ge 9)) { return $bin }
            }
        } catch {}
    }
    return $null
}

function Find-Node {
    if (-not (Test-Cmd "node")) { return $false }
    try {
        $v = & node --version 2>&1
        if ($v -match "v(\d+)" -and [int]$Matches[1] -ge 18) { return $true }
    } catch {}
    return $false
}

# 预构建发行包已自带 client\dist —— 此时完全不需要 Node/npm 与前端构建。
$HasPrebuilt = Test-Path "$RepoRoot\client\dist\index.html"

# ── 1. 自动检测 / 安装运行环境 ────────────────────────────────────────────────
Write-Info "检测运行环境..."
if ($HasPrebuilt) { Write-Info "  检测到预构建前端 dist —— 将跳过 Node 安装与前端构建。" }
$HasWinget = Test-Cmd "winget"

$Python = Find-Python
if (-not $Python) {
    if ($HasWinget) {
        Write-Warn "未检测到 Python 3.9+，正在用 winget 自动安装（约 1-2 分钟）..."
        winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
        Refresh-Path
        $Python = Find-Python
    }
    if (-not $Python) {
        Write-Fail "需要 Python 3.9+。请从 https://www.python.org/ 安装（勾选 Add to PATH），重开终端后重试。"
    }
}

if (-not $HasPrebuilt -and -not (Find-Node)) {
    if ($HasWinget) {
        Write-Warn "未检测到 Node.js 18+，正在用 winget 自动安装（约 1-2 分钟）..."
        winget install -e --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements
        Refresh-Path
    }
    if (-not (Find-Node)) {
        Write-Fail "需要 Node.js 18+。请从 https://nodejs.org/ 安装，重开终端后重试。"
    }
}

Write-Info "  Python: $(& $Python --version)"
if (-not $HasPrebuilt) { Write-Info "  Node:   $(& node --version)" }

# ── 1.5 国内镜像自动检测 ──────────────────────────────────────────────────────
# pip / npm 从中国大陆很慢。若 google 不可达则判定为大陆网络，pip/npm 走清华
# + 淘宝镜像。覆盖：环境变量 IVYEA_CN=1（强制开）/ IVYEA_CN=0（强制关）。
$PipMirror = @(); $NpmMirror = @()
$useCN = $false
if ($env:IVYEA_CN -eq "1") { $useCN = $true }
elseif ($env:IVYEA_CN -eq "0") { $useCN = $false }
else {
    try { Invoke-WebRequest -Uri "https://www.google.com" -TimeoutSec 4 -UseBasicParsing -ErrorAction Stop | Out-Null }
    catch { $useCN = $true }
}
if ($useCN) {
    Write-Info "检测到国内网络 —— 启用清华 PyPI + 淘宝 npm 镜像加速（设 IVYEA_CN=0 可关闭）"
    $PipMirror = @("-i", "https://pypi.tuna.tsinghua.edu.cn/simple")
    $env:PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"
    $NpmMirror = @("--registry=https://registry.npmmirror.com")
    $env:npm_config_registry = "https://registry.npmmirror.com"
    # uv (used by the optional Hermes installer) honours these.
    $env:UV_DEFAULT_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
    $env:UV_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"
}

# ── 2. 后端依赖（独立虚拟环境）────────────────────────────────────────────────
Write-Info "创建虚拟环境并安装后端依赖..."
$VenvPy = "$RepoRoot\server\.venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    & $Python -m venv "$RepoRoot\server\.venv"
}
if (-not (Test-Path $VenvPy)) { Write-Fail "创建虚拟环境失败。" }
& $VenvPy -m pip install -q @PipMirror --upgrade pip
& $VenvPy -m pip install -q @PipMirror -r "$RepoRoot\server\requirements.txt"
Write-Info "  后端依赖已装进 server\.venv。"

# ── 3. 前端构建（预构建包已自带 dist 则整段跳过）──────────────────────────────
if (-not $HasPrebuilt) {
Write-Info "构建前端..."
Set-Location "$RepoRoot\client"
# 关键修复：
#  · 不要 --silent —— 它会吞掉 npm 的安装/构建错误，正是“看起来装好了其实没构建”的根因。
#  · NODE_ENV=development —— 确保安装 devDependencies（vite/tsc）。不用 --include=dev flag：
#    该 flag 在部分 npm 版本上会触发 "Exit handler never called!" 崩溃（崩却假装成功、
#    留下残缺 node_modules）；用环境变量等效且更稳。
#  · 逐步检查退出码 + 最后校验 dist\index.html，绝不再谎报成功。
$env:NODE_ENV = "development"
& npm install --no-audit --no-fund @NpmMirror
if ($LASTEXITCODE -ne 0) { Set-Location $RepoRoot; Write-Fail "前端依赖安装失败（npm install）。常见：npm 镜像/网络不通、磁盘空间不足。修复后重跑安装。" }
& npm run build
if ($LASTEXITCODE -ne 0) { Set-Location $RepoRoot; Write-Fail "前端构建失败。若提示 'tsc/vite 不是内部或外部命令'，多为 devDependencies 未装全。请在 client 目录手动执行：npm install --include=dev 然后 npm run build" }
if (-not (Test-Path "$RepoRoot\client\dist\index.html")) { Set-Location $RepoRoot; Write-Fail "前端构建未产出 client\dist\index.html —— 详见上方报错。控制台首页会因此 404。" }
Set-Location $RepoRoot
Write-Info "  前端已构建到 client\dist。"
} else {
    Write-Info "  使用预构建前端 client\dist（已跳过 Node 与前端构建）。"
}

# ── 4. 生成 server\.env ───────────────────────────────────────────────────────
$EnvFile = "$RepoRoot\server\.env"
if (Test-Path $EnvFile) {
    Write-Warn ".env 已存在 — 跳过生成（如需重置请删除后重跑）。"
} else {
    Write-Info "生成 server\.env..."
    $Secret = & $VenvPy -c "import secrets; print(secrets.token_urlsafe(32))"

    Write-Host ""
    Write-Host "  设置网页管理员密码（直接回车 = 自动随机生成并显示）。"
    $SecurePw = Read-Host "  管理员密码" -AsSecureString
    $Pw = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecurePw))
    $Generated = $false
    if ([string]::IsNullOrWhiteSpace($Pw)) {
        $Pw = & $VenvPy -c "import secrets; print(secrets.token_urlsafe(9))"
        $Generated = $true
    }
    $PwHash = & $VenvPy -c "import bcrypt,sys; print(bcrypt.hashpw(sys.argv[1].encode(), bcrypt.gensalt()).decode())" $Pw

    @"
# 由 scripts\install.ps1 生成，可按需修改。完整说明见 docs\CONFIG.md。

IVYEA_OPS_HOST=127.0.0.1
IVYEA_OPS_PORT=8001
IVYEA_OPS_DEV=0

# 会话签名密钥（保密，设好后不要改，否则所有人退出登录）
IVYEA_OPS_SECRET=$Secret

IVYEA_OPS_USER=admin
IVYEA_OPS_PASSWORD_HASH=$PwHash

IVYEA_OPS_ALLOWED_ORIGINS=http://127.0.0.1:8001
"@ | Out-File -FilePath $EnvFile -Encoding utf8
    Write-Info "  server\.env 已创建。"
    if ($Generated) {
        Write-Host ""
        Write-Host "  ★ 已自动生成管理员密码：$Pw" -ForegroundColor Yellow
        Write-Host "    用户名 admin，请记下来；可在网页「系统配置 → 账号安全」里修改。" -ForegroundColor Yellow
    }
}

if (-not (Test-Path "$RepoRoot\data")) { New-Item -ItemType Directory -Path "$RepoRoot\data" | Out-Null }

# ── 4.5 可选：本地 AI Agent (Hermes) + 知识库 (GBrain) ────────────────────────
# 二者均可选；不装也能用（首启向导填「全局兜底大模型」即可）。装上后解锁
# 需要本地工具/MCP 的进阶功能。两个安装器都是官方一行命令、自带依赖。
Write-Host ""
$ai = Read-Host "顺便安装本地 AI Agent (Hermes) + 知识库 (GBrain)？联网较慢，可跳过 (y/N)"
if ($ai -eq "y" -or $ai -eq "Y") {
    Write-Info "安装 Hermes Agent（官方 Windows 安装器，自带 uv/Python/Node/git-bash）..."
    try {
        Invoke-Expression (Invoke-RestMethod "https://hermes-agent.nousresearch.com/install.ps1")
        Refresh-Path
        Write-Info "  Hermes 安装完成。"
    } catch {
        Write-Warn "Hermes 安装失败（可稍后手动重试：iex (irm https://hermes-agent.nousresearch.com/install.ps1)）：$_"
    }

    Write-Info "安装 Bun + GBrain..."
    try {
        if (-not (Test-Cmd "bun")) {
            Invoke-Expression (Invoke-RestMethod "https://bun.sh/install.ps1")
            $env:Path = "$env:USERPROFILE\.bun\bin;" + $env:Path
            Refresh-Path
        }
        $bun = if (Test-Cmd "bun") { "bun" } else { "$env:USERPROFILE\.bun\bin\bun.exe" }
        & $bun install -g github:garrytan/gbrain
        $gbrain = "$env:USERPROFILE\.bun\bin\gbrain.exe"
        if (Test-Path $gbrain) {
            $brain = "$env:USERPROFILE\brain"
            if (-not (Test-Path $brain)) { New-Item -ItemType Directory -Path $brain | Out-Null }
            Push-Location $brain
            try { & $gbrain init --pglite 2>$null } catch {}
            Pop-Location
            Write-Info "  GBrain 安装完成（本地 PGLite，已初始化 ~\brain）。"
        }
    } catch {
        Write-Warn "GBrain 安装失败（可稍后手动重试：bun install -g github:garrytan/gbrain）：$_"
    }
    Write-Host "  安装路径会被 IvyeaOps 自动发现；如未识别，可在「系统配置 → 智能体」里填路径。" -ForegroundColor Yellow
}

# ── 4.6 可选：Listing 采集服务 (amazon-image-workflow, 经 Docker) ─────────────
# 自包含 docker-compose（自带 Postgres）；免费抓取、零密钥。没 Docker 就跳过，
# Listing 其余功能照常（手填 + AI），仅无法自动抓竞品。
if (Test-Path "$RepoRoot\amazon-image-workflow\docker-compose.yml") {
    if (Test-Cmd "docker") {
        Write-Host ""
        $scrape = Read-Host "启动 Listing 采集服务（amazon-image-workflow，Docker，免密钥）？(y/N)"
        if ($scrape -eq "y" -or $scrape -eq "Y") {
            Write-Info "启动采集服务（首次构建镜像，较慢）..."
            Push-Location "$RepoRoot\amazon-image-workflow"
            try {
                & docker compose up -d --build
                Write-Info "  采集服务已启动（:3001）。IvyeaOps 默认已指向它。"
            } catch {
                Write-Warn "采集服务启动失败，可稍后手动：cd amazon-image-workflow; docker compose up -d --build"
            }
            Pop-Location
        }
    } else {
        Write-Warn "未检测到 Docker —— Listing 采集服务需要 Docker Desktop。装上后："
        Write-Warn "  cd amazon-image-workflow; docker compose up -d --build"
        Write-Warn "（不装也行：Listing 其余功能照常，仅无法自动抓竞品。）"
    }
}

# ── 5. 桌面快捷方式（指向随仓库提供的启动器）──────────────────────────────────
$Launcher = "$RepoRoot\启动 IvyeaOps.bat"
if (-not (Test-Path $Launcher)) {
    Write-Warn "未找到「启动 IvyeaOps.bat」，跳过快捷方式创建。"
}
if (Test-Path $Launcher) {
try {
    $Desktop = [Environment]::GetFolderPath("Desktop")
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut("$Desktop\IvyeaOps.lnk")
    $Shortcut.TargetPath = $Launcher
    $Shortcut.WorkingDirectory = $RepoRoot
    $Shortcut.Description = "启动 IvyeaOps 工作台"
    $ShortcutIcon = "$RepoRoot\client\public\favicon.ico"  # 圆角多尺寸 ICO，用于 Windows 桌面快捷方式
    if (Test-Path $ShortcutIcon) {
        $Shortcut.IconLocation = $ShortcutIcon
    }
    $Shortcut.Save()
    Write-Info "  桌面快捷方式已创建：IvyeaOps"
} catch {
    Write-Warn "桌面快捷方式创建失败（不影响使用），可手动双击「启动 IvyeaOps.bat」。"
}
}

# ── 6. 完成 / 可选立即启动 ────────────────────────────────────────────────────
Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
Write-Host "  IvyeaOps 安装完成！" -ForegroundColor Green
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
Write-Host ""
Write-Host "  以后双击桌面「IvyeaOps」或「启动 IvyeaOps.bat」即可启动（仅本机访问）。"
Write-Host "  想让同一局域网（同 Wi-Fi/路由器）下的其他电脑、手机也能用？"
Write-Host "    双击「启动 IvyeaOps (局域网共享).bat」——它会自动探测本机 IP、放行防火墙，"
Write-Host "    并在窗口里显示让别人访问的网址，对方浏览器打开即可，无需安装任何东西。" -ForegroundColor Yellow
Write-Host "  首次登录后会有向导，按提示填一个「全局兜底大模型」即可用全部 AI 功能。"
Write-Host "  注意：Windows 上终端(PTY)板块不可用，其余功能均正常。"
Write-Host ""
$go = Read-Host "现在就启动吗？(Y/n)"
if ($go -ne "n" -and $go -ne "N") {
    Start-Process -FilePath $Launcher
}
