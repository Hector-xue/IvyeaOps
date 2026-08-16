#!/usr/bin/env python3
"""IvyeaOps CPU watchdog — alerts on sustained high CPU.

Runs out-of-process (cron, every minute) so it can detect the very thing
that would prevent IvyeaOps from monitoring itself: a CPU-bound runaway
inside the uvicorn event loop. The systemd watchdog catches the worst
case (process is unresponsive); this catches the slower kind where the
process is "alive" but burning CPU on a tight loop.

Logic:
  1. Resolve the IvyeaOps MainPID via systemd
  2. Sample %CPU using two /proc/<pid>/stat reads ~3s apart
  3. Append the sample (timestamp, %cpu) to /tmp/IvyeaOps-cpu-history.json
     keeping only the last SUSTAIN_MIN minutes of samples
  4. If every sample in the window is above THRESHOLD_PCT, push an alert
  5. After alerting, wait COOLDOWN_MIN before the next push

Two delivery channels, tried in order:
  A) Custom-bot webhook   (simple but needs keyword/signature setup)
  B) Feishu App API       (uses self-built app credentials)

Configuration source (single source of truth = IvyeaOps hub_settings.json,
written via the web UI's 系统配置 page). Keys consulted:

  alert_webhook       Custom-bot webhook URL (channel A)
  alert_app_id        Feishu app id   (channel B)
  alert_app_secret    Feishu app secret
  alert_chat_id       Target open_chat_id (oc_xxx...)
  alert_threshold     CPU% trigger (default 80)
  alert_sustain       Minutes above threshold before firing (default 5)
  alert_cooldown      Minutes between alerts (default 30)

If a key is empty in hub_settings.json we fall back to the corresponding
env var (IVYEA_OPS_ALERT_*), then to an optional Hermes .env file for the
Feishu trio (FEISHU_APP_ID/FEISHU_APP_SECRET/FEISHU_HOME_CHANNEL) — that
fallback is purely a convenience for installs that co-host Hermes and
can be ignored everywhere else.

Cron entry (one minute interval):
  * * * * * /usr/bin/python3 /opt/ivyeaops/scripts/cpu_alert.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Make `from app.core import hub_settings` importable when running as a
# standalone cron script (no PYTHONPATH set).
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "server"))

STATE_PATH = Path("/tmp/IvyeaOps-cpu-history.json")
HERMES_ENV_PATH = Path(os.environ.get("HERMES_ENV", "/root/.hermes/.env"))
SAMPLE_GAP_S = 3.0  # how far apart the two /proc/stat snapshots are


def _read_env_file(path: Path, key: str) -> str:
    """Tiny .env reader (no quotes/comments handling beyond the basics)."""
    if not path.is_file():
        return ""
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip().strip("\"'")
    except OSError:
        return ""
    return ""


def _hub_setting(key: str) -> str:
    """Read one value from hub_settings.json via the app's helper, so the
    JSON-then-env precedence is identical to the live server."""
    try:
        from app.core import hub_settings as _hs  # type: ignore
    except Exception:
        return ""
    try:
        v = _hs.get(key, "")
        return "" if v is None else str(v)
    except Exception:
        return ""


def _resolve(setting_key: str, *, hermes_key: str = "") -> str:
    """hub_settings.json → env var (handled inside _hs.get) → Hermes .env."""
    v = _hub_setting(setting_key)
    if v:
        return v
    if hermes_key:
        v = _read_env_file(HERMES_ENV_PATH, hermes_key)
        if v:
            return v
    return ""


WEBHOOK_URL = _resolve("alert_webhook")
APP_ID = _resolve("alert_app_id", hermes_key="FEISHU_APP_ID")
APP_SECRET = _resolve("alert_app_secret", hermes_key="FEISHU_APP_SECRET")
CHAT_ID = _resolve("alert_chat_id", hermes_key="FEISHU_HOME_CHANNEL")
# 'feishu' (cn) → open.feishu.cn ; 'lark' or anything else → open.larksuite.com
_FEISHU_DOMAIN = (
    _resolve("alert_feishu_domain", hermes_key="FEISHU_DOMAIN") or "feishu"
).lower()
FEISHU_HOST = (
    "https://open.feishu.cn" if _FEISHU_DOMAIN == "feishu" else "https://open.larksuite.com"
)
THRESHOLD_PCT = float(_hub_setting("alert_threshold") or "80")
SUSTAIN_MIN = int(_hub_setting("alert_sustain") or "5")
COOLDOWN_MIN = int(_hub_setting("alert_cooldown") or "30")


def _systemd_main_pid() -> int | None:
    try:
        out = subprocess.check_output(
            ["systemctl", "show", "-p", "MainPID", "--value", "ivyea-ops.service"],
            text=True,
            timeout=5,
        ).strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    if not out or out == "0":
        return None
    try:
        return int(out)
    except ValueError:
        return None


def _proc_ticks(pid: int) -> tuple[int, int] | None:
    """(utime, stime) ticks from /proc/<pid>/stat, or None if pid is gone."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            raw = f.read()
    except OSError:
        return None
    # Field layout: pid (comm) state ppid pgrp ... utime(14) stime(15)
    # comm is parenthesised and may contain spaces/parens, so split on the
    # last ')' to skip past it.
    try:
        rest = raw.rsplit(b")", 1)[1].split()
        utime = int(rest[11])  # 14 - 3 (we dropped pid, comm; rest starts at state)
        stime = int(rest[12])
        return utime, stime
    except (IndexError, ValueError):
        return None


def _sample_cpu_pct(pid: int) -> float | None:
    """Average CPU% over SAMPLE_GAP_S seconds. None if process disappears."""
    a = _proc_ticks(pid)
    if a is None:
        return None
    t0 = time.monotonic()
    time.sleep(SAMPLE_GAP_S)
    b = _proc_ticks(pid)
    if b is None:
        return None
    elapsed = time.monotonic() - t0
    try:
        clk = os.sysconf("SC_CLK_TCK") or 100
    except (OSError, ValueError):
        clk = 100
    delta_ticks = (b[0] - a[0]) + (b[1] - a[1])
    if delta_ticks < 0 or elapsed <= 0:
        return 0.0
    # 100% means saturating one core; can exceed 100% on multi-threaded loops.
    return 100.0 * delta_ticks / clk / elapsed


def _load_history() -> dict:
    if not STATE_PATH.is_file():
        return {"samples": [], "last_alert_ts": 0}
    try:
        return json.loads(STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {"samples": [], "last_alert_ts": 0}


def _save_history(state: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(state))
    except OSError:
        pass


def _trim_samples(samples: list[dict], window_s: int) -> list[dict]:
    cutoff = time.time() - window_s
    return [s for s in samples if s.get("ts", 0) >= cutoff]


def _post_feishu(webhook: str, text: str) -> bool:
    """Send a plain-text Feishu/Lark bot alert. Returns True on HTTP 200."""
    body = json.dumps({"msg_type": "text", "content": {"text": text}}).encode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


# --- Channel B: Feishu self-built app (reuses Hermes credentials) ----------

def _fetch_tenant_token(app_id: str, app_secret: str) -> str | None:
    """Exchange app_id/app_secret for a tenant_access_token. Tokens last
    ~2h; we don't bother caching since cron only runs once a minute."""
    url = f"{FEISHU_HOST}/open-apis/auth/v3/tenant_access_token/internal"
    body = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError):
        return None
    if data.get("code") != 0:
        return None
    return data.get("tenant_access_token")


def _post_feishu_app(app_id: str, app_secret: str, chat_id: str, text: str) -> bool:
    """Send via /im/v1/messages using the self-built app. Works even
    without webhook keyword/signature setup because the app already has
    im:message:send_as_bot scope (Hermes uses it for two-way chat)."""
    token = _fetch_tenant_token(app_id, app_secret)
    if not token:
        return False
    url = f"{FEISHU_HOST}/open-apis/im/v1/messages?receive_id_type=chat_id"
    body = json.dumps(
        {
            "receive_id": chat_id,
            "msg_type": "text",
            # content for msg_type=text is a JSON-string field, not an object.
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError):
        return False
    return data.get("code") == 0


def _push(text: str) -> tuple[bool, str]:
    """Send text via the first available channel.

    Returns (ok, channel). channel is one of "webhook", "app", "none".
    """
    if WEBHOOK_URL:
        if _post_feishu(WEBHOOK_URL, text):
            return True, "webhook"
        # Webhook configured but failed — fall through to App as backup.
    if APP_ID and APP_SECRET and CHAT_ID:
        if _post_feishu_app(APP_ID, APP_SECRET, CHAT_ID, text):
            return True, "app"
    return False, "none"


def _hostname() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return "unknown"


# ── 系统级孤儿进程巡检 ──────────────────────────────────────────────────────
#
# 上面那套只盯 IvyeaOps 自己的 MainPID —— 它是给"uvicorn 陷进死循环"设计的自监控。
# 代价是**机器上其它任何东西它都看不见**：2026-08-16 在生产机上抓到一个
# `bun … gbrain config set …`，本该几毫秒退出的一次性命令卡死空转了 8 天半，
# 一直吃满一个核（2 核机器负载 3.3），全程零告警。同一天还捞出跑了 6 天的测试
# serve 和 3 天的 headless Chrome。
#
# 这类漏网之鱼的共同特征很稳定：
#   PPID == 1（原会话早退了，被 init 收养）
#   + 不是任何 systemd 服务的 MainPID（真服务也 PPID==1，必须排掉）
#   + 持续高 CPU
#
# **只报告，不自动杀**：误杀一个正经进程，比多烧几个 CPU 严重得多。

ORPHAN_THRESHOLD_PCT = float(_hub_setting("orphan_threshold") or "50")
ORPHAN_SUSTAIN_MIN = int(_hub_setting("orphan_sustain") or "10")


def _systemd_managed_pids() -> set[int]:
    """所有 systemd 服务的 MainPID —— 它们 PPID 也是 1，但不是孤儿。"""
    pids: set[int] = set()
    try:
        out = subprocess.run(
            ["systemctl", "show", "--type=service", "--state=running",
             "--property=MainPID", "--value"],
            capture_output=True, text=True, timeout=15,
        ).stdout
        for line in out.splitlines():
            line = line.strip()
            if line.isdigit() and int(line) > 0:
                pids.add(int(line))
    except Exception:  # noqa: BLE001 — 巡检失败绝不能影响主告警
        pass
    return pids


def _proc_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return " ".join(p.decode("utf-8", "replace") for p in raw.split(b"\0") if p)


def _proc_ppid(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("PPid:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return -1


def scan_orphans(state: dict) -> list[dict]:
    """返回持续高 CPU 的孤儿进程。维护自己的采样历史，和主告警互不干扰。"""
    if not Path("/proc").exists():
        return []          # 非 Linux：整段跳过
    managed = _systemd_managed_pids()
    me = os.getpid()
    now = time.time()

    hist: dict = state.setdefault("orphans", {})
    live: set[str] = set()
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid == me or pid in managed or pid <= 1:
            continue
        if _proc_ppid(pid) != 1:
            continue
        cpu = _sample_cpu_pct(pid)
        if cpu is None:
            continue
        key = str(pid)
        live.add(key)
        rec = hist.setdefault(key, {"cmd": _proc_cmdline(pid)[:200], "samples": []})
        rec["samples"] = _trim_samples(rec["samples"], ORPHAN_SUSTAIN_MIN * 60 + 30)
        rec["samples"].append({"ts": now, "cpu": round(cpu, 1)})

    # PID 消失了就把历史丢掉，否则 PID 回绕后会把新进程算进旧账
    for key in list(hist):
        if key not in live:
            hist.pop(key, None)

    window_start = now - ORPHAN_SUSTAIN_MIN * 60
    hits = []
    for key, rec in hist.items():
        win = [s for s in rec["samples"] if s["ts"] >= window_start]
        if len(win) >= ORPHAN_SUSTAIN_MIN and all(s["cpu"] >= ORPHAN_THRESHOLD_PCT for s in win):
            hits.append({"pid": int(key), "cmd": rec["cmd"],
                         "avg": sum(s["cpu"] for s in win) / len(win),
                         "peak": max(s["cpu"] for s in win)})
    return sorted(hits, key=lambda h: -h["peak"])


def _proc_age(pid: int) -> str:
    try:
        out = subprocess.run(["ps", "-o", "etime=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        return out or "?"
    except Exception:  # noqa: BLE001
        return "?"


def main() -> int:
    # Test mode: `--test` sends a one-off test message and exits, useful
    # for verifying the webhook + group keyword/signature config without
    # having to actually pin the CPU.
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        # Print resolved channel summary first so the user sees what's
        # being attempted (without leaking secrets).
        print(f"Webhook: {'set' if WEBHOOK_URL else 'not set'}")
        print(f"App API: app_id={'set' if APP_ID else 'not set'}, "
              f"app_secret={'set' if APP_SECRET else 'not set'}, "
              f"chat_id={CHAT_ID or '(empty)'}")
        print(f"Domain:  {FEISHU_HOST}")
        if not (WEBHOOK_URL or (APP_ID and APP_SECRET and CHAT_ID)):
            print("\nNo channel is fully configured — aborting.")
            return 2
        msg = (
            f"✅ IvyeaOps 告警通道测试\n"
            f"主机: {_hostname()}\n"
            f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"如果你看到这条消息，说明告警链路通畅。"
        )
        ok, channel = _push(msg)
        print(f"\nresult: {'PUSH OK via ' + channel if ok else 'PUSH FAILED'}")
        if not ok:
            print("debug tips:")
            if WEBHOOK_URL:
                print("  - webhook 失败：检查 URL、关键词（消息里得包含设置的关键词）、签名")
            if APP_ID:
                print("  - app api 失败：检查 app_id/app_secret 是否正确，bot 是否在目标 chat 里，"
                      "tenant_access_token 是否能取到")
        return 0 if ok else 1

    pid = _systemd_main_pid()
    if pid is None:
        # Service not running — nothing to monitor (and systemd will already
        # have its own alerts for that via Restart=).
        return 0

    cpu = _sample_cpu_pct(pid)
    if cpu is None:
        return 0

    state = _load_history()
    samples = _trim_samples(state.get("samples", []), SUSTAIN_MIN * 60 + 30)
    samples.append({"ts": time.time(), "pid": pid, "cpu": round(cpu, 1)})
    state["samples"] = samples

    # Need at least SUSTAIN_MIN samples (≈ one per minute from cron) all
    # above threshold. Window is SUSTAIN_MIN minutes wide.
    window_start = time.time() - SUSTAIN_MIN * 60
    in_window = [s for s in samples if s["ts"] >= window_start]
    sustained = (
        len(in_window) >= SUSTAIN_MIN
        and all(s["cpu"] >= THRESHOLD_PCT for s in in_window)
    )

    last_alert = state.get("last_alert_ts", 0)
    cooldown_active = (time.time() - last_alert) < COOLDOWN_MIN * 60

    if sustained and not cooldown_active and (WEBHOOK_URL or (APP_ID and APP_SECRET and CHAT_ID)):
        peak = max(s["cpu"] for s in in_window)
        avg = sum(s["cpu"] for s in in_window) / len(in_window)
        msg = (
            f"⚠️ IvyeaOps CPU 持续高位\n"
            f"主机: {_hostname()}\n"
            f"PID:  {pid}\n"
            f"窗口: 最近 {SUSTAIN_MIN} 分钟\n"
            f"CPU%: avg={avg:.1f}%, peak={peak:.1f}% (阈值 {THRESHOLD_PCT:.0f}%)\n"
            f"建议: 立即检查 journalctl -u ivyea-ops --since '10 min ago'"
        )
        ok, _channel = _push(msg)
        if ok:
            state["last_alert_ts"] = time.time()

    _report_orphans(state)
    _save_history(state)
    return 0


def _report_orphans(state: dict) -> None:
    """孤儿巡检的告警分支。用**独立**的冷却计时：和主告警共用一个的话，
    IvyeaOps 自己那条一响就会把孤儿这条压住半小时，而这两件事毫无关系。"""
    try:
        hits = scan_orphans(state)
    except Exception:  # noqa: BLE001 — 巡检永远不许影响主告警
        return
    if not hits:
        return
    last = state.get("last_orphan_alert_ts", 0)
    if (time.time() - last) < COOLDOWN_MIN * 60:
        return
    if not (WEBHOOK_URL or (APP_ID and APP_SECRET and CHAT_ID)):
        return
    lines = [
        "⚠️ 发现无人托管的高 CPU 进程",
        f"主机: {_hostname()}",
        f"窗口: 最近 {ORPHAN_SUSTAIN_MIN} 分钟持续 ≥ {ORPHAN_THRESHOLD_PCT:.0f}%",
        "",
    ]
    for h in hits[:5]:
        lines.append(f"PID {h['pid']} · 已运行 {_proc_age(h['pid'])} · "
                     f"avg={h['avg']:.0f}% peak={h['peak']:.0f}%")
        lines.append(f"  {h['cmd'][:120]}")
    lines += ["", "这些进程的父进程已退出（PPID=1）且不受 systemd 管辖，",
              "多半是历史会话/脚本留下的。确认无用后按精确 PID 结束：kill <PID>",
              "（不要用宽泛的 pkill -f）"]
    ok, _ch = _push("\n".join(lines))
    if ok:
        state["last_orphan_alert_ts"] = time.time()


if __name__ == "__main__":
    sys.exit(main())
