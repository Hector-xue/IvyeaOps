"""Application configuration loaded from environment variables (.env)."""
from __future__ import annotations

import os
import logging
import secrets
import sys
from pathlib import Path

from dotenv import load_dotenv


def _detect_root() -> Path:
    """Return the IvyeaOps runtime root for source and frozen exe builds."""
    explicit = os.getenv("IVYEA_OPS_ROOT", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    if getattr(sys, "frozen", False):
        # PyInstaller one-file/one-folder builds run from the packaged exe.
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]  # repo root


_ROOT = _detect_root()
load_dotenv(_ROOT / "server" / ".env")

# load_dotenv 把 .env 灌进了 os.environ —— 也就是说**每个子进程都能读到**
# 会话签名密钥和管理员密码哈希（终端里一条 env 命令就全看见了）。这里立刻把
# IvyeaOps 自己的凭据摘走存进内部字典；下面这些 os.getenv 在摘走**之前**执行，
# 读到的仍是正确的值。详见 core/secret_env。
from app.core import secret_env as _secret_env  # noqa: E402

logger = logging.getLogger("ivyea.core.config")


def _inherit_system_proxy() -> None:
    """On Windows, Clash/V2Ray often set only the WinINET *system* proxy (which
    browsers use) and NOT the HTTP_PROXY env vars. Python/httpx then connect via a
    raw socket that bypasses the proxy — so a host that is only reachable through
    the proxy (e.g. api.apimart.ai) times out, even though it opens instantly in
    the browser. Mirror the system proxy into the env so httpx uses the same path
    the browser does. No-op on Linux/macOS or when a proxy is already configured."""
    if os.name != "nt":
        return
    if any(os.environ.get(k) for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                                       "http_proxy", "https_proxy", "all_proxy")):
        return  # explicit config wins
    try:
        import urllib.parse
        import urllib.request
        proxies = urllib.request.getproxies()  # reads the WinINET registry on Windows
    except Exception:  # noqa: BLE001
        return
    raw = proxies.get("https") or proxies.get("http")
    if not raw:
        return
    p = urllib.parse.urlparse(raw if "://" in raw else "http://" + raw)
    if not p.hostname:
        return
    # The proxy is reached over plain http even for https targets (CONNECT tunnel).
    proxy = f"http://{p.hostname}:{p.port}" if p.port else f"http://{p.hostname}"
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ[var] = proxy


def _ensure_localhost_no_proxy() -> None:
    """Make every localhost HTTP call bypass a system/VPN proxy.

    On Windows/macOS with a proxy (Clash/V2Ray/corporate), httpx honours
    HTTP(S)_PROXY/ALL_PROXY and routes 127.0.0.1 (the embedded IvyeaAgent :8765,
    server-terminal, …) through the proxy, which returns 502 for
    localhost. urllib already skips it, which is why those calls silently worked
    while httpx-based ones (probes, agent panel synthesis) failed with 502.
    Augmenting NO_PROXY fixes httpx, requests and urllib at once; external hosts
    (DeepSeek, Sorftime, …) still use the proxy."""
    locals_ = ["127.0.0.1", "localhost", "::1", "0.0.0.0"]
    existing: list[str] = []
    for var in ("NO_PROXY", "no_proxy"):
        for h in (os.environ.get(var) or "").split(","):
            h = h.strip()
            if h and h not in existing:
                existing.append(h)
    merged = ",".join(existing + [h for h in locals_ if h not in existing])
    os.environ["NO_PROXY"] = merged
    os.environ["no_proxy"] = merged


_inherit_system_proxy()      # use the browser's system proxy (Windows/Clash)
_ensure_localhost_no_proxy()  # …but keep localhost direct


class Settings:
    # --- Runtime paths ---
    root_dir: Path = _ROOT

    # --- Networking ---
    host: str = os.getenv("IVYEA_OPS_HOST", "127.0.0.1")
    port: int = int(os.getenv("IVYEA_OPS_PORT", "8001"))
    dev_mode: bool = os.getenv("IVYEA_OPS_DEV", "0") == "1"

    # --- Security ---
    # On first run if IVYEA_OPS_SECRET is absent we generate an ephemeral one.
    # For production: set it in .env so sessions survive process restarts.
    secret_key: str = os.getenv("IVYEA_OPS_SECRET", "") or secrets.token_urlsafe(32)

    # A single user (personal hub). Username is arbitrary.
    admin_user: str = os.getenv("IVYEA_OPS_USER", "admin")
    # bcrypt hash, NOT plaintext. Generate with: python -m app.core.hashpw
    admin_password_hash: str = os.getenv("IVYEA_OPS_PASSWORD_HASH", "")

    def __init__(self):
        # Auto-hash plaintext ADMIN_PASSWORD if no hash is set
        if not self.admin_password_hash:
            plain = os.getenv("ADMIN_PASSWORD", "")
            if plain:
                try:
                    import bcrypt
                    self.admin_password_hash = bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()
                except Exception:
                    logger.debug("self.admin_password_hash = bcrypt.hashpw 失败（旁路，已忽略）", exc_info=True)

    session_cookie_name: str = "ivyea_ops_session"
    session_max_age_seconds: int = 60 * 60 * 24 * 7  # 7 days
    # Empty default = host-only cookie (safest). Set to e.g. ".example.com"
    # only if you want the session shared across subdomains via auth_request.
    cookie_domain: str = os.getenv("IVYEA_OPS_COOKIE_DOMAIN", "")

    # CSRF: comma-separated list of origins permitted to make state-changing
    # requests to /api/*. Requests whose Origin header is missing or not in
    # this list get rejected with 403. Safe methods (GET/HEAD/OPTIONS) are
    # exempt. Default covers the production host; override in .env for others.
    allowed_origins: list[str] = [
        o.strip()
        for o in os.getenv(
            "IVYEA_OPS_ALLOWED_ORIGINS",
            "",
        ).split(",")
        if o.strip()
    ]

    # --- Data ---
    data_dir: Path = Path(os.getenv("IVYEA_OPS_DATA_DIR", str(_ROOT / "data")))


settings = Settings()


# settings 的字段在类体里就已经从环境读完了（上面那些 os.getenv），
# 所以摘除放在这里：读取在前、摘除在后，两边都对。
_secret_env.harvest()
