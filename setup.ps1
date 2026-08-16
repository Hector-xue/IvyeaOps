# ═══════════════════════════════════════════════════
# IvyeaOps — Smart Installer for Windows
# Auto-detects & installs all dependencies
# ═══════════════════════════════════════════════════

function Write-Status($msg, $type="info") {
    $colors = @{ "ok"="Green"; "warn"="Yellow"; "err"="Red"; "info"="Cyan" }
    $prefix = @{ "ok"="  ✓"; "warn"="  !"; "err"="  ✗"; "info"="  " }
    Write-Host "$($prefix[$type]) $msg" -ForegroundColor $colors[$type]
}

function Test-Command($cmd) {
    return [bool](Get-Command $cmd -ErrorAction SilentlyContinue)
}

Write-Host ""
Write-Host "  ╔═══════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║     IvyeaOps — Smart Installer         ║" -ForegroundColor Cyan
Write-Host "  ╚═══════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── Step 1: Check Git ──
Write-Status "Step 1/5 — Checking required tools..." "info"
Write-Host ""

if (Test-Command "git") {
    Write-Status "git found" "ok"
} else {
    Write-Status "git not found. Installing..." "warn"
    if (Test-Command "winget") {
        winget install --id Git.Git -e --silent
    } elseif (Test-Command "choco") {
        choco install git -y
    } else {
        Write-Status "Please install Git from https://git-scm.com" "err"
        exit 1
    }
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
}

# ── Step 2: Check Docker ──
if (Test-Command "docker") {
    Write-Status "docker found" "ok"
} else {
    Write-Status "Docker not found." "warn"
    $install = Read-Host "  Install Docker Desktop? (y/n)"
    if ($install -eq "y") {
        Write-Status "Installing Docker Desktop..." "info"
        if (Test-Command "winget") {
            winget install --id Docker.DockerDesktop -e --silent
        } elseif (Test-Command "choco") {
            choco install docker-desktop -y
        } else {
            Write-Status "Please install Docker Desktop from https://docker.com/products/docker-desktop" "err"
            exit 1
        }
        Write-Status "Docker Desktop installed. Please restart your computer and start Docker." "warn"
        Write-Host "  Press Enter after Docker is running..." -ForegroundColor Yellow
        Read-Host
    } else {
        Write-Status "Docker is required. Exiting." "err"
        exit 1
    }
}

# Docker Compose check
try { docker compose version 2>&1 | Out-Null; Write-Status "docker compose found" "ok" } catch {
    Write-Status "Docker Compose not available. Please update Docker Desktop." "warn"
}

Write-Host ""

# ── Step 3: Check Hermes (optional) ──
Write-Status "Step 3/5 — Checking Hermes Agent..." "info"
Write-Host ""

if (Test-Command "hermes") {
    Write-Status "Hermes found" "ok"
} else {
    Write-Status "Hermes not found." "warn"
    $install = Read-Host "  Install Hermes? (recommended) (y/n)"
    if ($install -eq "y") {
        Write-Status "Installing Hermes Agent..." "info"
        $hermesDir = "$env:USERPROFILE\.hermes\hermes-agent"
        if (!(Test-Path $hermesDir)) {
            git clone --depth 1 https://github.com/nousresearch/hermes-agent.git $hermesDir 2>$null
            if (!$?) { git clone --depth 1 https://github.com/Hector-xue/hermes-agent.git $hermesDir }
        }
        Push-Location $hermesDir
        python -m venv venv
        .\venv\Scripts\activate
        pip install -e . --quiet
        deactivate
        Pop-Location
        Write-Status "Hermes installed" "ok"
    } else {
        Write-Status "Skipping Hermes." "warn"
    }
}

Write-Host ""

# ── Step 4: Clone repo ──
Write-Status "Step 4/5 — Getting IvyeaOps..." "info"
Write-Host ""

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoDir = $scriptDir

if (Test-Path "$repoDir\docker-compose.yml") {
    Write-Status "Already in IvyeaOps directory" "ok"
} else {
    Write-Status "Cloning IvyeaOps..." "info"
    $repoDir = "$env:USERPROFILE\ivyea-ops"
    git clone https://github.com/Hector-xue/IvyeaOps.git $repoDir
    Set-Location $repoDir
    Write-Status "Cloned to $repoDir" "ok"
}

Write-Host ""

# ── Step 5: Configure & Start ──
Write-Status "Step 5/5 — Configuring & starting..." "info"
Write-Host ""

Set-Location $repoDir

if (!(Test-Path ".env")) {
    $chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    $pass = -join (1..12 | ForEach-Object { $chars[(Get-Random -Maximum $chars.Length)] })
    # 两行写进 .env：ADMIN_PASSWORD 由 config 自动 bcrypt 成 hash；
    # PORT 给 docker-compose 用（"${PORT:-8080}:80"），不是给应用读的。
    "ADMIN_PASSWORD=$pass", "PORT=8080" | Set-Content .env
    Write-Status "Created .env" "ok"
    Write-Host ""
    Write-Host "  ╔═══════════════════════════════════════╗" -ForegroundColor Yellow
    Write-Host "  ║  Your admin password: $pass  ║" -ForegroundColor Green
    Write-Host "  ║  Save this! You'll need it to login.  ║" -ForegroundColor Yellow
    Write-Host "  ╚═══════════════════════════════════════╝" -ForegroundColor Yellow
    Write-Host ""
} else {
    Write-Status ".env already exists" "ok"
}

docker compose build --quiet
Write-Status "Docker image built" "ok"

docker compose up -d
Write-Status "Services started" "ok"

Write-Host ""

# ── Wait for health ──
Write-Status "Waiting for services..." "info"
$port = if ($env:PORT) { $env:PORT } else { "8080" }
for ($i = 0; $i -lt 30; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri "http://localhost:$port/api/health" -UseBasicParsing -TimeoutSec 2
        if ($resp.StatusCode -eq 200) {
            Write-Status "Backend is healthy" "ok"
            break
        }
    } catch {}
    Start-Sleep -Seconds 1
}

Write-Host ""
Write-Host "  ╔═══════════════════════════════════════╗" -ForegroundColor Green
Write-Host "  ║                                       ║" -ForegroundColor Green
Write-Host "  ║   IvyeaOps is ready!                   ║" -ForegroundColor Green
Write-Host "  ║                                       ║" -ForegroundColor Green
Write-Host "  ║   Open: http://localhost:$port           ║" -ForegroundColor Green
Write-Host "  ║                                       ║" -ForegroundColor Green
Write-Host "  ╚═══════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Host "  Useful commands:" -ForegroundColor Gray
Write-Host "    docker compose logs -f    # View logs" -ForegroundColor Gray
Write-Host "    docker compose restart    # Restart" -ForegroundColor Gray
Write-Host "    docker compose down       # Stop" -ForegroundColor Gray
Write-Host ""
