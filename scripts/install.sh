#!/usr/bin/env bash
# IvyeaOps one-shot install script for Linux
#
# What it does:
#   1. Checks Python 3.9+ and Node 18+ are available
#   2. Installs Python dependencies (pip)
#   3. Builds the React frontend (npm)
#   4. Generates server/.env with a random secret and an admin password hash
#   5. Prints next steps
#
# Usage:
#   bash scripts/install.sh
#
# To run as a non-root user, make sure you have write access to the repo
# directory and that pip/npm install directories are writable.

set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

# ── Colour helpers ────────────────────────────────────────────────────────────
GREEN="\033[32m"; YELLOW="\033[33m"; RED="\033[31m"; RESET="\033[0m"
info()  { echo -e "${GREEN}[IvyeaOps]${RESET} $*"; }
warn()  { echo -e "${YELLOW}[IvyeaOps]${RESET} $*"; }
die()   { echo -e "${RED}[IvyeaOps] ERROR${RESET} $*" >&2; exit 1; }

# ── 1. Prerequisite checks ────────────────────────────────────────────────────
info "Checking prerequisites..."

PYTHON=""
for bin in python3 python; do
  if command -v "$bin" &>/dev/null; then
    ver=$("$bin" -c "import sys; print(sys.version_info[:2])")
    major=$("$bin" -c "import sys; print(sys.version_info.major)")
    minor=$("$bin" -c "import sys; print(sys.version_info.minor)")
    if [ "$major" -gt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -ge 9 ]; }; then
      PYTHON="$bin"
      break
    fi
  fi
done
[ -n "$PYTHON" ] || die "Python 3.9+ is required. Install it with your package manager and re-run."

# 预构建发行包已自带 client/dist —— 此时无需 Node/npm 与前端构建。
PREBUILT=0
[ -f "$REPO_ROOT/client/dist/index.html" ] && PREBUILT=1

if [ "$PREBUILT" = 0 ]; then
  NODE=""
  if command -v node &>/dev/null; then
    node_major=$(node -e "process.stdout.write(String(process.versions.node.split('.')[0]))")
    if [ "$node_major" -ge 18 ]; then
      NODE="node"
    fi
  fi
  [ -n "$NODE" ] || die "Node.js 18+ is required. Download from https://nodejs.org/ and re-run."
  command -v npm &>/dev/null || die "npm not found. Ensure Node.js is properly installed."
fi

info "  Python: $($PYTHON --version)"
if [ "$PREBUILT" = 1 ]; then
  info "  检测到预构建前端 dist —— 跳过 Node 与前端构建。"
else
  info "  Node:   $(node --version)"
  info "  npm:    $(npm --version)"
fi

# ── 1.5 China mirror auto-detection ───────────────────────────────────────────
# pip (PyPI) and npm are slow/unreliable from mainland China. If google is
# unreachable we assume a mainland network and route both through fast domestic
# mirrors (Tsinghua PyPI + npmmirror). Override: IVYEA_CN=1 (force on) /
# IVYEA_CN=0 (force off). The exported env vars also speed up the optional
# Hermes/GBrain installers' own pip/npm steps.
PIP_MIRROR=""
NPM_MIRROR=""
_use_cn=""
case "${IVYEA_CN:-auto}" in
  1) _use_cn=1 ;;
  0) _use_cn="" ;;
  *) curl -fsS -o /dev/null -m 4 https://www.google.com 2>/dev/null || _use_cn=1 ;;
esac
if [ -n "$_use_cn" ]; then
  info "检测到国内网络 —— 启用清华 PyPI + 淘宝 npm 镜像加速（设 IVYEA_CN=0 可关闭）"
  PIP_MIRROR="-i https://pypi.tuna.tsinghua.edu.cn/simple"
  NPM_MIRROR="--registry=https://registry.npmmirror.com"
  export PIP_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
  export npm_config_registry="https://registry.npmmirror.com"
  # uv (used by the optional Hermes installer) honours these — speeds up its
  # Python dependency downloads too.
  export UV_DEFAULT_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"
  export UV_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
fi

# ── 2. Python dependencies (in an isolated venv) ──────────────────────────────
# A venv avoids polluting system Python and, crucially, sidesteps PEP 668
# ("externally-managed-environment") which makes `pip install` into the system
# interpreter fail outright on modern Debian/Ubuntu/Fedora.
info "Creating Python virtualenv (server/.venv)..."
cd "$REPO_ROOT/server"
VENV_DIR="$REPO_ROOT/server/.venv"
if [ ! -x "$VENV_DIR/bin/python" ]; then
  if ! $PYTHON -m venv "$VENV_DIR" 2>/dev/null; then
    die "Failed to create a virtualenv. On Debian/Ubuntu install it first:
       sudo apt install python3-venv
     then re-run this script."
  fi
fi
VENV_PY="$VENV_DIR/bin/python"

info "Installing Python dependencies..."
# shellcheck disable=SC2086  # $PIP_MIRROR is intentionally word-split (URL, no spaces)
"$VENV_PY" -m pip install -q $PIP_MIRROR --upgrade pip
"$VENV_PY" -m pip install -q $PIP_MIRROR -r requirements.txt
info "  Python deps installed into server/.venv."

# ── 3. Frontend build（预构建包已自带 dist 则整段跳过）───────────────────────────
if [ "$PREBUILT" = 0 ]; then
info "Building frontend..."
cd "$REPO_ROOT/client"

# The production bundle is large; vite/rollup peaks around 1.5–2 GB RAM. On small
# cloud servers the build gets OOM-killed *silently* (exit 137, no message). If
# RAM is tight and we're root, add a temporary swapfile so the build survives.
ram_mb=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)
swap_mb=$(awk '/SwapTotal/{print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)
if [ "${ram_mb:-0}" -lt 1900 ] && [ "${swap_mb:-0}" -lt 1024 ] && [ "$(id -u)" = 0 ] && [ ! -e /swapfile ]; then
  warn "内存偏小（${ram_mb}MB），前端构建可能 OOM —— 临时创建 2G swap..."
  if (fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 2>/dev/null) \
       && chmod 600 /swapfile && mkswap /swapfile >/dev/null 2>&1 && swapon /swapfile 2>/dev/null; then
    info "  已启用 2G swap（/swapfile；装完想移除：swapoff /swapfile && rm /swapfile）。"
  else
    warn "  swap 创建失败；若构建报 Killed 即为内存不足，请手动加 swap 或换 ≥2G 内存的机器。"
  fi
fi

# No --silent: a hidden npm failure here is exactly what makes the install look
# like it "just stopped". Show output so errors are visible.
# shellcheck disable=SC2086  # $NPM_MIRROR is intentionally word-split
# NODE_ENV=development 确保安装 devDependencies（vite/tsc）。不用 --include=dev flag：
# 该 flag 在部分 npm 版本上会触发 "Exit handler never called!" 崩溃，用环境变量等效更稳。
if ! NODE_ENV=development npm install --no-audit --no-fund $NPM_MIRROR; then
  die "npm install 失败（详见上方错误）。常见：npm 镜像/网络、磁盘空间不足。"
fi
# Cap node heap so the build is gentler on RAM (helps small servers + swap).
if ! NODE_OPTIONS="${NODE_OPTIONS:---max-old-space-size=1536}" npm run build; then
  die "前端构建失败。若上面显示 'Killed' 即为内存不足（OOM）：加 swap 或换 ≥2G 内存的机器后重试。"
fi
[ -f "$REPO_ROOT/client/dist/index.html" ] || die "构建未产出 client/dist/index.html，请检查上方报错。"
info "  Frontend built into client/dist."
else
  info "  使用预构建前端 client/dist（已跳过构建）。"
fi
cd "$REPO_ROOT"

# ── 4. Generate server/.env ──────────────────────────────────────────────────
cd "$REPO_ROOT/server"
ENV_FILE=".env"

if [ -f "$ENV_FILE" ]; then
  warn ".env already exists — skipping generation. Delete it and re-run to regenerate."
else
  info "Generating server/.env..."

  SECRET=$("$VENV_PY" -c "import secrets; print(secrets.token_urlsafe(32))")

  echo ""
  echo "  Set an admin password for the web UI (press Enter to auto-generate one)."
  # Hash directly with the venv's bcrypt and capture ONLY the bare hash.
  # (Do NOT pipe `python -m app.core.hashpw` here — that helper prints
  # human-facing instructions, which would corrupt .env.)
  read -rsp "  Admin password: " PW; echo ""
  GENERATED_PW=""
  if [ -z "$PW" ]; then
    PW=$("$VENV_PY" -c "import secrets; print(secrets.token_urlsafe(9))")
    GENERATED_PW="$PW"
  fi
  PW_HASH=$("$VENV_PY" -c "import bcrypt,sys; print(bcrypt.hashpw(sys.argv[1].encode(), bcrypt.gensalt()).decode())" "$PW")

  # Detect public hostname (best-effort)
  HOSTNAME_GUESS=""
  if command -v hostname &>/dev/null; then
    HOSTNAME_GUESS=$(hostname -f 2>/dev/null || hostname 2>/dev/null || true)
  fi

  cat > "$ENV_FILE" <<EOF
# Generated by scripts/install.sh — edit as needed.
# See docs/CONFIG.md for the full reference.

IVYEA_OPS_HOST=127.0.0.1
IVYEA_OPS_PORT=8001
IVYEA_OPS_DEV=0

# Session signing key (keep secret, do not change once set)
IVYEA_OPS_SECRET=${SECRET}

IVYEA_OPS_USER=admin
IVYEA_OPS_PASSWORD_HASH=${PW_HASH}

# Set this to your public URL (used for CSRF protection)
IVYEA_OPS_ALLOWED_ORIGINS=http://127.0.0.1:8001
# IVYEA_OPS_ALLOWED_ORIGINS=https://${HOSTNAME_GUESS:-ops.example.com}
EOF

  info "  server/.env created."
  if [ -n "$GENERATED_PW" ]; then
    echo ""
    warn "★ 已自动生成管理员密码：${GENERATED_PW}"
    warn "  用户名 admin，请记下来；可在网页「系统配置 → 账号安全」里修改。"
  fi
fi

# ── 5. Ensure data directory ──────────────────────────────────────────────────
cd "$REPO_ROOT"
mkdir -p data

# ── 5.5 Optional: local AI Agent (Hermes) + knowledge base (GBrain) ───────────
# Both are optional — IvyeaOps works with just the global fallback model. They
# unlock the local-tool / MCP-driven features. Official one-line installers.
echo ""
printf "  顺便安装本地 AI Agent (Hermes) + 知识库 (GBrain)？联网较慢，可跳过 (y/N) "
read -r ANS || ANS=""
if [ "$ANS" = "y" ] || [ "$ANS" = "Y" ]; then
  info "安装 Hermes Agent（官方安装器）..."
  curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash || \
    warn "Hermes 安装失败，可稍后手动重试：curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash"

  info "安装 Bun + GBrain..."
  if ! command -v bun &>/dev/null && [ ! -x "$HOME/.bun/bin/bun" ]; then
    curl -fsSL https://bun.sh/install | bash || warn "Bun 安装失败"
  fi
  BUN="$HOME/.bun/bin/bun"; [ -x "$BUN" ] || BUN="$(command -v bun || true)"
  if [ -n "$BUN" ] && [ -x "$BUN" ]; then
    "$BUN" install -g github:garrytan/gbrain || warn "GBrain 安装失败"
    GBRAIN="$HOME/.bun/bin/gbrain"
    if [ -x "$GBRAIN" ]; then
      mkdir -p "$HOME/brain"
      ( cd "$HOME/brain" && "$GBRAIN" init --pglite >/dev/null 2>&1 || true )
      info "  GBrain 安装完成（本地 PGLite，已初始化 ~/brain）。"
    fi
  else
    warn "未找到 bun，GBrain 跳过。可手动：bun install -g github:garrytan/gbrain"
  fi
  info "  安装路径会被 IvyeaOps 自动发现；如未识别，可在「系统配置 → 智能体」里填路径。"
fi

# ── 5.6 Listing 采集服务 (amazon-image-workflow, via Docker) ──────────────────
# Self-contained docker-compose (bundles Postgres). Free scraping (curl/puppeteer),
# no API keys. This is what pulls a listing's FULL main-image set (all 7 carousel
# images). Without it, IvyeaOps falls back to sorftime, which only returns a
# single (white-background) main image — so this is recommended-on, not optional.
if [ -f "$REPO_ROOT/amazon-image-workflow/docker-compose.yml" ]; then
  if command -v docker &>/dev/null && (docker compose version &>/dev/null || command -v docker-compose &>/dev/null); then
    echo ""
    info "Listing 采集服务（amazon-image-workflow）能抓取竞品的【完整主图组（全部 7 张）】。"
    info "不启用则只能经 sorftime 拿到 1 张白底主图。强烈建议启用。"
    printf "  启动 Listing 采集服务（Docker，免密钥，推荐）？(Y/n) "
    read -r ANS2 || ANS2=""
    if [ "$ANS2" != "n" ] && [ "$ANS2" != "N" ]; then
      info "启动采集服务（首次会构建镜像，较慢）..."
      if ( cd "$REPO_ROOT/amazon-image-workflow" && (docker compose up -d --build || docker-compose up -d --build) ); then
        info "  采集服务已启动（:3001）。IvyeaOps 默认已指向它，可采集完整主图组。"
      else
        warn "采集服务启动失败 —— Listing 采集将只能拿 1 张白底主图。"
        warn "  可稍后重试：cd amazon-image-workflow && docker compose up -d --build"
      fi
    else
      warn "已跳过采集服务 —— Listing 采集将只能经 sorftime 拿 1 张白底主图。"
      warn "  想拿完整主图组，随时可启用：cd amazon-image-workflow && docker compose up -d --build"
    fi
  else
    warn "未检测到 Docker —— Listing 采集服务需要 Docker，缺它则采集只能拿 1 张白底主图。"
    warn "  装上 Docker 后启用（即可采集完整 7 张主图组）："
    warn "  cd amazon-image-workflow && docker compose up -d --build"
    warn "（不装也行：Listing 其余功能照常，仅无法自动抓取完整竞品主图。）"
  fi
fi

# ── 6. Done ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${GREEN}  IvyeaOps install complete!${RESET}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""
echo "  Start the server:"
echo "    bash scripts/start.sh"
echo ""
echo "  Then open http://127.0.0.1:8001 in your browser."
echo "  A first-run wizard will guide you through agents + API keys."
echo ""
echo "  TIP: to work out of the box without a local agent CLI, set a"
echo "       「全局兜底大模型」 in 系统配置 (any OpenAI-compatible model + key)."
echo ""
echo "  For production deploy (nginx + systemd + certbot):"
echo "    See docs/INSTALL.md  (set PYTHON_BIN=server/.venv/bin/python)"
echo ""
