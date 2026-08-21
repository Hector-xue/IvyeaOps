"""Server monitoring — real system metrics via psutil."""
from __future__ import annotations

import os
import logging
import subprocess
import sys
import time
from typing import List, Optional

_WINDOWS = sys.platform.startswith("win")

import psutil
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.security import require_user

router = APIRouter()

# Cached last net IO sample for rate calculation.
_net_state = {"ts": 0.0, "sent": 0, "recv": 0}

# Network interfaces to EXCLUDE from aggregation.
# lo/docker/veth/br 不是真实公网流量，腾讯云控制台只统计 eth0。
_NET_EXCLUDE_PREFIXES = ("lo", "docker", "veth", "br-", "virbr", "cni", "flannel", "tun", "tap", "warp", "utun")


class CpuInfo(BaseModel):
    percent: float
    count: int
    load_1m: float
    load_5m: float
    load_15m: float


class MemInfo(BaseModel):
    total: int
    used: int
    available: int
    percent: float            # Linux available-based (reflects real pressure)
    percent_used_raw: float   # (total-free)/total — matches 腾讯云控制台显示口径


class DiskInfo(BaseModel):
    total: int
    used: int
    free: int
    percent: float               # df/psutil: used / (used+avail), excludes reserved
    total_hardware: int          # raw disk capacity from /sys/block/* (matches 腾讯云)
    percent_hardware: float      # used / total_hardware (matches 腾讯云控制台)
    mount: str


class NetInfo(BaseModel):
    bytes_sent_total: int
    bytes_recv_total: int
    bytes_sent_rate: float  # bytes/sec
    bytes_recv_rate: float
    interface: str          # which iface(s) the numbers came from


class Snapshot(BaseModel):
    cpu: CpuInfo
    memory: MemInfo
    disk: DiskInfo
    network: NetInfo
    uptime_seconds: int


class ServiceStatus(BaseModel):
    name: str
    active: bool
    sub_state: Optional[str] = None
    description: str = ""
    category: str = "on-demand"
    impact: str = ""


def _cpu() -> CpuInfo:
    try:
        # POSIX-only: the function doesn't even exist on Windows (AttributeError),
        # which used to 500 the whole /stats endpoint there.
        l1, l5, l15 = os.getloadavg()
    except (OSError, AttributeError):
        l1 = l5 = l15 = 0.0
    # interval=0.2 blocks briefly to give an accurate sample (no more first-call 0%)
    return CpuInfo(
        percent=psutil.cpu_percent(interval=0.2),
        count=psutil.cpu_count(logical=True) or 0,
        load_1m=l1,
        load_5m=l5,
        load_15m=l15,
    )


def _memory() -> MemInfo:
    m = psutil.virtual_memory()
    # percent_used_raw = (total-free)/total, the same definition 腾讯云控制台 uses.
    # m.percent is the Linux "available-based" metric that accounts for reclaimable cache.
    pct_raw = 100.0 * (m.total - m.free) / m.total if m.total else 0.0
    return MemInfo(
        total=m.total,
        used=m.used,
        available=m.available,
        percent=m.percent,
        percent_used_raw=round(pct_raw, 1),
    )


def _default_mount() -> str:
    # "/" on POSIX; the current drive root (e.g. "C:\\") on Windows, where "/"
    # doesn't exist and psutil.disk_usage("/") would raise.
    return os.path.abspath(os.sep)


def _disk(mount: str | None = None) -> DiskInfo:
    mount = mount or _default_mount()
    d = psutil.disk_usage(mount)
    # Find the block device backing this mount, then read raw capacity from
    # /sys/block/<dev>/size (sectors × 512). This is what cloud consoles report.
    total_hw = d.total  # fallback = filesystem total
    try:
        # Resolve mount → device (e.g. /dev/vda1)
        with open("/proc/mounts", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == mount:
                    dev = parts[0]  # e.g. /dev/vda1
                    base = os.path.basename(dev)
                    # Strip trailing digits to find parent disk (vda1 → vda)
                    parent = base.rstrip("0123456789")
                    if parent and parent != base:
                        sys_path = f"/sys/block/{parent}/size"
                        if os.path.isfile(sys_path):
                            with open(sys_path, "r", encoding="utf-8") as sf:
                                sectors = int(sf.read().strip())
                                total_hw = sectors * 512
                    break
    except OSError:
        logger.debug("line.split 失败（旁路，已忽略）", exc_info=True)
    pct_hw = 100.0 * d.used / total_hw if total_hw else d.percent
    return DiskInfo(
        total=d.total,
        used=d.used,
        free=d.free,
        percent=d.percent,
        total_hardware=total_hw,
        percent_hardware=round(pct_hw, 1),
        mount=mount,
    )


def _network() -> NetInfo:
    # Only count real public-facing interfaces — exclude lo/docker/veth/br to
    # match 腾讯云控制台 (which only reports eth0 traffic).
    per = psutil.net_io_counters(pernic=True)
    kept: list[str] = []
    total_sent = 0
    total_recv = 0
    for name, c in per.items():
        if any(name.startswith(p) for p in _NET_EXCLUDE_PREFIXES):
            continue
        kept.append(name)
        total_sent += c.bytes_sent
        total_recv += c.bytes_recv
    iface_label = ",".join(sorted(kept)) or "none"
    now = time.time()
    prev_ts = _net_state["ts"]
    if prev_ts <= 0:
        sent_rate = 0.0
        recv_rate = 0.0
    else:
        dt = max(now - prev_ts, 0.001)
        sent_rate = max(0.0, (total_sent - _net_state["sent"]) / dt)
        recv_rate = max(0.0, (total_recv - _net_state["recv"]) / dt)
    _net_state.update(ts=now, sent=total_sent, recv=total_recv)
    return NetInfo(
        bytes_sent_total=total_sent,
        bytes_recv_total=total_recv,
        bytes_sent_rate=sent_rate,
        bytes_recv_rate=recv_rate,
        interface=iface_label,
    )


@router.get("/snapshot", response_model=Snapshot)
def snapshot(_user: str = Depends(require_user)) -> Snapshot:
    return Snapshot(
        cpu=_cpu(),
        memory=_memory(),
        disk=_disk(),
        network=_network(),
        uptime_seconds=int(time.time() - psutil.boot_time()),
    )


# Which systemd services to monitor. Tweak to taste.
_WATCHED_SERVICES = [
    "nginx",
    "IvyeaOps",
    "xray",
    "hysteria-server",
    "feishu-codex-relay",
    "warp-svc",
    "pm2-root",
    "hermes-dashboard",
    "agents-ui",
    "ttyd",
]

_SERVICE_CATALOG: dict[str, tuple[str, str, str]] = {
    "nginx": ("公网入口与 HTTPS 反向代理，承载 ops/term/cli 等域名转发", "critical", "停止后所有 Web 页面、API 与终端入口不可访问"),
    "IvyeaOps": ("当前运维控制台后端与静态页面服务", "critical", "停止后本控制台不可用"),
    "xray": ("Xray 代理服务，提供一组备用代理链路", "optional", "对应代理不可用，不影响 IvyeaOps 本身"),
    "hysteria-server": ("Hysteria 代理服务，高速 UDP 代理入口", "optional", "对应代理不可用，不影响 Web 控制台"),
    "feishu-codex-relay": ("飞书消息与 Codex/AI 会话中继服务", "on-demand", "飞书侧 AI 对话和转发停止"),
    "warp-svc": ("Cloudflare WARP 客户端，用于出站网络代理/绕路", "optional", "WARP 出站链路不可用，通常不影响核心服务"),
    "pm2-root": ("PM2 托管的 Node 应用进程管理器", "on-demand", "PM2 托管应用可能停止或无法自恢复"),
    "hermes-dashboard": ("Hermes 监控/仪表盘 Web 服务", "on-demand", "Hermes 仪表盘不可访问"),
    "agents-ui": ("Claude Code UI / Agents Web 界面", "on-demand", "Web AI 编码界面不可用"),
    "ttyd": ("Web 服务器终端服务，嵌入 /terminal 页面", "on-demand", "网页终端不可用，SSH 不受影响"),
    "agy": ("Antigravity AI 终端", "on-demand", "Antigravity CLI 功能停止"),
}


@router.get("/services", response_model=List[ServiceStatus])
def services(_user: str = Depends(require_user)) -> List[ServiceStatus]:
    out: List[ServiceStatus] = []
    # systemd is Linux-only — on Windows return empty instead of a list of
    # bogus "error" rows, so the panel shows "无" rather than all-red.
    if _WINDOWS:
        return out
    for name in _WATCHED_SERVICES:
        try:
            from app.core import proc as _proc
            r = _proc.run(   # audit=False：面板每次刷新都会跑，记了只会淹掉真正的操作
                ["systemctl", "is-active", f"{name}.service"], audit=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            state = r.stdout.strip() or "unknown"
        except Exception:
            state = "error"
        description, category, impact = _SERVICE_CATALOG.get(
            name,
            ("系统服务", "on-demand", "影响未登记，操作前请确认依赖关系"),
        )
        out.append(
            ServiceStatus(
                name=name,
                active=state == "active",
                sub_state=state,
                description=description,
                category=category,
                impact=impact,
            )
        )
    return out


@router.get("/logs")
def logs(_user: str = Depends(require_user), n: int = 20) -> dict:
    """Tail nginx access log (most recent N lines)."""
    n = max(1, min(200, n))
    if _WINDOWS:
        return {"lines": [], "note": "nginx 访问日志为 Linux 部署专用，Windows 不适用。"}
    try:
        from app.core import proc as _proc
        r = _proc.run(   # audit=False：同上，只读且高频
            ["tail", "-n", str(n), "/var/log/nginx/access.log"], audit=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        return {"lines": r.stdout.splitlines()}
    except Exception as e:
        return {"lines": [], "error": str(e)}


# ─── Process Management ───────────────────────────────────────────────────────

# Category: "critical" = must run, "on-demand" = only when needed, "optional" = safe to close
# Each entry: (description, category, impact_if_stopped)
_PROC_CATALOG: dict[str, tuple[str, str, str]] = {
    # ── 必须运行 ──
    "systemd": ("系统初始化守护进程", "critical", "系统崩溃"),
    "sshd": ("SSH远程登录服务", "critical", "无法远程连接服务器"),
    "nginx": ("Web反向代理服务器", "critical", "所有网站和API不可访问"),
    "crond": ("定时任务调度器", "critical", "所有cron定时任务停止执行"),
    "rsyslogd": ("系统日志服务", "critical", "无法记录系统日志"),
    "NetworkManager": ("网络管理服务", "critical", "网络断开"),
    "systemd-journald": ("Systemd日志服务", "critical", "journalctl无法使用"),
    "systemd-logind": ("用户登录管理", "critical", "无法登录"),
    "systemd-udevd": ("设备管理守护进程", "critical", "设备无法识别"),
    "dbus-broker": ("进程间通信总线", "critical", "系统服务间通信中断"),
    "agetty": ("终端登录管理", "critical", "控制台无法登录"),
    "chronyd": ("NTP时间同步", "critical", "系统时间不准确"),
    "iscsid": ("iSCSI存储服务", "critical", "云盘可能断开"),
    "IvyeaOps": ("运维控制台后端", "critical", "当前管理面板不可用"),
    # ── 按需运行 ──
    "hermes": ("Hermes AI助手主进程", "on-demand", "AI对话/飞书机器人停止，不影响网站"),
    "agy": ("Antigravity AI终端", "on-demand", "Antigravity CLI 操作停止"),
    "python": ("Hermes/IvyeaOps Python进程", "on-demand", "对应服务停止"),
    "tsserver": ("TypeScript语言服务器", "on-demand", "代码补全/检查停止，省~150MB/个"),
    "pyright": ("Python语言服务器", "on-demand", "Python代码检查停止，省~60MB"),
    "lark-cli": ("飞书CLI(消息转发)", "on-demand", "飞书消息转发停止"),
    "ttyd": ("Web终端服务", "on-demand", "网页终端不可用，SSH不受影响"),
    "kiro-gateway": ("Kiro Gateway API代理", "on-demand", "本地AI API代理不可用"),
    "feishu-codex-relay": ("飞书Codex中继", "on-demand", "飞书AI对话停止"),
    "agents-ui": ("Claude Code UI", "on-demand", "Web版Claude Code不可用"),
    "postgresql": ("PostgreSQL数据库", "on-demand", "依赖它的应用不可用"),
    "imgflow": ("图片工作流(PM2)", "on-demand", "Amazon图片处理不可用"),
    "next-server": ("imgflow前端(Next.js)", "on-demand", "图片工作流前端不可用"),
    "pm2": ("PM2进程管理器", "on-demand", "PM2管理的应用全部停止"),
    # ── 可关闭(省内存) ──
    "warp-svc": ("Cloudflare WARP VPN", "optional", "WARP代理不可用，其他代理不受影响。省~199MB"),
    "YDService": ("腾讯云主机安全(云镜)", "optional", "失去入侵检测，安全风险低。省~61MB"),
    "YDLive": ("腾讯云安全实时防护", "optional", "失去实时防护。省~9MB"),
    "barad_agent": ("腾讯云监控Agent", "optional", "腾讯云控制台看不到监控数据。省~25MB"),
    "sgagent": ("腾讯云星网Agent", "optional", "腾讯云内部通信停止。省~3MB"),
    "tat_agent": ("腾讯云自动化助手", "optional", "无法从控制台远程执行命令。省~7MB"),
    "xray": ("Xray代理服务", "optional", "Xray协议代理不可用，Hysteria不受影响"),
    "hysteria": ("Hysteria代理服务", "optional", "Hysteria协议代理不可用，Xray不受影响"),
    "kiro-cli": ("Kiro CLI AI助手", "optional", "当前AI会话结束"),
    "bun": ("Bun运行时", "optional", "Bun 脚本/服务停止"),
    "upower": ("电源管理守护进程", "optional", "服务器不需要电源管理。省~2MB"),
    "rtkit-daemon": ("实时调度策略服务", "optional", "音频优先级调度停止，服务器无影响"),
    "gssproxy": ("GSSAPI代理", "optional", "Kerberos认证停止，通常不需要"),
    "auditd": ("安全审计服务", "optional", "停止安全审计日志，省少量内存"),
}

# Processes that must NEVER be killed.
_PROTECTED_PROCS = {"systemd", "init", "sshd", "kthreadd", "kworker", "ksoftirqd",
                    "migration", "rcu_sched", "rcu_bh", "watchdog"}


def _match_catalog(name: str, cmdline: str) -> tuple[str, str, str]:
    """Match process to catalog entry. Returns (desc, category, impact)."""
    # Exact match
    if name in _PROC_CATALOG:
        return _PROC_CATALOG[name]
    # Match by cmdline keywords
    cmd_lower = cmdline.lower()
    for key, val in _PROC_CATALOG.items():
        if key.lower() in cmd_lower:
            return val
    # Defaults by name pattern
    if name in _PROTECTED_PROCS or name.startswith(("systemd", "kworker")):
        return ("系统内核/守护进程", "critical", "系统不稳定")
    return ("系统进程", "critical", "未知影响，建议不要关闭")


# Cmdline-based identification for processes that share the same binary name.
# Each entry: (cmdline_keyword, display_name, description, category, impact)
_CMDLINE_IDENTIFY: list[tuple[str, str, str, str, str]] = [
    ("tsserver.js --useInferredProject", "tsserver(全量)", "TypeScript全量语言服务", "on-demand", "代码补全停止，省~208MB"),
    ("tsserver.js --serverMode", "tsserver(轻量)", "TypeScript部分语义检查", "on-demand", "代码检查停止，省~54MB"),
    ("typingsInstaller.js", "TS类型安装器", "TypeScript类型自动下载", "on-demand", "类型安装停止，省~21MB"),
    ("typescript-language-server", "TS-LSP入口", "TypeScript语言服务入口", "on-demand", "TS补全停止，省~18MB"),
    ("pyright-langserver", "Pyright-LSP", "Python语言服务(代码检查)", "on-demand", "Python补全停止，省~60MB"),
    ("hermes_cli.main gateway", "Hermes网关", "Hermes AI请求网关", "on-demand", "AI对话停止"),
    ("hermes dashboard", "Hermes仪表盘", "Hermes Web仪表盘", "on-demand", "仪表盘不可用，省~121MB"),
    # Substring match against `ps` cmdline; "/bin/hermes" catches both
    # /usr/local/bin/hermes and ~/.local/bin/hermes without needing config.
    ("/bin/hermes", "Hermes主进程", "Hermes AI助手核心", "on-demand", "所有Hermes功能停止"),
    ("/bin/agy", "Antigravity", "Antigravity AI助手", "on-demand", "Antigravity功能停止"),
    ("feishu-codex-relay/relay.js", "飞书中继(node)", "飞书消息转发服务", "on-demand", "飞书AI对话停止"),
    ("dist-server/server/index.js", "Claude Code UI", "Web版Claude Code界面", "on-demand", "Web AI界面不可用"),
    ("amazon-image-workflow/backend", "imgflow后端", "Amazon图片工作流API", "on-demand", "图片处理不可用"),
    ("lark-cli", "飞书CLI", "飞书命令行工具", "on-demand", "飞书消息转发停止"),
    ("web-terminal/server.cjs", "Web终端插件", "Claude Code UI终端", "on-demand", "Web终端不可用"),
    ("main.py --port", "Kiro Gateway", "本地AI API代理", "on-demand", "AI API代理不可用"),
    ("uvicorn", "IvyeaOps后端", "运维面板API服务", "critical", "当前管理面板不可用"),
    ("next-server", "imgflow前端", "图片工作流Next.js前端", "on-demand", "图片工作流前端不可用"),
    ("kiro-cli-chat acp", "Kiro(AI引擎)", "Kiro CLI AI推理进程", "optional", "当前AI会话结束"),
    ("kiro-cli-chat chat", "Kiro(会话)", "Kiro CLI 会话管理", "optional", "当前AI会话结束"),
    ("kiro-cli/bun", "Kiro(TUI)", "Kiro CLI 终端界面", "optional", "Kiro界面关闭"),
]


class ProcessInfo(BaseModel):
    pid: int
    name: str
    status: str
    cpu_percent: float
    memory_percent: float
    memory_mb: float
    cpu_time: float
    description: str
    category: str       # critical / on-demand / optional
    impact: str         # what happens if stopped
    can_stop: bool
    username: str
    service: Optional[str] = None  # systemd service name if applicable


# Map known process names/cmdline patterns to their systemd service.
_PROC_TO_SERVICE: dict[str, str] = {
    "warp-svc": "warp-svc",
    "nginx": "nginx",
    "hysteria": "hysteria-server",
    "xray": "xray",
    "ttyd": "ttyd",
    "YDService": "YDService",
    "YDLive": "YDLive",
    "barad_agent": "barad_agent",
    "sgagent": "sgagent",
    "tat_agent": "tat_agent",
    "postmaster": "postgresql",
    "hermes dashboard": "hermes-dashboard",
    "agents-ui": "agents-ui",
    "feishu-codex-relay": "feishu-codex-relay",
    "kiro-gateway": "kiro-gateway",
}


@router.get("/processes", response_model=List[ProcessInfo])
def get_processes(_user: str = Depends(require_user)) -> List[ProcessInfo]:
    """List all system processes with category/impact info."""
    procs: List[ProcessInfo] = []
    for p in psutil.process_iter(["pid", "name", "status", "cpu_percent",
                                   "memory_percent", "cpu_times", "username",
                                   "memory_info", "cmdline"]):
        try:
            info = p.info
            name = info["name"] or ""
            pid = info["pid"]
            if pid == 0 or name.startswith("["):
                continue
            cpu_t = info.get("cpu_times")
            cpu_time = (cpu_t.user + cpu_t.system) if cpu_t else 0.0
            cmdline = " ".join(info.get("cmdline") or [])
            mem_info = info.get("memory_info")
            mem_mb = round((mem_info.rss / 1024 / 1024), 1) if mem_info else 0.0

            # Try cmdline-based identification first (more specific)
            display_name = name
            desc, category, impact = "", "", ""
            for keyword, dname, ddesc, dcat, dimpact in _CMDLINE_IDENTIFY:
                if keyword in cmdline:
                    display_name = dname
                    desc, category, impact = ddesc, dcat, dimpact
                    break

            if not desc:
                # Fall back to catalog
                if name in _PROC_CATALOG:
                    desc, category, impact = _PROC_CATALOG[name]
                else:
                    # Match by cmdline keywords in catalog
                    for key, val in _PROC_CATALOG.items():
                        if key.lower() in cmdline.lower():
                            desc, category, impact = val
                            break
                    if not desc:
                        if name in _PROTECTED_PROCS or name.startswith(("systemd", "kworker")):
                            desc, category, impact = "系统内核/守护进程", "critical", "系统不稳定"
                        else:
                            desc, category, impact = "系统进程", "critical", "未知影响，建议不要关闭"

            can_stop = (pid != 1 and category != "critical" and
                        name not in _PROTECTED_PROCS)

            # Determine associated systemd service
            service = None
            for key, svc in _PROC_TO_SERVICE.items():
                if key in name or key in cmdline:
                    service = svc
                    break

            procs.append(ProcessInfo(
                pid=pid,
                name=display_name,
                status=info.get("status", "unknown") or "unknown",
                cpu_percent=info.get("cpu_percent", 0.0) or 0.0,
                memory_percent=round(info.get("memory_percent", 0.0) or 0.0, 1),
                memory_mb=mem_mb,
                cpu_time=round(cpu_time, 1),
                description=desc,
                category=category,
                impact=impact,
                can_stop=can_stop,
                username=info.get("username", "") or "",
                service=service,
            ))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    # Sort: category priority (optional first for easy action), then by memory desc
    cat_order = {"optional": 0, "on-demand": 1, "critical": 2}
    running_states = {"running", "sleeping", "disk-sleep"}
    procs.sort(key=lambda p: (
        0 if p.status in running_states else 1,
        cat_order.get(p.category, 2),
        -p.memory_mb,
    ))
    return procs


class ProcessAction(BaseModel):
    pid: Optional[int] = None
    service: Optional[str] = None


@router.post("/processes/stop")
def stop_process(body: ProcessAction, _user: str = Depends(require_user)) -> dict:
    """Stop a process by PID or a systemd service by name."""
    if body.service:
        try:
            from app.core import proc as _proc
            r = _proc.run(
                ["systemctl", "stop", f"{body.service}.service"],
                capture_output=True, text=True, timeout=10,
                audit_module="server", audit_action="service.stop",
            )
            if r.returncode != 0:
                return {"ok": False, "error": r.stderr.strip() or "stop failed"}
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    if body.pid:
        try:
            proc = psutil.Process(body.pid)
            name = proc.name()
            if name in _PROTECTED_PROCS or body.pid == 1:
                return {"ok": False, "error": "不允许停止关键系统进程"}
            proc.terminate()
            return {"ok": True}
        except psutil.NoSuchProcess:
            return {"ok": False, "error": "进程不存在"}
        except psutil.AccessDenied:
            return {"ok": False, "error": "权限不足"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": False, "error": "需要提供 pid 或 service"}


@router.post("/processes/start")
def start_process(body: ProcessAction, _user: str = Depends(require_user)) -> dict:
    """Start a systemd service by name."""
    if not body.service:
        return {"ok": False, "error": "只能启动 systemd 服务，需提供 service 名称"}
    try:
        r = subprocess.run(
            ["systemctl", "start", f"{body.service}.service"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return {"ok": False, "error": r.stderr.strip() or "start failed"}
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─── Token Usage Statistics ───────────────────────────────────────────────────

import sqlite3 as _sqlite3
from pathlib import Path as _Path
from datetime import datetime as _datetime, timedelta as _timedelta, timezone as _timezone

logger = logging.getLogger("ivyea.routers.monitor")

# Token-usage DB / session paths are read from hub_settings (External
# Integrations). Each helper returns a Path that *might* exist; callers
# guard with .exists(). Wrapped in functions so a hub_settings change
# takes effect on the next request without a restart.
def _hermes_db() -> _Path | None:
    from app.core import integrations
    return integrations.hermes_db()

def _kiro_gw_db() -> _Path | None:
    from app.core import integrations
    return integrations.kiro_gateway_db()

def _codex_db() -> _Path | None:
    from app.core import integrations
    return integrations.codex_db()

def _feishu_codex_db() -> _Path | None:
    from app.core import integrations
    return integrations.feishu_codex_db()

def _kiro_cli_db() -> _Path | None:
    from app.core import integrations
    return integrations.kiro_cli_db()

def _kiro_cli_sessions() -> _Path | None:
    from app.core import integrations
    return integrations.kiro_cli_sessions_dir()

def _claude_projects() -> _Path | None:
    from app.core import integrations
    return integrations.claude_projects_dir()

def _ivyea_sessions_dir() -> _Path | None:
    from app.core import integrations
    return integrations.ivyea_sessions_dir()

def _dsh_sessions_dir() -> _Path | None:
    from app.core import integrations
    return integrations.dsh_sessions_dir()
_LOCAL_TZ = _timezone(_timedelta(hours=8))
_KIRO_DEFAULT_CONTEXT_TOKENS = 200_000

# Pricing per 1M tokens (input, output) in USD. Keys are lowercase.
# Anthropic 一档按官方价目表（2026-06 口径）：Fable 5 是 10/50，Opus 家族 5/25，
# Sonnet 3/15，Haiku 1/5。**别照搬旧记忆里的 15/75** —— 那是早已作废的 Opus 3 价，
# 会把 opus 成本整体高估 3 倍。
_PRICING = {
    # Anthropic Claude
    "claude-fable-5": (10, 50), "claude-mythos-5": (10, 50),
    "claude-opus-5": (5, 25),
    "claude-opus-4-8": (5, 25), "claude-opus-4-7": (5, 25), "claude-opus-4-6": (5, 25),
    "claude-opus-4.5": (5, 25), "claude-opus-4": (5, 25),
    "claude-sonnet-5": (3, 15),
    "claude-sonnet-4-6": (3, 15), "claude-sonnet-4-5": (3, 15), "claude-sonnet-4": (3, 15),
    "claude-sonnet-4.5": (3, 15), "claude-3.7-sonnet": (3, 15), "claude-3-5-sonnet": (3, 15),
    "claude-haiku-4-5": (1, 5), "claude-haiku-4.5": (1, 5), "claude-3-5-haiku": (1, 5),
    # OpenAI
    "gpt-5.5": (2, 10), "gpt-5.4": (2, 10), "gpt-5": (2, 10),
    "gpt-5-codex": (2, 10), "gpt-5.3-codex": (2, 10),
    "gpt-4o": (2.5, 10), "gpt-4o-mini": (0.15, 0.6), "o3": (2, 8), "o4-mini": (1.1, 4.4),
    "gpt-image-2": (0, 0),  # per-image pricing, not token-based
    # DeepSeek
    "deepseek-chat": (0.27, 1.1), "deepseek-reasoner": (0.55, 2.19), "deepseek-3.2": (0.27, 1.1),
    "deepseek-v4-pro": (0.55, 2.19), "deepseek-v4-flash": (0.27, 1.1),
    # xAI
    "grok-4.5": (3, 15), "grok-4": (3, 15),
    # MiniMax
    "minimax-m2.7": (0.5, 2), "minimax/minimax-m2.7": (0.5, 2), "minimax-m2": (0.5, 2),
    # Kimi / Moonshot
    "moonshotai/kimi-k2.6": (1, 4), "kimi-k2.6": (1, 4), "kimi-k2.5": (1, 4), "kimi-k2": (0.6, 2.5),
    # Xiaomi MiMo
    "mimo-v2.5-pro": (0.3, 1.2), "mimo-v2-pro": (0.3, 1.2), "mimo": (0.3, 1.2),
    # Misc / OpenRouter-style
    "qwen3-coder-next": (0.5, 2), "qwen3-coder": (0.5, 2),
    "gemini-2.0-flash": (0.1, 0.4), "gemini-2.5-pro": (1.25, 5),
    "llama-3.3-70b": (0.59, 0.79), "glm-4.6": (0.6, 2.2), "glm-4.5": (0.6, 2.2),
}
_DEFAULT_PRICE = (1, 4)  # conservative mid-tier fallback for unknown models


def _price_for(model: str) -> tuple:
    """Resolve pricing for a model name via longest-key prefix/substring match.

    Iterating longest keys first avoids a short key (e.g. 'gpt-5') shadowing a
    more specific one (e.g. 'gpt-5.4'). Unknown models fall back to a
    conservative mid-tier price rather than the old sonnet-level (3,15),
    which over-counted cheap models ~10×.
    """
    key = (model or "").lower()
    if not key:
        return _DEFAULT_PRICE
    for k in sorted(_PRICING, key=len, reverse=True):
        if k in key or key in k:
            return _PRICING[k]
    return _DEFAULT_PRICE


def _estimate_cost(model: str, inp: int, out: int, cache_read: int = 0, cache_write: int = 0) -> float:
    """Estimate cost in USD. cache_read = 0.1× input rate; cache_write = 1.25×."""
    prices = _price_for(model)
    return (
        inp * prices[0]
        + out * prices[1]
        + cache_read * prices[0] * 0.1
        + cache_write * prices[0] * 1.25
    ) / 1_000_000


def _scan_claude_sessions(since: float) -> list:
    """Scan Claude Code jsonl sessions for usage data."""
    import json as _json
    results = []
    root = _claude_projects()
    if root is None:
        return results
    for jsonl in root.rglob("*.jsonl"):
        if jsonl.stat().st_mtime < since:
            continue
        session_input = session_output = session_cache_read = session_cache_write = 0
        # 按"哪个模型烧掉的 token 最多"定这个会话的归属，而不是取文件里第一个 model。
        # 取第一个会踩到 `<synthetic>` —— Claude Code 把配额提示/报错这类本地构造的
        # 消息也写成 message.model，它排在真实回合前面，于是整个会话被记到一个不存在
        # 的"模型"名下（改之前有 55 亿 token 挂在 `<synthetic>` 上）。
        model_tokens: dict = {}
        ts = jsonl.stat().st_mtime
        with open(jsonl, encoding="utf-8") as fh:
            for line in fh:
                try:
                    d = _json.loads(line)
                    msg = d.get("message", {})
                    if isinstance(msg, dict):
                        usage = msg.get("usage", {})
                        if usage:
                            inp = usage.get("input_tokens", 0)
                            c_read = usage.get("cache_read_input_tokens", 0)
                            c_write = usage.get("cache_creation_input_tokens", 0)
                            out = usage.get("output_tokens", 0)
                            session_input += inp
                            session_cache_read += c_read
                            session_cache_write += c_write
                            session_output += out
                            m = msg.get("model")
                            if m and not str(m).startswith("<"):
                                model_tokens[m] = model_tokens.get(m, 0) + inp + out + c_read + c_write
                except Exception:
                    logger.debug("_json.loads 失败（旁路，已忽略）", exc_info=True)
        model = max(model_tokens, key=model_tokens.get) if model_tokens else None
        if session_input > 0 or session_output > 0 or session_cache_read > 0 or session_cache_write > 0:
            results.append({
                "ts": ts,
                "model": model or "claude-code",
                "input": session_input,
                "output": session_output,
                "cache_read": session_cache_read,
                "cache_write": session_cache_write,
            })
    return results


def _read_codex_rollout_usage(path: str) -> dict | None:
    """Return final token_count from a Codex rollout jsonl when available."""
    import json as _json
    if not path:
        return None
    p = _Path(path)
    if not p.exists():
        return None
    usage = None
    ts = None
    try:
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                try:
                    d = _json.loads(line)
                except Exception:
                    continue
                payload = d.get("payload") or {}
                if payload.get("type") != "token_count":
                    continue
                info = payload.get("info") or {}
                total_usage = info.get("total_token_usage") or {}
                if not total_usage:
                    continue
                usage = total_usage
                raw_ts = d.get("timestamp")
                if raw_ts:
                    try:
                        ts = _datetime.fromisoformat(raw_ts.replace("Z", "+00:00")).timestamp()
                    except Exception:
                        ts = None
    except Exception:
        return None
    if not usage:
        return None
    return {
        "ts": ts,
        "input": int(usage.get("input_tokens") or 0),
        "output": int(usage.get("output_tokens") or 0),
        "total": int(usage.get("total_tokens") or 0),
    }


def _parse_iso_ts(value: str | None) -> float | None:
    if not value:
        return None
    raw = value.replace("Z", "+00:00")
    if "." in raw:
        head, tail = raw.split(".", 1)
        zone = ""
        if "+" in tail:
            frac, rest = tail.split("+", 1)
            zone = "+" + rest
        elif "-" in tail[1:]:
            frac, rest = tail.rsplit("-", 1)
            zone = "-" + rest
        else:
            frac = tail
        raw = f"{head}.{frac[:6]}{zone}"
    try:
        return _datetime.fromisoformat(raw).timestamp()
    except Exception:
        return None


def _scan_kiro_cli_sessions(since: float) -> list:
    """Estimate Kiro CLI token usage from saved context usage percentages."""
    import json as _json
    results = []
    sessions_root = _kiro_cli_sessions()
    if sessions_root is None or not sessions_root.exists():
        return results

    def _collect_context_percentages(node, out: list):
        if isinstance(node, dict):
            val = node.get("context_usage_percentage")
            if isinstance(val, (int, float)) and val > 0:
                out.append(float(val))
            for child in node.values():
                _collect_context_percentages(child, out)
        elif isinstance(node, list):
            for child in node:
                _collect_context_percentages(child, out)

    def _collect_metering_credits(node) -> float:
        total = 0.0
        if isinstance(node, dict):
            usage = node.get("metering_usage")
            if isinstance(usage, list):
                for item in usage:
                    if not isinstance(item, dict):
                        continue
                    unit = str(item.get("unit") or item.get("unitPlural") or "").lower()
                    value = item.get("value")
                    if "credit" in unit and isinstance(value, (int, float)):
                        total += float(value)
            for child in node.values():
                total += _collect_metering_credits(child)
        elif isinstance(node, list):
            for child in node:
                total += _collect_metering_credits(child)
        return total

    for snap in sessions_root.glob("*.json"):
        if snap.stat().st_mtime < since:
            continue
        try:
            data = _json.loads(snap.read_text(encoding="utf-8"))
        except Exception:
            continue
        ts = _parse_iso_ts(data.get("updated_at")) or snap.stat().st_mtime
        if ts < since:
            continue
        percentages: list[float] = []
        _collect_context_percentages(data, percentages)
        estimated_input = sum(int((pct / 100) * _KIRO_DEFAULT_CONTEXT_TOKENS) for pct in percentages)
        if estimated_input <= 0:
            continue
        results.append({
            "ts": ts,
            "model": "kiro-cli-estimate",
            "input": estimated_input,
            "output": 0,
            "turns": len(percentages),
            "credits": round(_collect_metering_credits(data), 6),
        })
    return results


def _append_coverage(rows: list, source: str, path: _Path, status: str, sessions: int = 0, total_tokens: int = 0, credits: float = 0.0):
    rows.append({
        "source": source,
        "path": str(path),
        "status": status,
        "sessions": sessions,
        "total_tokens": total_tokens,
        "credits": round(credits, 6),
    })


# ── Token source registry ────────────────────────────────────────────────────
# Each scanner reads ONE upstream tool's local store and yields flat records:
#   {ts, model, input, output, agent, source, credits, cache_read, cache_write}
# Plus a coverage dict describing what was found. To add a NEW tool later,
# write a scanner and register it in _TOKEN_SOURCES — nothing else changes.

def _rec(ts, model, inp, out, agent, source, credits=0.0, cache_read=0, cache_write=0):
    return {"ts": ts, "model": model, "input": inp, "output": out, "agent": agent,
            "source": source, "credits": credits, "cache_read": cache_read, "cache_write": cache_write}


def _scan_hermes(since: float):
    p = _hermes_db()
    if not (p and p.exists()):
        return [], {"source": "Hermes", "path": p, "status": "missing"}
    recs, total = [], 0
    try:
        conn = _sqlite3.connect(str(p))
        for row in conn.execute(
            """SELECT started_at, model, input_tokens, output_tokens, cache_read_tokens,
                      cache_write_tokens, reasoning_tokens, source
               FROM sessions WHERE started_at > ?""", (since,)).fetchall():
            inp = row[2] or 0
            out = (row[3] or 0) + (row[6] or 0)
            recs.append(_rec(row[0], row[1] or "hermes", inp, out, "Hermes",
                             f"Hermes/{row[7] or 'hermes'}", cache_read=row[4] or 0, cache_write=row[5] or 0))
            total += inp + out + (row[4] or 0) + (row[5] or 0)
        conn.close()
        return recs, {"source": "Hermes", "path": p, "status": "included", "sessions": len(recs), "total": total}
    except Exception as e:
        return recs, {"source": "Hermes", "path": p, "status": f"error: {e}"}


def _scan_kiro_gateway(since: float):
    p = _kiro_gw_db()
    if not (p and p.exists()):
        return [], {"source": "Kiro Gateway", "path": p, "status": "missing"}
    recs, total = [], 0
    try:
        conn = _sqlite3.connect(str(p))
        for row in conn.execute(
            "SELECT ts, model, prompt_tokens, completion_tokens, total_tokens, source FROM token_usage WHERE ts > ?",
            (since,)).fetchall():
            inp, out = row[2] or 0, row[3] or 0
            if not inp and not out and row[4]:
                inp, out = int(row[4] * 0.8), int(row[4] * 0.2)
            recs.append(_rec(row[0], row[1] or "kiro-gateway", inp, out, "Kiro", row[5] or "kiro-gateway"))
            total += inp + out
        conn.close()
        return recs, {"source": "Kiro Gateway", "path": p, "status": "included", "sessions": len(recs), "total": total}
    except Exception:
        return recs, {"source": "Kiro Gateway", "path": p, "status": "error"}


def _scan_kiro_cli(since: float):
    p = _kiro_cli_sessions()
    if not (p and p.exists()):
        return [], {"source": "Kiro CLI sessions", "path": p, "status": "missing"}
    recs, total, credits_seen = [], 0, 0.0
    try:
        for s in _scan_kiro_cli_sessions(since):
            c = s.get("credits") or 0.0
            recs.append(_rec(s["ts"], s["model"], s["input"], s["output"], "Kiro", "Kiro CLI estimate", credits=c))
            total += s["input"] + s["output"]
            credits_seen += c
        return recs, {"source": "Kiro CLI sessions", "path": p, "status": "estimated-from-context-usage",
                      "sessions": len(recs), "total": total, "credits": credits_seen}
    except Exception:
        return recs, {"source": "Kiro CLI sessions", "path": p, "status": "error"}


def _scan_codex_source(since: float, path_getter, agent: str, source_name: str):
    p = path_getter()
    if not (p and p.exists()):
        return [], {"source": source_name, "path": p, "status": "missing"}
    recs, total = [], 0
    try:
        conn = _sqlite3.connect(str(p))
        for row in conn.execute(
            """SELECT created_at, updated_at, model, tokens_used, rollout_path
               FROM threads WHERE tokens_used > 0 AND updated_at > ?""", (since,)).fetchall():
            usage = _read_codex_rollout_usage(row[4])
            if usage and usage["total"] > 0:
                ts, inp, out = usage["ts"] or row[1] or row[0], usage["input"], usage["output"]
            else:
                tot = row[3] or 0
                ts, inp, out = row[1] or row[0], int(tot * 0.8), int(tot * 0.2)
            recs.append(_rec(ts, row[2] or "codex", inp, out, agent, source_name))
            total += inp + out
        conn.close()
        return recs, {"source": source_name, "path": p, "status": "included", "sessions": len(recs), "total": total}
    except Exception:
        return recs, {"source": source_name, "path": p, "status": "error"}


def _scan_claude(since: float):
    p = _claude_projects()
    if not (p and p.exists()):
        return [], {"source": "Claude Code", "path": p, "status": "missing"}
    recs, total = [], 0
    try:
        for s in _scan_claude_sessions(since):
            recs.append(_rec(s["ts"], s["model"], s["input"], s["output"], "Claude Code", "Claude Code",
                             cache_read=s.get("cache_read", 0), cache_write=s.get("cache_write", 0)))
            total += s["input"] + s["output"] + s.get("cache_read", 0) + s.get("cache_write", 0)
        return recs, {"source": "Claude Code", "path": p, "status": "included", "sessions": len(recs), "total": total}
    except Exception:
        return recs, {"source": "Claude Code", "path": p, "status": "error"}


def _scan_ivyea_agent(since: float):
    """Scan ivyea-agent 的会话账本 ~/.ivyea/sessions/<id>.json。

    每个会话存 {id, created, updated, model, messages, usage}，usage 是
    ivyea_agent.sessions._merge_stats 累出来的 {prompt, completion, cost, turns}。
    ``prompt`` 走的是 OpenAI 兼容语义 —— **已经含缓存命中的那部分**，所以整体计入
    input（和 Codex 口径一致），这里没有可单独拆出的 cache 列。

    这个目录里还混着 MCP 的结果转储（{doc,data} 那种），靠"有没有 usage"筛掉。
    """
    import json as _json
    p = _ivyea_sessions_dir()
    if not (p and p.exists()):
        return [], {"source": "Ivyea Agent", "path": p, "status": "missing"}
    recs, total = [], 0
    try:
        for f in p.glob("*.json"):
            try:
                if f.stat().st_mtime < since:
                    continue
                d = _json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(d, dict):
                continue
            usage = d.get("usage")
            if not isinstance(usage, dict) or not usage:
                continue
            inp = int(usage.get("prompt") or usage.get("prompt_tokens") or 0)
            out = int(usage.get("completion") or usage.get("completion_tokens") or 0)
            if inp <= 0 and out <= 0:
                continue
            ts = d.get("updated") or d.get("created") or f.stat().st_mtime
            try:
                ts = float(ts)
            except Exception:
                ts = f.stat().st_mtime
            recs.append(_rec(ts, d.get("model") or "ivyea-agent", inp, out,
                             "Ivyea Agent", "Ivyea Agent"))
            total += inp + out
        return recs, {"source": "Ivyea Agent", "path": p, "status": "included",
                      "sessions": len(recs), "total": total}
    except Exception as e:
        return recs, {"source": "Ivyea Agent", "path": p, "status": f"error: {e}"}


def _scan_dsh(since: float):
    """Scan DeepSeek Harness 会话 ~/.dsh/sessions/<proj>/<session>/session.jsonl.zstd。

    每个 ``assistant/message`` 事件带
    ``usage: {inputTokens, outputTokens, reasoningTokens, cacheReadTokens}``。
    reasoning 归到 output（和 Hermes 一样：思考 token 是按输出价计的）。

    这些文件是 zstd 压的，而 IvyeaOps 跑在系统 python 上、没有 zstandard 包 ——
    所以走 `zstd -dc` 子进程。没这个二进制就整源跳过并在覆盖表里说明，绝不静默归零。
    事件自带毫秒时间戳，按事件时间分桶，比按文件 mtime 准。
    """
    import json as _json
    import shutil as _shutil
    import subprocess as _subprocess
    p = _dsh_sessions_dir()
    if not (p and p.exists()):
        return [], {"source": "DeepSeek Harness", "path": p, "status": "missing"}
    zstd = _shutil.which("zstd")
    if not zstd:
        return [], {"source": "DeepSeek Harness", "path": p, "status": "error: 缺少 zstd 命令，无法解压会话"}
    recs, total = [], 0
    try:
        for f in p.rglob("session.jsonl.zstd"):
            if f.stat().st_mtime < since:
                continue
            try:
                raw = _subprocess.run([zstd, "-dc", str(f)], capture_output=True,
                                      timeout=60).stdout.decode("utf-8", "ignore")
            except Exception:
                continue
            # 一个会话可能横跨多天，按天聚合而不是整包记在最后一次修改时间上。
            per_day: dict = {}
            model = None
            for line in raw.splitlines():
                if not line.strip():
                    continue
                try:
                    d = _json.loads(line)
                except Exception:
                    continue
                data = d.get("data") or {}
                if not model and isinstance(data, dict) and data.get("model"):
                    model = data["model"]
                if d.get("type") != "assistant/message":
                    continue
                u = (data or {}).get("usage") or {}
                if not u:
                    continue
                ms = d.get("time")
                ts = (ms / 1000.0) if isinstance(ms, (int, float)) and ms > 1e11 else f.stat().st_mtime
                day = _datetime.fromtimestamp(ts, tz=_LOCAL_TZ).strftime("%Y-%m-%d")
                b = per_day.setdefault(day, {"ts": ts, "input": 0, "output": 0, "cache_read": 0})
                b["input"] += int(u.get("inputTokens") or 0)
                b["output"] += int(u.get("outputTokens") or 0) + int(u.get("reasoningTokens") or 0)
                b["cache_read"] += int(u.get("cacheReadTokens") or 0)
            for b in per_day.values():
                if b["input"] <= 0 and b["output"] <= 0 and b["cache_read"] <= 0:
                    continue
                recs.append(_rec(b["ts"], model or "deepseek-harness", b["input"], b["output"],
                                 "DeepSeek Harness", "DeepSeek Harness", cache_read=b["cache_read"]))
                total += b["input"] + b["output"] + b["cache_read"]
        return recs, {"source": "DeepSeek Harness", "path": p, "status": "included",
                      "sessions": len(recs), "total": total}
    except Exception as e:
        return recs, {"source": "DeepSeek Harness", "path": p, "status": f"error: {e}"}


# Registry of all token sources. Append a (name, scanner) here to add a tool.
_TOKEN_SOURCES = [
    ("Hermes", _scan_hermes),
    ("Kiro Gateway", _scan_kiro_gateway),
    ("Kiro CLI", _scan_kiro_cli),
    ("Codex", lambda since: _scan_codex_source(since, _codex_db, "Codex", "Codex")),
    ("Feishu Codex", lambda since: _scan_codex_source(since, _feishu_codex_db, "Hermes", "Hermes/Feishu Codex Relay")),
    ("Claude Code", _scan_claude),
    ("Ivyea Agent", _scan_ivyea_agent),
    ("DeepSeek Harness", _scan_dsh),
]


def iter_all_records(since: float):
    """Scan every registered source. Returns (records, coverage_rows).

    Single source of truth shared by the live endpoint and the archiver.
    """
    all_recs: list = []
    coverage: list = []
    for _name, scanner in _TOKEN_SOURCES:
        try:
            recs, cov = scanner(since)
        except Exception as e:  # a broken scanner must not kill the rest
            recs, cov = [], {"source": _name, "path": None, "status": f"error: {e}"}
        all_recs.extend(recs)
        p = cov.get("path")
        _append_coverage(coverage, cov["source"], p if p is not None else _Path("(unconfigured)"),
                         cov["status"], cov.get("sessions", 0), cov.get("total", 0), cov.get("credits", 0.0))
    return all_recs, coverage


@router.get("/token-usage")
def token_usage(_user: str = Depends(require_user)) -> dict:
    """Token usage stats from ALL sources on this server."""
    now = time.time()
    today_key = _datetime.fromtimestamp(now, tz=_LOCAL_TZ).strftime("%Y-%m-%d")
    daily_map: dict = {}
    weekly_map: dict = {}
    monthly_map: dict = {}
    model_map: dict = {}
    agent_map: dict = {}
    today_agent_map: dict = {}
    coverage: list = []
    # Running grand totals across ALL ingested rows (full window, every source).
    totals = {"sessions": 0, "input_tokens": 0, "output_tokens": 0,
              "cache_read_tokens": 0, "cache_write_tokens": 0,
              "total_tokens": 0, "cost_usd": 0.0}

    def _bump(m: dict, key: str, inp: int, out: int, cost: float, source: str | None = None,
              credits: float = 0.0, cache_read: int = 0, cache_write: int = 0):
        if key not in m:
            m[key] = {
                "sessions": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
                "credits": 0.0,
                "sources": set(),
            }
        m[key]["sessions"] += 1
        m[key]["input_tokens"] += inp
        m[key]["output_tokens"] += out
        m[key]["cache_read_tokens"] += cache_read
        m[key]["cache_write_tokens"] += cache_write
        m[key]["total_tokens"] += inp + out + cache_read + cache_write
        m[key]["cost_usd"] = round(m[key]["cost_usd"] + cost, 4)
        m[key]["credits"] = round(m[key]["credits"] + credits, 6)
        if source:
            m[key]["sources"].add(source)

    def _add(ts: float, model: str, inp: int, out: int, agent: str, source: str, credits: float = 0.0, cache_read: int = 0, cache_write: int = 0):
        dt = _datetime.fromtimestamp(ts, tz=_LOCAL_TZ)
        day = dt.strftime("%Y-%m-%d")
        week = dt.strftime("%Y-W%W")
        month = dt.strftime("%Y-%m")
        # **缓存也是 token。** 少算它就没法跨工具比：Claude Code 每次请求的裸
        # input_tokens 只有几个 token，整段上下文都记在 cache_read 里；而 Codex 上报的
        # input_tokens 本身就含 cached_input_tokens（实测 96.5% 是缓存）。只算 in+out
        # 等于把 Claude 的量抹成 0、把 Codex 的照单全收 —— 排行榜会整个反过来。
        total = inp + out + cache_read + cache_write
        cost = _estimate_cost(model, inp, out, cache_read, cache_write)
        for key, m in [(day, daily_map), (week, weekly_map), (month, monthly_map)]:
            if key not in m:
                m[key] = {"sessions": 0, "input_tokens": 0, "output_tokens": 0,
                          "cache_read_tokens": 0, "cache_write_tokens": 0,
                          "total_tokens": 0, "cost_usd": 0.0}
            m[key]["sessions"] += 1
            m[key]["input_tokens"] += inp
            m[key]["output_tokens"] += out
            m[key]["cache_read_tokens"] += cache_read
            m[key]["cache_write_tokens"] += cache_write
            m[key]["total_tokens"] += total
            m[key]["cost_usd"] = round(m[key]["cost_usd"] + cost, 4)
        _bump(agent_map, agent, inp, out, cost, source, credits, cache_read, cache_write)
        if day == today_key:
            _bump(today_agent_map, agent, inp, out, cost, source, credits, cache_read, cache_write)
        # Model breakdown — same full window as agents (口径统一).
        if model:
            if model not in model_map:
                model_map[model] = {"sessions": 0, "total_tokens": 0, "cost_usd": 0.0}
            model_map[model]["sessions"] += 1
            model_map[model]["total_tokens"] += total
            model_map[model]["cost_usd"] = round(model_map[model]["cost_usd"] + cost, 4)
        # Grand totals.
        totals["sessions"] += 1
        totals["input_tokens"] += inp
        totals["output_tokens"] += out
        totals["cache_read_tokens"] += cache_read
        totals["cache_write_tokens"] += cache_write
        totals["total_tokens"] += total
        totals["cost_usd"] = round(totals["cost_usd"] + cost, 4)

    # Full lookback window — 2 years covers all current data with headroom.
    since = now - 730 * 86400

    # --- Live scan: every registered source (single source of truth) ---
    live_records, coverage = iter_all_records(since)
    seen_live_day_source = set()  # (day, source) present in live data
    for r in live_records:
        day = _datetime.fromtimestamp(r["ts"], tz=_LOCAL_TZ).strftime("%Y-%m-%d")
        seen_live_day_source.add((day, r["source"]))
        _add(r["ts"], r["model"], r["input"], r["output"], r["agent"], r["source"],
             r["credits"], r["cache_read"], r["cache_write"])

    # --- Archive backfill: replay archived rows for (day, source) pairs the
    # live scan did NOT cover, so history survives even after a tool is deleted.
    try:
        from app.services import token_archive
        backfill_sources: dict = {}
        for ar in token_archive.load_records(since):
            if (ar["day"], ar["source"]) in seen_live_day_source:
                continue  # live data wins for days still readable
            _add(ar["ts"], ar["model"], ar["input"], ar["output"], ar["agent"],
                 ar["source"], ar.get("credits", 0.0), ar.get("cache_read", 0), ar.get("cache_write", 0))
            agg = backfill_sources.setdefault(ar["source"], 0)
            backfill_sources[ar["source"]] = (agg + ar["input"] + ar["output"]
                                              + ar.get("cache_read", 0) + ar.get("cache_write", 0))
        for src, tok in sorted(backfill_sources.items()):
            coverage.append({"source": f"{src} (归档)", "path": "token_archive.sqlite3",
                             "status": "from-archive", "sessions": 0,
                             "total_tokens": tok, "credits": 0})
    except Exception:
        logger.debug("backfill_sources: dict = {} 失败（旁路，已忽略）", exc_info=True)

    # Format output
    def _to_list(m, key_name):
        return sorted([{key_name: k, **v} for k, v in m.items()], key=lambda x: x[key_name], reverse=True)

    def _agent_list(m: dict):
        rows = []
        for k, v in m.items():
            row = {kk: vv for kk, vv in v.items() if kk != "sources"}
            row["agent"] = k
            row["sources"] = sorted(v.get("sources") or [])
            rows.append(row)
        return sorted(rows, key=lambda x: x["total_tokens"], reverse=True)

    daily = _to_list(daily_map, "day")[:90]
    weekly = _to_list(weekly_map, "week")[:26]
    monthly = _to_list(monthly_map, "month")[:12]
    models = sorted(
        [{"model": k, **v} for k, v in model_map.items()],
        key=lambda x: x["total_tokens"], reverse=True
    )[:30]

    return {
        "totals": totals,
        "daily": daily,
        "weekly": weekly,
        "monthly": monthly,
        "models": models,
        "agents": _agent_list(agent_map),
        "today_agents": _agent_list(today_agent_map),
        "coverage": coverage,
        "timezone": "Asia/Shanghai",
    }
