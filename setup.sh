#!/bin/bash
set -e

# ═══════════════════════════════════════════════════
# IvyeaOps — Smart Setup Script
# Auto-detects & installs all dependencies
# ═══════════════════════════════════════════════════

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${CYAN}  $1${NC}"; }
ok()   { echo -e "${GREEN}  ✓ $1${NC}"; }
warn() { echo -e "${YELLOW}  ! $1${NC}"; }
err()  { echo -e "${RED}  ✗ $1${NC}"; }

echo ""
echo "  ╔═══════════════════════════════════════╗"
echo "  ║     IvyeaOps — Smart Installer         ║"
echo "  ╚═══════════════════════════════════════╝"
echo ""

# ── Detect OS ─────────────────────────────────────
detect_os() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        OS="macos"
        PKG_MGR="brew"
    elif [[ -f /etc/debian_version ]]; then
        OS="debian"
        PKG_MGR="apt"
    elif [[ -f /etc/redhat-release ]]; then
        OS="redhat"
        PKG_MGR="yum"
    elif [[ -f /etc/arch-release ]]; then
        OS="arch"
        PKG_MGR="pacman"
    else
        OS="unknown"
        PKG_MGR=""
    fi
    log "OS: $OS ($PKG_MGR)"
}

# ── Check if command exists ───────────────────────
has() { command -v "$1" &>/dev/null; }

# ── Install a package ─────────────────────────────
install_pkg() {
    local pkg="$1"
    log "Installing $pkg..."
    case $PKG_MGR in
        apt)    sudo apt-get update -qq && sudo apt-get install -y -qq "$pkg" ;;
        yum)    sudo yum install -y -q "$pkg" ;;
        pacman) sudo pacman -S --noconfirm --quiet "$pkg" ;;
        brew)   brew install "$pkg" ;;
        *)      err "Unknown package manager. Please install $pkg manually."; return 1 ;;
    esac
}

# ── Check & install a tool ────────────────────────
ensure_tool() {
    local cmd="$1"
    local pkg="${2:-$1}"
    local desc="$3"
    if has "$cmd"; then
        ok "$cmd found ($(command -v $cmd))"
    else
        warn "$cmd not found. Installing $pkg..."
        install_pkg "$pkg"
        if has "$cmd"; then
            ok "$cmd installed"
        else
            err "Failed to install $cmd. Please install manually: $desc"
            return 1
        fi
    fi
}

# ── Install Docker ────────────────────────────────
install_docker() {
    log "Installing Docker..."
    if [[ "$OS" == "macos" ]]; then
        if has brew; then
            brew install --cask docker
            warn "Docker Desktop installed. Please open Docker.app and start it."
            warn "Press Enter after Docker is running..."
            read -r
        else
            err "Please install Docker Desktop from https://docker.com/products/docker-desktop"
            exit 1
        fi
    else
        # Linux: use official install script
        curl -fsSL https://get.docker.com | sh
        sudo systemctl start docker
        sudo systemctl enable docker
        # Add current user to docker group
        if [[ "$EUID" -ne 0 ]]; then
            sudo usermod -aG docker "$USER"
            warn "Added $USER to docker group. You may need to log out and back in."
        fi
    fi
}

# ── Install Bun ──────────────────────────────────
install_bun() {
    log "Installing Bun..."
    curl -fsSL https://bun.sh/install | bash 2>/dev/null
    export PATH="$HOME/.bun/bin:$PATH"
    if has bun; then
        ok "Bun installed ($(bun --version))"
    else
        warn "Bun installed but not in PATH. Run: export PATH=\"\$HOME/.bun/bin:\$PATH\""
    fi
}

# ── Install Hermes ────────────────────────────────
install_hermes() {
    log "Installing Hermes Agent..."
    if has pip3; then
        PIP="pip3"
    elif has pip; then
        PIP="pip"
    else
        install_pkg python3-pip || install_pkg python-pip
        PIP="pip3"
    fi

    # Clone hermes-agent
    HERMES_DIR="$HOME/.hermes/hermes-agent"
    if [[ -d "$HERMES_DIR" ]]; then
        log "Hermes directory exists, updating..."
        cd "$HERMES_DIR" && git pull --quiet 2>/dev/null || true
    else
        log "Cloning hermes-agent..."
        mkdir -p "$HOME/.hermes"
        git clone --depth 1 https://github.com/nousresearch/hermes-agent.git "$HERMES_DIR" 2>/dev/null || \
        git clone --depth 1 https://github.com/Hector-xue/hermes-agent.git "$HERMES_DIR" 2>/dev/null || {
            err "Failed to clone hermes-agent. Please check your internet connection."
            return 1
        }
    fi

    # Create venv and install
    cd "$HERMES_DIR"
    if [[ ! -d "venv" ]]; then
        python3 -m venv venv
    fi
    source venv/bin/activate
    pip install -e . --quiet 2>/dev/null || pip install -e .
    deactivate

    # Create symlink
    mkdir -p "$HOME/.local/bin"
    ln -sf "$HERMES_DIR/venv/bin/hermes" "$HOME/.local/bin/hermes"

    # Add to PATH if needed
    if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
        export PATH="$HOME/.local/bin:$PATH"
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc" 2>/dev/null || true
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.zshrc" 2>/dev/null || true
    fi

    if has hermes; then
        ok "Hermes installed ($(hermes --version 2>/dev/null || echo 'unknown version'))"
    else
        warn "Hermes installed but not in PATH. Run: export PATH=\"\$HOME/.local/bin:\$PATH\""
    fi
}

# ═══════════════════════════════════════════════════
# Main Flow
# ═══════════════════════════════════════════════════

detect_os
echo ""

# ── Step 1: Check required tools ──────────────────
log "Step 1/5 — Checking required tools..."
echo ""

ensure_tool "git" "git" "https://git-scm.com"
ensure_tool "curl" "curl" "https://curl.se"

# Docker
if has docker; then
    ok "docker found ($(docker --version 2>/dev/null | head -1))"
else
    warn "Docker not found."
    read -p "  Install Docker? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        install_docker
    else
        err "Docker is required. Exiting."
        exit 1
    fi
fi

# Docker Compose
if docker compose version &>/dev/null 2>&1; then
    ok "docker compose found"
elif has docker-compose; then
    ok "docker-compose found (legacy)"
else
    warn "Docker Compose not found. Updating Docker..."
    if [[ "$OS" == "macos" ]]; then
        brew upgrade docker 2>/dev/null || true
    else
        # Linux: reinstall docker
        curl -fsSL https://get.docker.com | sh
    fi
fi

echo ""

# ── Step 2: Check Hermes (optional) ───────────────
log "Step 2/5 — Checking Hermes Agent..."
echo ""

if has hermes; then
    ok "Hermes found ($(hermes --version 2>/dev/null | head -1))"
else
    warn "Hermes not found."
    read -p "  Install Hermes? (recommended for full AI features) [Y/n] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        install_hermes
    else
        warn "Skipping Hermes. Some AI features will be limited."
    fi
fi

echo ""

echo ""

# ── Step 3: Clone repo ────────────────────────────
log "Step 3/5 — Getting IvyeaOps..."
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$SCRIPT_DIR"

if [[ -f "$REPO_DIR/docker-compose.yml" ]]; then
    ok "Already in IvyeaOps directory"
else
    log "Cloning IvyeaOps..."
    git clone https://github.com/Hector-xue/IvyeaOps.git "$HOME/ivyea-ops" 2>/dev/null || {
        err "Failed to clone IvyeaOps."
        exit 1
    }
    REPO_DIR="$HOME/ivyea-ops"
    cd "$REPO_DIR"
    ok "Cloned to $REPO_DIR"
fi

echo ""

# ── Step 4: Configure ─────────────────────────────
log "Step 4/5 — Configuring..."
echo ""

cd "$REPO_DIR"

if [[ ! -f .env ]]; then
    # Generate random password
    PASS=$(head -c 16 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 12)
    cat > .env << EOF
ADMIN_PASSWORD=${PASS}
PORT=8080
EOF
    ok "Created .env with random password"
    echo ""
    echo -e "  ${YELLOW}╔═══════════════════════════════════════╗${NC}"
    echo -e "  ${YELLOW}║  Your admin password: ${GREEN}${PASS}${YELLOW}  ║${NC}"
    echo -e "  ${YELLOW}║  Save this! You'll need it to login.  ║${NC}"
    echo -e "  ${YELLOW}╚═══════════════════════════════════════╝${NC}"
    echo ""
else
    ok ".env already exists"
fi

echo ""

# ── Step 5: Build & Start ─────────────────────────
log "Step 5/5 — Building & starting..."
echo ""

docker compose build --quiet
ok "Docker image built"

docker compose up -d
ok "Services started"

echo ""

# ── Wait for health check ─────────────────────────
log "Waiting for services to be ready..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:${PORT:-8080}/api/health > /dev/null 2>&1; then
        ok "Backend is healthy"
        break
    fi
    sleep 1
done

echo ""
echo "  ╔═══════════════════════════════════════╗"
echo "  ║                                       ║"
echo "  ║   IvyeaOps is ready!                   ║"
echo "  ║                                       ║"
echo "  ║   Open: http://localhost:${PORT:-8080}           ║"
echo "  ║                                       ║"
echo "  ╚═══════════════════════════════════════╝"
echo ""
echo "  Useful commands:"
echo "    docker compose logs -f    # View logs"
echo "    docker compose restart    # Restart"
echo "    docker compose down       # Stop"
echo ""
