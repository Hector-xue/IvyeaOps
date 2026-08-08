"""Client bridge from IvyeaOps to the local IvyeaAgent HTTP API."""
from __future__ import annotations

import json
import logging
import hashlib
import hmac
import os
import shutil
import socket
import subprocess
import tempfile
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from app.core.config import settings as ops_settings
from app.core.proc import no_window_kwargs

logger = logging.getLogger("ivyea.services.ivyea_agent")


DEFAULT_BASE_URL = "http://127.0.0.1:8765"
DEFAULT_TIMEOUT_SECONDS = 5.0
AUTOSTART_COOLDOWN_SECONDS = 20.0
_LAST_START_ATTEMPT = 0.0
_LAST_MODEL_SYNC_SIGNATURE = ""


class IvyeaAgentError(RuntimeError):
    """Base error for IvyeaAgent bridge failures."""


class IvyeaAgentUnavailable(IvyeaAgentError):
    """The local IvyeaAgent service is not reachable."""


def base_url() -> str:
    """Return the configured local IvyeaAgent base URL."""
    from app.core import hub_settings
    raw = (os.getenv("IVYEA_AGENT_URL") or str(hub_settings.get("ivyea_agent_url") or "") or DEFAULT_BASE_URL).strip()
    if not raw:
        raw = DEFAULT_BASE_URL
    if "://" not in raw:
        raw = f"http://{raw}"
    return raw.rstrip("/")


def token_configured() -> bool:
    return bool(_token())


def _find_ivyea_cli() -> str:
    found = shutil.which("ivyea")
    if found:
        return found
    candidates = [
        Path(sys.executable).resolve().parent / "ivyea",
        Path(sys.executable).resolve().parent / "ivyea.exe",
        ops_settings.root_dir / "server" / ".venv" / "bin" / "ivyea",
        ops_settings.root_dir / "server" / ".venv" / "Scripts" / "ivyea.exe",
        Path.home() / ".local" / "bin" / "ivyea",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return ""


def _service_bind() -> tuple[str, int] | None:
    parsed = urllib.parse.urlparse(base_url())
    host = parsed.hostname or "127.0.0.1"
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return None
    port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
    return host, port


def _timeout() -> float:
    raw = (os.getenv("IVYEA_AGENT_TIMEOUT") or "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return max(1.0, min(float(raw), 60.0))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def _token() -> str:
    from app.core import hub_settings
    return (
        str(hub_settings.get("ivyea_agent_token") or "")
        or os.getenv("IVYEA_AGENT_TOKEN")
        or os.getenv("IVYEA_API_TOKEN")
        or ""
    ).strip()


def _url(path: str) -> str:
    if not path.startswith("/") or path.startswith("//") or "://" in path:
        raise ValueError("IvyeaAgent path must be an absolute local API path")
    return urllib.parse.urljoin(base_url() + "/", path.lstrip("/"))


def _decode_json(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError as exc:
        raise IvyeaAgentError(f"IvyeaAgent returned non-JSON response: {exc}") from exc
    if not isinstance(data, dict):
        raise IvyeaAgentError("IvyeaAgent returned a JSON value that is not an object")
    return data


def request_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Call IvyeaAgent and return a JSON object.

    This wrapper intentionally uses stdlib urllib so IvyeaOps does not need a
    new runtime dependency just to talk to the local agent.
    """
    method = method.upper().strip()
    data = None
    headers = {
        "Accept": "application/json",
        "User-Agent": "IvyeaOps-IvyeaAgent-Bridge/1",
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    auth = _token()
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
    req = urllib.request.Request(_url(path), data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout or _timeout()) as resp:
            return _decode_json(resp.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        detail = ""
        if raw:
            try:
                body = _decode_json(raw)
                detail = str(body.get("detail") or body.get("error") or body)
            except IvyeaAgentError:
                detail = raw.decode("utf-8", errors="replace")[:500]
        raise IvyeaAgentError(f"IvyeaAgent HTTP {exc.code}: {detail or exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        raise IvyeaAgentUnavailable(str(exc)) from exc


def request_stream(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> Any:
    """Yield raw bytes from an IvyeaAgent streaming endpoint."""
    method = method.upper().strip()
    data = None
    headers = {
        "Accept": "text/event-stream",
        "User-Agent": "IvyeaOps-IvyeaAgent-Bridge/1",
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    auth = _token()
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
    req = urllib.request.Request(_url(path), data=data, headers=headers, method=method)

    def _chunks() -> Any:
        try:
            with urllib.request.urlopen(req, timeout=timeout or _timeout()) as resp:
                # read1 = "把现在已经到的字节给我"，read = "凑够 4096 或等到流结束"。
                # 必须用 read1：SSE 是低速率长连接，事件一到就得往下游转。
                # 用 read 时，agent 发完几百字节就停下来等（等人工审批、等一个几分钟
                # 的慢工具），这几百字节会一直卡在这里凑不满 4096——前端因此收不到
                # 审批卡，也看不到中途的步骤事件，看起来就像"卡死了"。
                # 顺带也修掉了正常轮次里 token 按 4KB 一坨才吐出来的顿挫感。
                reader = getattr(resp, "read1", None)
                while True:
                    chunk = reader(65536) if reader is not None else resp.read(4096)
                    if not chunk:
                        break
                    yield chunk
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            detail = raw.decode("utf-8", errors="replace")[:500] if raw else str(exc.reason)
            yield _sse_error(f"IvyeaAgent HTTP {exc.code}: {detail}")
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            yield _sse_error(f"IvyeaAgent 不可用：{exc}")

    return _chunks()


def _sse_error(detail: str) -> bytes:
    return (
        "event: error\n"
        f"data: {json.dumps({'ok': False, 'error': 'bridge_error', 'detail': detail}, ensure_ascii=False)}\n\n"
    ).encode("utf-8")


def availability() -> dict[str, Any]:
    """Best-effort health payload for UI status cards."""
    result: dict[str, Any] = {
        "ok": True,
        "available": False,
        "base_url": base_url(),
        "token_configured": token_configured(),
        "health": None,
        "error": "",
    }
    try:
        result["health"] = request_json("GET", "/health", timeout=2.0)
        result["available"] = bool(isinstance(result["health"], dict) and result["health"].get("ok"))
    except IvyeaAgentError as exc:
        result["ok"] = False
        result["error"] = str(exc)
    return result


def start_local_service() -> dict[str, Any]:
    bind = _service_bind()
    if not bind:
        return {"ok": False, "error": "auto_start_only_supports_localhost", "base_url": base_url()}
    host, port = bind

    # Frozen build (Windows x64 exe / macOS .app): the agent is bundled into this
    # exe — there is no `ivyea` binary or python. Run the serve from the exe itself
    # via `<exe> agent-serve …`, spawned detached. No pip/Python/git needed.
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "agent-serve", "--host", host, "--port", str(port)]
        from app.core.proc import child_env as _scrubbed_env
        env = _scrubbed_env()   # agent 只需要下面显式塞的 IVYEA_API_TOKEN
        token = _token()
        if token:
            env["IVYEA_API_TOKEN"] = token   # serve reads the token from env
        try:
            proc = subprocess.Popen(
                cmd, cwd=str(ops_settings.root_dir), env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=(os.name != "nt"), **no_window_kwargs(),
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "command": " ".join(cmd[:3])}
        return {"ok": True, "frozen": True, "pid": proc.pid, "command": "agent-serve"}

    cli = _find_ivyea_cli()
    if not cli:
        return {"ok": False, "error": "ivyea_cli_not_found"}
    cmd = [cli, "self", "service-start", "--host", host, "--port", str(port)]
    token = _token()
    from app.core.proc import child_env as _scrubbed_env
    env = _scrubbed_env()   # 同上：其余凭据不往下传
    if token:
        env["IVYEA_API_TOKEN"] = token
        cmd.extend(["--api-token", token])
    # 输出**落临时文件而不是管道**。这条命令要起一个守护进程，而在 Windows 上
    # 守护进程会继承管道的写端 —— 于是 subprocess.run 的 reader 线程永远等不到
    # EOF，连它自己 18 秒的 timeout 都会越过去，调用方就永久卡死。
    # （实测：Windows CI 上整个测试作业挂了 25 分钟，堆栈停在 `_readerthread`。）
    # 文件句柄没有这个问题：守护进程照样可以持有它，但这边不需要等任何人。
    try:
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            proc = subprocess.run(
                cmd,
                cwd=str(ops_settings.root_dir),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                timeout=18,
                **no_window_kwargs(),
            )
            out.seek(0)
            err.seek(0)
            stdout = out.read().decode("utf-8", "replace")
            stderr = err.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "command": " ".join(cmd)}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "command": " ".join(cmd[:6]),
        "stdout": stdout[-2000:],
        "stderr": stderr[-2000:],
    }


def _venv_python(cli: str) -> str:
    """The python next to the ivyea CLI (its install env), for pip upgrades."""
    parent = Path(cli).resolve().parent
    for name in ("python", "python3", "python.exe"):
        cand = parent / name
        if cand.is_file():
            return str(cand)
    return sys.executable


def agent_version() -> str:
    try:
        h = request_json("GET", "/health", timeout=2.0)
        return str(h.get("version") or "") if isinstance(h, dict) else ""
    except Exception:  # noqa: BLE001
        return ""


_AGENT_REPO = "Hector-xue/ivyea-agent"
_agent_latest_cache: dict[str, Any] = {"tag": "", "at": 0.0}


def latest_agent_version() -> str:
    """GitHub 上 IvyeaAgent 最新 release 的 tag（缓存 6h；离线/失败返回缓存或 ''）。
    供系统配置卡片显示"最新 vX / 有更新"。"""
    import time
    now = time.time()
    if _agent_latest_cache["tag"] and now - float(_agent_latest_cache["at"]) < 6 * 3600:
        return str(_agent_latest_cache["tag"])
    try:
        import urllib.request
        import json as _json
        req = urllib.request.Request(
            f"https://api.github.com/repos/{_AGENT_REPO}/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "IvyeaOps"})
        with urllib.request.urlopen(req, timeout=4) as r:
            tag = str(_json.loads(r.read().decode("utf-8", "replace")).get("tag_name") or "")
        if tag:
            _agent_latest_cache.update(tag=tag, at=now)
        return tag
    except Exception:  # noqa: BLE001
        return str(_agent_latest_cache["tag"] or "")


def _ver_tuple(v: str) -> tuple:
    parts = []
    for p in (v or "").strip().lstrip("vV").split("."):
        num = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts) or (0,)


def agent_update_available(current: str = "", latest: str = "") -> bool:
    current = current or _installed_agent_version("") or agent_version()
    latest = latest or latest_agent_version()
    if not current or not latest:
        return False   # 版本测不出（如源码机 serve 未起）→ 不误报"有更新"
    return _ver_tuple(latest) > _ver_tuple(current)


def _installed_agent_version(py: str) -> str:
    """Version of the *installed* ivyea_agent package (reflects files on disk),
    independent of whether the serve has restarted to load them."""
    if getattr(sys, "frozen", False):
        # Bundled into this exe — import it directly (sys.executable isn't python).
        try:
            import ivyea_agent
            return str(getattr(ivyea_agent, "__version__", "") or "")
        except Exception:  # noqa: BLE001
            return ""
    # `ivyea --version` 对**源码 launcher / pip / venv 都可靠**（源码机没 pip 装、site-packages
    # 被移除时 `py -c import ivyea_agent` 会失败返回空 → 误判"有更新"）。优先用它。
    try:
        cli = _find_ivyea_cli()
        if cli:
            p = subprocess.run([cli, "--version"], text=True, capture_output=True,
                               timeout=15, **no_window_kwargs())
            out = (p.stdout or "").strip()   # "ivyea-agent 1.1.3" → "1.1.3"
            if out:
                return out.split()[-1]
    except Exception:  # noqa: BLE001
        pass
    try:
        p = subprocess.run([py, "-c", "import ivyea_agent, sys; sys.stdout.write(ivyea_agent.__version__)"],
                           text=True, capture_output=True, timeout=15, **no_window_kwargs())
        return (p.stdout or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _run_step(cmd: list[str], timeout: float = 300.0) -> dict[str, Any]:
    try:
        p = subprocess.run(cmd, cwd=str(ops_settings.root_dir), text=True,
                           capture_output=True, timeout=timeout, **no_window_kwargs())
        return {"cmd": " ".join(cmd[:4]), "returncode": p.returncode,
                "stdout": (p.stdout or "")[-1500:], "stderr": (p.stderr or "")[-1500:]}
    except Exception as exc:  # noqa: BLE001
        return {"cmd": " ".join(cmd[:4]), "returncode": -1, "error": str(exc)}


def upgrade_agent(progress=None) -> dict[str, Any]:
    """Update the bundled IvyeaAgent (pip -U from git into its venv) and restart
    the local serve so the new code loads. Returns before/after version + logs.

    progress(phase: str, percent: int) is called at each step so a UI can show a
    progress bar instead of blocking silently."""
    def _p(phase: str, pct: int) -> None:
        if progress:
            try:
                progress(phase, pct)
            except Exception:  # noqa: BLE001
                pass

    _p("preparing", 5)
    # Frozen build: the agent is bundled into this exe, so it updates *with*
    # IvyeaOps — there's nothing to pip-upgrade. Tell the user to update IvyeaOps.
    if getattr(sys, "frozen", False):
        v = _installed_agent_version("") or agent_version()
        _p("done", 100)
        return {"ok": True, "bundled": True, "before": v, "after": v,
                "note": "内置 IvyeaAgent 随 IvyeaOps 一起更新——请用左下角的「更新」升级 IvyeaOps 即可。"}
    cli = _find_ivyea_cli()
    if not cli:
        return {"ok": False, "error": "ivyea CLI 未找到（IvyeaAgent 可能未安装）"}
    py = _venv_python(cli)
    repo = (os.getenv("IVYEA_AGENT_REPO") or "https://github.com/Hector-xue/ivyea-agent.git").strip()
    ref = (os.getenv("IVYEA_AGENT_REF") or "main").strip()
    before = _installed_agent_version(py) or agent_version()
    _p("downloading", 25)
    # 优先用 IvyeaAgent 自己的 updater：`ivyea self update` 会按安装方式选对更新方式——
    # 源码/launcher 安装 → git pull（这台 dev 机就是，pip 装了也不影响运行的源码）；
    # pip/pipx 安装 → 升级包。老版本(无 self update / 未知选项)回退到 pip 装。
    upd = _run_step([cli, "self", "update"], timeout=300.0)
    _out = (upd.get("stdout") or "") + (upd.get("stderr") or "")
    if upd.get("returncode") == 0 or "已是最新" in _out or "更新完成" in _out:
        install = upd
    else:
        # --no-cache-dir + --force-reinstall: pip caches VCS builds → 强制新拉。--no-deps 快。
        install = _run_step([py, "-m", "pip", "install", "--no-cache-dir",
                             "--force-reinstall", "--no-deps", f"git+{repo}@{ref}"])
    _p("restarting", 80)
    _run_step([cli, "self", "service-stop"], timeout=20.0)   # stop old serve
    restart = start_local_service()                          # start fresh (new code)
    # Read the *installed* version (reflects the files pip just wrote), not the
    # serve's /health — the serve restart can lag on Windows and report the old
    # version, which previously made a real update look like "已是最新".
    after = _installed_agent_version(py) or agent_version()
    ok = install.get("returncode") == 0
    _p("done" if ok else "error", 100)
    return {"ok": ok, "before": before, "after": after, "install": install,
            "restart": restart,
            "note": "" if ok else "升级失败，请查看 install.stderr 或在终端手动 pip 升级。"}


def _agent_is_editable() -> bool:
    """True when IvyeaAgent runs from a source checkout (pip install -e / dev),
    where auto-upgrading would clobber the developer's working tree."""
    try:
        import ivyea_agent
        p = str(Path(ivyea_agent.__file__).resolve())
        return "site-packages" not in p and "dist-packages" not in p
    except Exception:  # noqa: BLE001
        return False


def maybe_sync_agent_on_upgrade() -> None:
    """When IvyeaOps boots on a NEW version, refresh the bundled IvyeaAgent once
    (best-effort, background). The agent is pip-installed @main at IvyeaOps
    install time and otherwise never moves with IvyeaOps updates; this keeps them
    in sync. Skipped for editable/source installs and when auto-start is off."""
    from app.core import hub_settings
    from app.core.version import app_version
    configured = hub_settings.get("ivyea_agent_auto_start")
    auto = configured if isinstance(configured, bool) else \
        os.getenv("IVYEA_AGENT_AUTO_START", "1").lower() not in {"0", "false", "no"}
    # Frozen build: the agent is bundled and updates with IvyeaOps — nothing to pip.
    if not auto or _agent_is_editable() or getattr(sys, "frozen", False):
        return
    cur = app_version()
    if cur in ("", "dev"):
        return
    marker = ops_settings.data_dir / "agent_sync.json"
    try:
        last = json.loads(marker.read_text(encoding="utf-8")).get("ops_version", "") if marker.exists() else ""
    except Exception:  # noqa: BLE001
        last = ""
    if cur == last:
        return  # already synced for this IvyeaOps version

    def _bg() -> None:
        try:
            res = upgrade_agent()
            # 原子落盘：write_text 是"先截断再写"，中间那一瞬文件已经存在但内容是空的。
            # 并发的读方（另一次启动、或紧接着的一次调用）此时读到空串会当作"没同步过"
            # 而重跑一遍 pip 安装。先写临时文件再 rename，读方要么看到旧的、要么看到
            # 完整的新的。（agent 侧 sessions.py 早就是这么写的。）
            tmp = marker.with_suffix(marker.suffix + ".tmp")
            tmp.write_text(json.dumps({"ops_version": cur, "agent": res.get("after", "")}),
                           encoding="utf-8")
            os.replace(tmp, marker)
            logger.info("agent auto-sync on %s: %s->%s ok=%s",
                        cur, res.get("before"), res.get("after"), res.get("ok"))
        except Exception as e:  # noqa: BLE001
            logger.warning("agent auto-sync failed: %s", e)

    threading.Thread(target=_bg, daemon=True).start()


def ensure_available() -> dict[str, Any]:
    global _LAST_START_ATTEMPT
    current = availability()
    if current.get("available"):
        try:
            sync_model_settings()
        except IvyeaAgentError:
            pass
        current["auto_start"] = {"attempted": False, "reason": "already_available"}
        return current
    from app.core import hub_settings
    configured_auto = hub_settings.get("ivyea_agent_auto_start")
    auto_start = configured_auto if isinstance(configured_auto, bool) else os.getenv("IVYEA_AGENT_AUTO_START", "1").lower() not in {"0", "false", "no"}
    if not auto_start:
        current["auto_start"] = {"attempted": False, "reason": "disabled"}
        return current
    now = time.time()
    if now - _LAST_START_ATTEMPT < AUTOSTART_COOLDOWN_SECONDS:
        current["auto_start"] = {"attempted": False, "reason": "cooldown"}
        return current
    _LAST_START_ATTEMPT = now
    started = start_local_service()
    refreshed = availability()
    if refreshed.get("available"):
        try:
            sync_model_settings()
        except IvyeaAgentError:
            pass
    refreshed["auto_start"] = {"attempted": True, "result": started}
    return refreshed


def bootstrap() -> dict[str, Any]:
    return request_json("GET", "/v1/system/bootstrap")


def manifest() -> dict[str, Any]:
    return request_json("GET", "/v1/manifest")


def chat(payload: dict[str, Any]) -> dict[str, Any]:
    # 复杂运营任务一轮可跑 10 分钟以上（多次模型往返 + 慢 MCP 工具），180s 会把
    # 仍在健康执行的轮次掐断——serve 端独立跑完落盘，用户却看到"超时"。
    return request_json("POST", "/v1/chat", payload, timeout=max(_timeout(), 900.0))


def chat_stream(payload: dict[str, Any]) -> Any:
    # 这是"单次 read 无字节"的静默超时：单个慢工具（如市场调研 MCP）可能几分钟
    # 不产出任何 SSE 字节。serve 端已加 15s 心跳；900s 是老版本 serve 的兜底。
    return request_stream("POST", "/v1/chat/stream", payload, timeout=max(_timeout(), 900.0))


def chat_available() -> bool:
    """True when the local IvyeaAgent service answers /v1/health.

    Used by the knowledge-base chat to decide whether to route a turn through
    the governed IvyeaAgent brain (its built-in Amazon knowledge base) instead
    of the legacy Hermes/global fallback.
    """
    try:
        return bool(availability().get("available"))
    except Exception:  # noqa: BLE001 — availability must never raise into chat
        return False


def chat_stream_events(payload: dict[str, Any]) -> Any:
    """Yield (event_name, data_dict) parsed from the /v1/chat/stream SSE bytes.

    The IvyeaAgent service writes frames as ``event: <name>\\n data: <json>\\n\\n``
    (see ivyea_agent/service.py:_sse_send). Heartbeat/comment lines (``:`` …) and
    blank frames are skipped. This is a blocking generator (stdlib urllib); call
    it from a worker thread when driving an async SSE response.
    """
    buffer = b""
    for chunk in chat_stream(payload):
        if not chunk:
            continue
        buffer += chunk
        while b"\n\n" in buffer:
            frame, buffer = buffer.split(b"\n\n", 1)
            event = "message"
            data_lines: list[str] = []
            for line in frame.split(b"\n"):
                if line.startswith(b"event:"):
                    event = line[6:].strip().decode("utf-8", "replace")
                elif line.startswith(b"data:"):
                    data_lines.append(line[5:].strip().decode("utf-8", "replace"))
            if not data_lines:
                continue
            raw = "\n".join(data_lines)
            try:
                data = json.loads(raw)
            except Exception:  # noqa: BLE001 — tolerate non-JSON payloads
                data = {"text": raw}
            yield event, data


def chat_sessions(limit: int = 20) -> dict[str, Any]:
    # 上限 100 曾把左栏的分页顶死：ops 明明传 200，拿回来永远只有 100 条，
    # 而磁盘上的会话早超过这个数 —— 第 101 条往后的历史等于不存在。
    # agent 那边是本地文件扫描，实测 162 个会话热态 55ms，放到 500 不成问题。
    safe_limit = max(1, min(int(limit or 20), 500))
    return request_json("GET", f"/v1/chat/sessions?limit={safe_limit}")


def chat_session(session_id: str) -> dict[str, Any]:
    safe_id = urllib.parse.quote(session_id.strip(), safe="")
    return request_json("GET", f"/v1/chat/sessions/{safe_id}")


def chat_session_delete(session_id: str) -> dict[str, Any]:
    safe_id = urllib.parse.quote(session_id.strip(), safe="")
    return request_json("DELETE", f"/v1/chat/sessions/{safe_id}")


def chat_create(payload: dict[str, Any]) -> dict[str, Any]:
    return request_json("POST", "/v1/chat/sessions", payload)


def chat_import(payload: dict[str, Any]) -> dict[str, Any]:
    """Seed an agent session with pre-existing messages (migration, no LLM turn)."""
    return request_json("POST", "/v1/chat/sessions/import", payload)


def chat_permission(payload: dict[str, Any]) -> dict[str, Any]:
    """把一次审批决策回送给 daemon，解开阻塞在该步的轮次。"""
    return request_json("POST", "/v1/chat/permission", payload, timeout=20.0)


def skills() -> dict[str, Any]:
    """agent 侧技能库（内置 skills_builtin + ~/.ivyea/skills）。

    与 IvyeaOps 自己的 Skill 中心（~/.hermes/skills，走 services/skill_repo.py）是
    两个库：这个是 agent 跑一轮时真正能加载进 system prompt 的那套，任务台的
    「技能」选择器要的就是它。
    """
    return request_json("GET", "/v1/skills")


def skills_search(query: str, limit: int = 8) -> dict[str, Any]:
    safe_q = urllib.parse.quote(query.strip(), safe="")
    safe_limit = max(1, min(int(limit or 8), 50))
    return request_json("GET", f"/v1/skills/search?q={safe_q}&limit={safe_limit}")


def model_providers() -> dict[str, Any]:
    return request_json("GET", "/v1/model/providers")


def provider_models(provider_id: str, refresh: bool = False) -> dict[str, Any]:
    suffix = "?refresh=1" if refresh else ""
    safe_id = urllib.parse.quote(provider_id.strip(), safe="")
    return request_json("GET", f"/v1/model/providers/{safe_id}/models{suffix}")


def provider_probe(provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    safe_id = urllib.parse.quote(provider_id.strip(), safe="")
    return request_json("POST", f"/v1/model/providers/{safe_id}/probe", payload)


def configure_model(payload: dict[str, Any]) -> dict[str, Any]:
    return request_json("POST", "/v1/model/configure", payload, timeout=max(_timeout(), 60.0))


def _agent_provider_payload(settings: dict[str, Any]) -> dict[str, Any] | None:
    provider = str(settings.get("ivyea_agent_provider") or "").strip()
    model = str(settings.get("ivyea_agent_model") or "").strip()
    api_key = str(settings.get("ivyea_agent_api_key") or "").strip()
    base_url = str(settings.get("ivyea_agent_base_url") or "").strip()
    if not any((provider, model, api_key, base_url)):
        return None
    if not provider:
        provider = "custom" if base_url else "deepseek"
    payload = {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
    }
    return {k: v for k, v in payload.items() if v not in ("", None)}


def sync_model_settings(settings: dict[str, Any] | None = None, force: bool = False) -> dict[str, Any]:
    """Best-effort push of the IvyeaAgent model slot from Hub Settings."""
    global _LAST_MODEL_SYNC_SIGNATURE
    if settings is None:
        from app.core import hub_settings
        settings = hub_settings.load()
    payload = _agent_provider_payload(settings)
    if not payload:
        return {"ok": True, "skipped": True, "reason": "ivyea_agent_model_unconfigured"}
    signature = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if not force and signature == _LAST_MODEL_SYNC_SIGNATURE:
        return {"ok": True, "skipped": True, "reason": "unchanged"}
    result = configure_model(payload)
    if result.get("ok"):
        _LAST_MODEL_SYNC_SIGNATURE = signature
    return result


def retrieval_status() -> dict[str, Any]:
    return request_json("GET", "/v1/retrieval/status")


def retrieval_embeddings() -> dict[str, Any]:
    return request_json("GET", "/v1/retrieval/embeddings")


def retrieval_sync() -> dict[str, Any]:
    return request_json("POST", "/v1/retrieval/index", {"sync": True})


def knowledge_watchlist() -> dict[str, Any]:
    return request_json("GET", "/v1/knowledge/watchlist")


def knowledge_governance() -> dict[str, Any]:
    return request_json("GET", "/v1/knowledge/governance")


def knowledge_coverage() -> dict[str, Any]:
    return request_json("GET", "/v1/knowledge/coverage")


def knowledge_freshness() -> dict[str, Any]:
    return request_json("GET", "/v1/knowledge/freshness")


def knowledge_quality() -> dict[str, Any]:
    return request_json("GET", "/v1/knowledge/quality", timeout=max(_timeout(), 60.0))


def knowledge_changes(limit: int = 50, status: str = "") -> dict[str, Any]:
    params = {"limit": max(1, min(int(limit or 50), 500))}
    if status:
        params["status"] = status
    return request_json("GET", f"/v1/knowledge/changes?{urllib.parse.urlencode(params)}")


def knowledge_reviews(limit: int = 100, event_id: str = "") -> dict[str, Any]:
    params = {"limit": max(1, min(int(limit or 100), 1000))}
    if event_id:
        params["event_id"] = event_id
    return request_json("GET", f"/v1/knowledge/reviews?{urllib.parse.urlencode(params)}")


def knowledge_publications(limit: int = 100, event_id: str = "") -> dict[str, Any]:
    params = {"limit": max(1, min(int(limit or 100), 1000))}
    if event_id:
        params["event_id"] = event_id
    return request_json("GET", f"/v1/knowledge/publications?{urllib.parse.urlencode(params)}")


def knowledge_versions(card_id: str = "", limit: int = 100) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit}
    if card_id:
        params["card_id"] = card_id
    return request_json("GET", f"/v1/knowledge/versions?{urllib.parse.urlencode(params)}")


def knowledge_version_rollback(payload: dict[str, Any]) -> dict[str, Any]:
    return request_json("POST", "/v1/knowledge/versions/rollback", payload)


def knowledge_evidence(limit: int = 100) -> dict[str, Any]:
    return request_json("GET", f"/v1/knowledge/evidence?{urllib.parse.urlencode({'limit': limit})}")


def knowledge_evidence_schema() -> dict[str, Any]:
    return request_json("GET", "/v1/knowledge/evidence/schema")


def knowledge_evidence_draft(payload: dict[str, Any]) -> dict[str, Any]:
    return request_json("POST", "/v1/knowledge/evidence/draft", payload)


def knowledge_evidence_apply(payload: dict[str, Any]) -> dict[str, Any]:
    return request_json("POST", "/v1/knowledge/evidence/apply", payload, timeout=max(_timeout(), 120.0))


def knowledge_review_change(payload: dict[str, Any]) -> dict[str, Any]:
    signed = dict(payload)
    token = _token()
    if token and signed.get("reviewer_source") == "ops_authenticated_admin":
        timestamp = str(int(time.time()))
        material = "|".join([
            str(signed.get("event_id") or ""),
            str(signed.get("decision") or ""),
            str(signed.get("reviewer") or ""),
            timestamp,
        ])
        signed["identity_assertion"] = {
            "timestamp": timestamp,
            "signature": hmac.new(token.encode("utf-8"), material.encode("utf-8"), hashlib.sha256).hexdigest(),
        }
    signed.pop("identity_verified", None)
    return request_json("POST", "/v1/knowledge/changes/review", signed)


def knowledge_change_packet(event_id: str, card_id: str = "") -> dict[str, Any]:
    safe_event = urllib.parse.quote(str(event_id or ""), safe="")
    query = ""
    if card_id:
        query = "?" + urllib.parse.urlencode({"card_id": card_id})
    return request_json("GET", f"/v1/knowledge/changes/{safe_event}/packet{query}")


def knowledge_change_draft(payload: dict[str, Any]) -> dict[str, Any]:
    return request_json("POST", "/v1/knowledge/changes/draft", payload)


def knowledge_change_apply(payload: dict[str, Any]) -> dict[str, Any]:
    return request_json("POST", "/v1/knowledge/changes/apply", payload, timeout=max(_timeout(), 120.0))


def knowledge_sync(payload: dict[str, Any]) -> dict[str, Any]:
    return request_json("POST", "/v1/knowledge/sync", payload, timeout=max(_timeout(), 120.0))


def knowledge_cards(limit: int = 200) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 200), 1000))
    return request_json("GET", f"/v1/knowledge/cards?limit={safe_limit}")


def knowledge_search(query: str, limit: int = 8) -> dict[str, Any]:
    params = urllib.parse.urlencode({"q": query, "limit": max(1, min(int(limit or 8), 50))})
    return request_json("GET", f"/v1/knowledge/search?{params}")


def knowledge_card(card_id: str) -> dict[str, Any]:
    safe_id = urllib.parse.quote(str(card_id).strip(), safe="")
    return request_json("GET", f"/v1/knowledge/cards/{safe_id}")


def knowledge_card_update(card_id: str, title: str, body: str) -> dict[str, Any]:
    """One-shot edit of a user knowledge card: /v1/knowledge/update/apply builds
    the draft from body and applies it (confirm=True)."""
    payload = {
        "id": card_id,
        "title": title or card_id,
        "body": body,
        "source_type": "user",
        "confirm": True,
        "rebuild": True,
    }
    return request_json("POST", "/v1/knowledge/update/apply", payload, timeout=max(_timeout(), 60.0))


def knowledge_user_card_path(card_id: str) -> str:
    """Resolve a user knowledge card id to its real relative file path (the card
    detail endpoint returns null paths; the files listing carries them)."""
    for row in (knowledge_files(limit=1000).get("cards") or []):
        if row.get("id") == card_id:
            return str(row.get("path") or "")
    return ""


def knowledge_files(limit: int = 500) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 500), 1000))
    return request_json("GET", f"/v1/knowledge/files?limit={safe_limit}")


def knowledge_uploads(limit: int = 50) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 50), 200))
    return request_json("GET", f"/v1/knowledge/uploads?limit={safe_limit}")


def knowledge_file(path: str) -> dict[str, Any]:
    params = urllib.parse.urlencode({"path": path})
    return request_json("GET", f"/v1/knowledge/file?{params}")


def knowledge_delete_file(path: str) -> dict[str, Any]:
    params = urllib.parse.urlencode({"path": path})
    return request_json("DELETE", f"/v1/knowledge/file?{params}")


def knowledge_update_draft(payload: dict[str, Any]) -> dict[str, Any]:
    return request_json("POST", "/v1/knowledge/update/draft", payload)


def knowledge_update_apply(payload: dict[str, Any]) -> dict[str, Any]:
    return request_json("POST", "/v1/knowledge/update/apply", payload)


def knowledge_upload(payload: dict[str, Any]) -> dict[str, Any]:
    return request_json("POST", "/v1/knowledge/upload", payload, timeout=max(_timeout(), 60.0))


def knowledge_upload_apply(payload: dict[str, Any]) -> dict[str, Any]:
    return request_json("POST", "/v1/knowledge/uploads/apply", payload, timeout=max(_timeout(), 60.0))


def _legacy_brain_root() -> str:
    from app.core import hub_settings
    configured = str(hub_settings.get("brain_root") or "").strip()
    if configured:
        return configured
    return os.environ.get("IVYEA_OPS_BRAIN_ROOT") or str(Path.home() / "brain")


def knowledge_import_directory(payload: dict[str, Any]) -> dict[str, Any]:
    root = str(payload.get("root") or "").strip() or _legacy_brain_root()
    body = {
        "root": root,
        "namespace": str(payload.get("namespace") or "gbrain"),
        "confirm": bool(payload.get("confirm")),
        "rebuild": payload.get("rebuild") if isinstance(payload.get("rebuild"), bool) else True,
        "max_files": max(1, min(int(payload.get("max_files") or 1000), 5000)),
        "max_file_bytes": max(1024, min(int(payload.get("max_file_bytes") or 5 * 1024 * 1024), 25 * 1024 * 1024)),
    }
    return request_json("POST", "/v1/knowledge/import-directory", body, timeout=max(_timeout(), 120.0))


def code_bundle(payload: dict[str, Any]) -> dict[str, Any]:
    return request_json("POST", "/v1/code/bundle", payload)


def code_apply_loop(payload: dict[str, Any]) -> dict[str, Any]:
    return request_json("POST", "/v1/code/apply-loop", payload)


def service_status(host: str = "", port: int | None = None) -> dict[str, Any]:
    query = ""
    params: dict[str, str] = {}
    if host:
        params["host"] = host
    if port:
        params["port"] = str(int(port))
    if params:
        query = "?" + urllib.parse.urlencode(params)
    return request_json("GET", f"/v1/system/service/status{query}")


def service_logs(lines: int = 80) -> dict[str, Any]:
    return request_json("GET", f"/v1/system/service/logs?lines={max(1, min(int(lines or 80), 500))}")


def service_start(payload: dict[str, Any]) -> dict[str, Any]:
    return request_json("POST", "/v1/system/service/start", payload)


def service_stop(payload: dict[str, Any]) -> dict[str, Any]:
    return request_json("POST", "/v1/system/service/stop", payload)


def service_autostart(payload: dict[str, Any]) -> dict[str, Any]:
    return request_json("POST", "/v1/system/service/autostart", payload)
