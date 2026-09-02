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
from app.core import secret_env as _secret_env

logger = logging.getLogger("ivyea.services.ivyea_agent")


DEFAULT_BASE_URL = "http://127.0.0.1:8765"
DEFAULT_TIMEOUT_SECONDS = 5.0
AUTOSTART_COOLDOWN_SECONDS = 20.0
_LAST_START_ATTEMPT = 0.0
_LAST_MODEL_SYNC_SIGNATURE = ""
# 本进程是否往 agent 推过一个**非空**视觉槽。用来区分"用户在界面上把槽位删了"
# （该推清除）和"ops 本来就没配过视觉槽"（不该碰 agent 那边，见 sync_model_settings）。
_VISION_SLOT_PUSHED = False


class IvyeaAgentError(RuntimeError):
    """Base error for IvyeaAgent bridge failures."""


class IvyeaAgentUnavailable(IvyeaAgentError):
    """The local IvyeaAgent service is not reachable."""


class IvyeaAgentNotFound(IvyeaAgentError):
    """The agent answered, but the requested resource does not exist (HTTP 404).

    和"服务挂了"必须分开：调用方常写成「agent 出错就回退旧后端」，而 404 是
    **正常答复**——"这张卡不存在"。混在一起会让一次普通的找不到，变成一次
    对已被摘除的旧依赖（GBrain）的调用。
    """


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
        or _secret_env.get("IVYEA_AGENT_TOKEN")
        or _secret_env.get("IVYEA_API_TOKEN")
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
        msg = f"IvyeaAgent HTTP {exc.code}: {detail or exc.reason}"
        # 404 单独成类：它是**正常答复**（"这东西不存在"），不是故障。调用方普遍
        # 写成「agent 出错就回退旧后端」，混在一起会让一次普通的找不到，变成一次
        # 对已摘除依赖的调用。
        if exc.code == 404:
            raise IvyeaAgentNotFound(msg) from exc
        raise IvyeaAgentError(msg) from exc
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
        # 下面 stdout 接的是 DEVNULL —— 不是控制台，Windows 就会按系统代码页(GBK)
        # 编码，agent 的中文开场白直接编不出来、serve 崩在第一行输出上。
        env.setdefault("PYTHONUTF8", "1")
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
        logger.debug("_find_ivyea_cli 失败（旁路，已忽略）", exc_info=True)
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
                logger.debug("progress 失败（旁路，已忽略）", exc_info=True)

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
    # 装 release tag，**不装 main**。「有新版本」的提示比的就是 release tag
    # （agent_update_available → latest_agent_version），装 main 会让提示和实际
    # 装到的东西对不上，还会把未发布代码推给用户。release.yml 同一策略。
    ref = (os.getenv("IVYEA_AGENT_REF") or "").strip() or latest_agent_version()
    if not ref:
        return {"ok": False, "error": "agent_release_unresolved",
                "note": "取不到 IvyeaAgent 的最新 release，已中止更新。"
                        "这里**故意不回退到 main** —— 那会装上未发布代码，而且和"
                        "「有新版本」的提示对不上（提示比的是 release tag）。"
                        "请检查网络后重试，或设置 IVYEA_AGENT_REF 指定版本。"}
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
        # --no-cache-dir + --force-reinstall: pip caches VCS builds → 强制新拉。
        #
        # **不再加 --no-deps**（原来是为了快）：版本之间会新增依赖——v1.13.0 就加了
        # Pillow 和 rapidocr-onnxruntime。跳过依赖的话，升级"成功"了但新功能
        # 装完即坏，而且坏得很安静（本地视觉探测到缺 Pillow 就直接判不可用）。
        # 带 [feishu] extra：飞书接收端的 SDK。少了它，用户配完飞书、卡片也收到了，
        # 一点按钮什么都不发生 —— 而且不报错。装了它，接收端跟着 serve 自动起来。
        install = _run_step([py, "-m", "pip", "install", "--no-cache-dir",
                             "--force-reinstall",
                             f"ivyea-agent[feishu] @ git+{repo}@{ref}"], timeout=900.0)
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
            logger.debug("sync_model_settings 失败（旁路，已忽略）", exc_info=True)
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
            logger.debug("sync_model_settings 失败（旁路，已忽略）", exc_info=True)
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


def _paging_int(value: Any, default: int) -> int:
    """转不成整数就回默认值。

    不是过度防御：这个函数既被 HTTP 路由调用（那条路 FastAPI 已经校验过），也被
    ops 内部直接调用 —— 直接调用时拿到的是路由签名里的 `Query(...)` 默认对象，
    int() 它会直接抛 TypeError，表现为"打开会话 500"。
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def chat_session(session_id: str, turns: Any = 8, before: Any = None) -> dict[str, Any]:
    """历史会话详情，按**轮**分页（agent ≥ v1.10.3；老 agent 忽略这两个参数）。"""
    safe_id = urllib.parse.quote(session_id.strip(), safe="")
    query = f"?turns={max(1, min(_paging_int(turns, 8), 100))}"
    if before is not None:
        query += f"&before={max(0, _paging_int(before, 0))}"
    return request_json("GET", f"/v1/chat/sessions/{safe_id}{query}")


def chat_session_live(session_id: str, from_seq: int = 0) -> Any:
    """接进这条会话**正在跑的那一轮**：先回放已发生的事件，再实时跟随。

    这是"切走再回来 / 刷新 / 换台机器还能看到进度"的那条路。轮次本身跟这条连接
    没有关系（agent 侧独立跑），所以随便接随便断。

    超时必须给足：这条连接要挂到轮次结束，而一轮可以跑几十分钟。agent 每 15 秒
    发一次 SSE 注释保活，所以"静默超时"不会误伤。
    """
    safe_id = urllib.parse.quote(session_id.strip(), safe="")
    return request_stream("GET", f"/v1/chat/sessions/{safe_id}/live?from={max(0, int(from_seq or 0))}",
                          timeout=max(_timeout(), 3600.0))


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


def chat_inject(payload: dict[str, Any]) -> dict[str, Any]:
    """把一条追加指令投进**正在跑的那一轮**（agent ≥ v1.16.0）。

    回包里的 `accepted` 才是答案：没有活轮时 agent 明确不收（accepted=false），
    调用方据此把这句话当成下一轮发出去 —— "这句话到底进没进去"必须有个准信。
    """
    return request_json("POST", "/v1/chat/inject", payload, timeout=20.0)


def chat_question(payload: dict[str, Any]) -> dict[str, Any]:
    """回送一次选项卡的选择，解开阻塞在 ask_user_question 上的那一步。"""
    return request_json("POST", "/v1/chat/question", payload, timeout=20.0)


def chat_cancel(payload: dict[str, Any]) -> dict[str, Any]:
    """真的停掉这条会话正在跑的那一轮（agent ≥ v1.16.0）。

    回包里的 `cancelled` 才是答案：False = 这条会话本来就没有在跑的轮次。
    老 agent 没有这个端点 —— 调用方拿到 404/502 时要照实说"停不掉"，
    绝不能显示"已停止"却其实什么都没停。
    """
    return request_json("POST", "/v1/chat/cancel", payload, timeout=20.0)


def chat_live_sessions() -> dict[str, Any]:
    """此刻真的有一轮在跑的会话（agent ≥ v1.16.0）。左栏的闪烁标记读它。

    读的是 agent 的内存态，不扫会话文件 —— 这个接口会被几秒问一次。
    老 agent 没有这个端点：调用方拿到 None 时**不要**当成"一条都没在跑"，
    那会让正在执行的会话看着像已经停了；应该退回"不显示这个标记"。
    """
    return request_json("GET", "/v1/chat/live-sessions", timeout=5.0)


def pending_permissions() -> list[str] | None:
    """agent 此刻**真的还卡在等人点**的审批 id（agent ≥ v1.16.1）。

    用来给「待审批」页对账：我们自己那张表是流水账，只有决策/超时帧回到 ops 才销账，
    所以页面关掉、断网、agent 重启之后，早就作废的那一步会永远挂在待审批里。

    拿不到就返回 None（老 agent 没这个端点、或者 agent 没起）—— 调用方据此退回按
    时间兜底，绝不能把"问不到"当成"一条都不在等"，那会把真正等着的审批一把清掉。
    """
    try:
        data = request_json("GET", "/v1/chat/permissions/pending", timeout=5.0)
    except (IvyeaAgentUnavailable, IvyeaAgentNotFound, IvyeaAgentError):
        return None            # 问不到 ≠ 没有
    rows = data.get("pending")
    return [str(x) for x in rows] if isinstance(rows, list) else None


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


# ── 订阅制 provider 的登录（agent ≥ v1.15.5）────────────────────────────────
# Claude 订阅 / Codex / Gemini / Qwen / Copilot 不是填 key 而是走 OAuth，此前只有
# agent 的 CLI 能做。这里只做透传：凭据全程留在 agent 那边，ops 不落盘、不回显。

def auth_status() -> dict[str, Any]:
    return request_json("GET", "/v1/auth")


def auth_start(provider_id: str) -> dict[str, Any]:
    safe = urllib.parse.quote(provider_id.strip(), safe="")
    # 要真发一次外网请求去要设备码/授权链接，默认 8 秒常常不够。
    return request_json("POST", f"/v1/auth/{safe}/start", {}, timeout=max(_timeout(), 40.0))


def auth_poll(provider_id: str, session: str) -> dict[str, Any]:
    safe = urllib.parse.quote(provider_id.strip(), safe="")
    return request_json("POST", f"/v1/auth/{safe}/poll", {"session": session},
                        timeout=max(_timeout(), 40.0))


def auth_complete(provider_id: str, session: str, value: str) -> dict[str, Any]:
    safe = urllib.parse.quote(provider_id.strip(), safe="")
    return request_json("POST", f"/v1/auth/{safe}/complete",
                        {"session": session, "value": value}, timeout=max(_timeout(), 60.0))


def auth_logout(provider_id: str) -> dict[str, Any]:
    safe = urllib.parse.quote(provider_id.strip(), safe="")
    return request_json("POST", f"/v1/auth/{safe}/logout", {}, timeout=max(_timeout(), 30.0))


def model_catalog(payload: dict[str, Any]) -> dict[str, Any]:
    """任意 OpenAI 兼容端点的模型清单（agent ≥ v1.15.4）。

    provider_models() 只认 agent 内置 provider 表里那几家、密钥也只从 agent 自己的
    .env 取；ops 的视觉槽/生图槽常指向内置表里没有的中转商，密钥又存在 ops 这边。
    """
    # 取清单要真发一次外网请求，默认 8 秒常常不够（中转商动辄 3~5 秒）。
    return request_json("POST", "/v1/model/catalog", payload, timeout=max(_timeout(), 20.0))


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


def configure_vision(payload: dict[str, Any]) -> dict[str, Any]:
    return request_json("POST", "/v1/config/vision", payload, timeout=max(_timeout(), 30.0))


def _agent_vision_payload(settings: dict[str, Any]) -> dict[str, Any]:
    """把 Hub Settings 的独立视觉槽组成 agent 的 /v1/config/vision 入参。

    这是 agent 视觉三档里 T2（旁路代读）的模型来源。下推而不是让 agent 自己配，
    是为了让用户在 IvyeaOps 界面配一次就同时对网页和 CLI 生效——两边各配一份
    必然长期不一致。

    槽位为空时返回 {"model": ""}，agent 端把空 model 当作**清除**。但要不要真把这个
    清除推出去，由 sync_model_settings 判断——见那里的"绝不主动清除"。

    优先级与 `ai_synthesis_service._assistant_vision_cfg()` 保持一致：
    独立视觉槽 > 全局兜底槽（用 assistant_vision_model，退而用 assistant_model）。
    只读 vision_* 四个键会漏掉一大批只配了全局兜底的存量用户——他们明明有可用的
    视觉模型，agent 却还停在 T3。

    这里按**传入的 settings** 解析而不是直接调那个函数：sync_model_settings 允许
    调用方显式传一份 settings（同步前预演、测试都用这条路），复用那个函数会让它
    绕过入参去读全局配置，同步的就不是调用方给的那一份了。
    """
    def _pick(provider_key: str, key_key: str, base_key: str, model_key: str) -> dict[str, Any] | None:
        provider = str(settings.get(provider_key) or "").strip().lower()
        api_key = str(settings.get(key_key) or "").strip()
        base_url = str(settings.get(base_key) or "").strip()
        model = str(settings.get(model_key) or "").strip()
        if not api_key or not model:
            return None
        return {
            "provider": provider or ("custom" if base_url else ""),
            "model": model,
            "base_url": base_url,
            "api_key": api_key,
        }

    slot = _pick("vision_provider", "vision_api_key", "vision_base_url", "vision_model")
    if slot:
        return slot
    fallback = _pick("assistant_provider", "assistant_api_key", "assistant_base_url",
                     "assistant_vision_model")
    if fallback:
        return fallback
    fallback = _pick("assistant_provider", "assistant_api_key", "assistant_base_url",
                     "assistant_model")
    return fallback or {"model": ""}


def sync_model_settings(settings: dict[str, Any] | None = None, force: bool = False) -> dict[str, Any]:
    """Best-effort push of the IvyeaAgent model slot from Hub Settings.

    连同**视觉槽**一起推：主脑和视觉模型是同一次配置动作的两半，分两条路同步
    必然出现"主脑换了、视觉槽还是旧的"。
    """
    global _LAST_MODEL_SYNC_SIGNATURE
    if settings is None:
        from app.core import hub_settings
        settings = hub_settings.load()
    payload = _agent_provider_payload(settings)
    vision_payload = _agent_vision_payload(settings)
    if not payload and not vision_payload:
        return {"ok": True, "skipped": True, "reason": "ivyea_agent_model_unconfigured"}

    signature = json.dumps({"model": payload, "vision": vision_payload},
                           ensure_ascii=False, sort_keys=True)
    if not force and signature == _LAST_MODEL_SYNC_SIGNATURE:
        return {"ok": True, "skipped": True, "reason": "unchanged"}

    result: dict[str, Any] = {"ok": True}
    if payload:
        result = configure_model(payload)

    # **绝不主动清除**：ops 这边没配视觉槽时，不代表 agent 那边也不该有——CLI 用户
    # 完全可能自己 `ivyea config set vision_slot`。无脑推一条空 model 会把它悄悄
    #清掉，用户只会看到"某天起视觉突然降级了"，且毫无线索。
    # 只有本进程确实推过一个非空槽位、之后又被清空（= 用户在界面上删了它），
    # 才把清除推下去。
    global _VISION_SLOT_PUSHED
    has_slot = bool(vision_payload.get("model"))
    if has_slot or _VISION_SLOT_PUSHED:
        try:
            result["vision"] = configure_vision(vision_payload)
            _VISION_SLOT_PUSHED = has_slot
        except IvyeaAgentError as exc:
            # 老版本 serve 没有 /v1/config/vision。视觉槽推不过去不该让主脑同步
            # 一起失败——主脑同步是每次 ensure_available 都跑的关键路径。
            logger.debug("configure_vision 失败（旁路，已忽略）：%s", exc)
            result["vision"] = {"ok": False, "error": str(exc)}
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
    """按 id 取一张知识卡的原文。

    v1.13.5 起还供任务台的「引用来源 → 查看原文」用：结尾「引用知识」里，官方卡带
    真实 https 地址（点了跳外网），而治理卡与用户上传的文档**没有外网原文** ——
    它们的地址是 `ivyea://knowledge/<id>` 这类内部协议，前端出于安全只放行 http(s)
    （防 `javascript:` 伪协议），于是成了点不动的死文本。原文一直在系统里，
    这个函数把它取出来。（此前只有 service 有，路由层没暴露。）
    """
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


def files_extract(payload: dict[str, Any]) -> dict[str, Any]:
    """只抽正文，**不进知识库**（agent ≥ v1.16.2）。

    ops 自己不抽是有原因的：它只装了 pypdf/openpyxl，没有 python-docx，自己抽会
    漏 docx；而且两边各写一套抽取逻辑，同一份文件读出来的字迟早会不一样。
    """
    return request_json("POST", "/v1/files/extract", payload, timeout=max(_timeout(), 60.0))


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


# ── 飞书：一处配置，四条链路 ────────────────────────────────────────────────
# IvyeaOps 的系统配置页是**唯一的填写入口**，写完两边各存一份：
#   · hub_settings.json  → CPU/服务器告警（scripts/cpu_alert.py，cron 里跑）
#   · agent ~/.ivyea     → 巡检卡片 / 审批回调 / 飞书对话 / relay 白名单
# 故意不让前者去读后者：cpu_alert 是**看门狗**，agent 挂了、8765 不通了它还得
# 能把消息发出去。看门狗依赖被看的那个进程的配置，等于在最需要报警时没有报警。

def feishu_status(probe: bool = False) -> dict[str, Any]:
    return request_json("GET", "/v1/config/feishu" + ("?probe=1" if probe else ""),
                        timeout=max(_timeout(), 30.0) if probe else None)


def configure_feishu(payload: dict[str, Any]) -> dict[str, Any]:
    return request_json("POST", "/v1/config/feishu", payload, timeout=max(_timeout(), 20.0))


def feishu_action(payload: dict[str, Any]) -> dict[str, Any]:
    """向导里的辅助动作：test / chats / members / patrol。"""
    return request_json("POST", "/v1/config/feishu/action", payload,
                        timeout=max(_timeout(), 30.0))


def _agent_feishu_payload(settings: dict[str, Any]) -> dict[str, Any]:
    """把 Hub Settings 的飞书那一组组成 agent 的 /v1/config/feishu 入参。

    **空值不下推**（agent 侧同样按"空 = 不动"处理）：界面上没填的框会老实传空串，
    把它当"清除"就会出现「打开系统配置、什么都没改、保存一下飞书就瞎了」。
    白名单和巡检任务不在这里——它们只存在 agent 一侧，由界面直接调 action 端点维护，
    存两份必然长期不一致。
    """
    pairs = {
        "app_id": settings.get("alert_app_id"),
        "app_secret": settings.get("alert_app_secret"),
        "chat_id": settings.get("alert_chat_id"),
        "domain": settings.get("alert_feishu_domain"),
        "webhook_url": settings.get("alert_webhook"),
    }
    return {k: str(v).strip() for k, v in pairs.items() if str(v or "").strip()}


def sync_feishu_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """把飞书凭据推给 agent。失败只记日志——保存设置这件事不该被 agent 拖垮。"""
    if settings is None:
        from app.core import hub_settings
        settings = hub_settings.load()
    payload = _agent_feishu_payload(settings)
    if not payload:
        return {"ok": True, "skipped": True, "reason": "feishu_unconfigured"}
    try:
        return configure_feishu(payload)
    except IvyeaAgentError as exc:
        # 老版本 serve 没有 /v1/config/feishu；CPU 告警那条链路不受影响，照常保存。
        logger.debug("configure_feishu 失败（旁路，已忽略）：%s", exc)
        return {"ok": False, "error": str(exc)}


# ── 亚马逊官方 API（SP-API / Ads API）────────────────────────────────────────
# 凭据只存 IvyeaAgent 一侧（~/.ivyea/.env），ops 不留副本：与飞书那组不同，
# 这里没有"agent 挂了也要能用"的场景 —— 取数本来就是 agent 干的活。
# 存两份的唯一后果是长期不一致。

def amazon_status() -> dict[str, Any]:
    return request_json("GET", "/v1/config/amazon")


def configure_amazon(payload: dict[str, Any]) -> dict[str, Any]:
    return request_json("POST", "/v1/config/amazon", payload, timeout=max(_timeout(), 20.0))


def amazon_action(payload: dict[str, Any]) -> dict[str, Any]:
    """verify 会真的去打亚马逊（换 token + 库存接口 + 广告档案），给足超时。"""
    return request_json("POST", "/v1/config/amazon/action", payload,
                        timeout=max(_timeout(), 90.0))
