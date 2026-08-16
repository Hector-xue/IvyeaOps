"""Per-setting health probes for the /api/settings/test endpoint.

Each handler returns a uniform ``{ok, detail}`` dict. Probes are
lightweight (HEAD or a single low-cost API call) and bounded by short
timeouts so the UI stays responsive even when a target is down.

The autodetect() helper at the bottom scans the host for common
integration paths and returns suggestions the user can apply with one
click — saves them from typing paths by hand.
"""
from __future__ import annotations

import asyncio
import logging
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("ivyea.services.settings_test")

_WINDOWS = sys.platform == "win32"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Generous enough for slow/proxied client networks (Windows behind Clash/VPN can
# take >8s to reach api.apimart.ai etc.). A dead host still fails fast at connect.
_DEFAULT_TIMEOUT = 20.0


def _ok(detail: str = "可用") -> Dict[str, Any]:
    return {"ok": True, "detail": detail}


def _err(detail: str) -> Dict[str, Any]:
    return {"ok": False, "detail": detail}


def _hub_get(key: str) -> str:
    """Read a hub_setting (synchronous, falls back to env)."""
    from app.core import hub_settings as _hs
    v = _hs.get(key)
    return str(v) if v is not None else ""


# ---------------------------------------------------------------------------
# Probe handlers
# ---------------------------------------------------------------------------

async def _probe_apimart(key: str) -> Dict[str, Any]:
    if not key:
        return _err("未填写")
    base = (_hub_get("apimart_base") or "https://api.apimart.ai/v1").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as c:
            r = await c.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
        if r.status_code == 200:
            try:
                data = r.json()
                models = data.get("data") or data.get("models") or []
                n = len(models) if isinstance(models, list) else 0
                return _ok(f"密钥有效（可见 {n} 个模型）" if n else "密钥有效")
            except Exception:
                return _ok("HTTP 200")
        if r.status_code in (401, 403):
            return _err(f"鉴权失败 (HTTP {r.status_code})，检查密钥是否过期或额度耗尽")
        return _err(f"HTTP {r.status_code}：{r.text[:120]}")
    except httpx.ConnectError:
        return _err("连接失败（DNS / 网络 / 防火墙）")
    except httpx.TimeoutException:
        return _err(f"超时（>{_DEFAULT_TIMEOUT}s）")
    except Exception as e:
        return _err(f"异常：{e}")


async def _probe_apimart_base(url: str) -> Dict[str, Any]:
    if not url:
        return _err("未填写")
    if not url.startswith("http"):
        return _err("应以 http(s):// 开头")
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as c:
            r = await c.get(url.rstrip("/") + "/models", headers={"Authorization": "Bearer test"})
        # Even 401 means the endpoint is reachable and OpenAI-compatible.
        if r.status_code in (200, 401, 403):
            return _ok(f"端点可达 (HTTP {r.status_code})")
        return _err(f"HTTP {r.status_code}")
    except Exception as e:
        return _err(f"无法连接：{e}")


async def _probe_sorftime(key: str) -> Dict[str, Any]:
    if not key:
        return _err("未填写")
    # Use MCP tools/list as a low-cost auth check.
    url = f"https://mcp.sorftime.com?key={key}"
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as c:
            r = await c.post(url, json=payload, headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            })
        if r.status_code != 200:
            return _err(f"HTTP {r.status_code}")
        # Sorftime returns SSE: parse last data line
        body = None
        for line in r.text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                try:
                    body = json.loads(line[5:].strip())
                except Exception:
                    logger.debug("json.loads 失败（旁路，已忽略）", exc_info=True)
        if body is None:
            return _err("响应解析失败")
        if "error" in body:
            return _err(f"鉴权失败：{body['error']}")
        tools = body.get("result", {}).get("tools", [])
        return _ok(f"密钥有效（{len(tools)} 个工具可用）")
    except Exception as e:
        return _err(str(e)[:200])


async def _probe_sif(key: str) -> Dict[str, Any]:
    if not key:
        return _err("未填写")
    try:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as c:
            r = await c.post("https://mcp.sif.com/mcp", json=payload, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
                "Accept": "application/json, text/event-stream",
            })
        if r.status_code == 401:
            return _err("密钥无效")
        if r.status_code != 200:
            return _err(f"HTTP {r.status_code}")
        body = None
        for line in r.text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                try:
                    body = json.loads(line[5:].strip())
                except Exception:
                    logger.debug("json.loads 失败（旁路，已忽略）", exc_info=True)
        if body is None:
            try:
                body = r.json()
            except Exception:
                return _err("响应解析失败")
        if "error" in body:
            return _err(f"鉴权失败：{body['error']}")
        tools = body.get("result", {}).get("tools", [])
        return _ok(f"SIF 密钥有效（{len(tools)} 个工具）")
    except Exception as e:
        return _err(str(e)[:200])


async def _probe_sellersprite(key: str) -> Dict[str, Any]:
    if not key:
        return _err("未填写")
    # SellerSprite's open platform is an MCP server (not REST) — connect the same
    # way the data pipeline does (mcp.sellersprite.com/mcp?secret-key=...), and
    # confirm tools/list answers. (The REST endpoint returns HTTP 200 even when
    # unauthorized, so checking the MCP is both correct and matches real usage.)
    from app.services.sellersprite_service import parse_sse
    url = f"https://mcp.sellersprite.com/mcp?secret-key={key}"
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as c:
            await c.post(url, headers=headers, json={
                "jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "IvyeaOps", "version": "1.0"}}})
            r = await c.post(url, headers=headers,
                             json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        body = parse_sse(r.text)
        tools = (body.get("result") or {}).get("tools") or []
        names = [t.get("name") for t in tools if isinstance(t, dict)]
        # An invalid key still answers tools/list — but with a single
        # `secret_invalid`("密钥不合法") tool instead of the real toolset.
        if "secret_invalid" in names:
            return _err("密钥不合法（卖家精灵 MCP 拒绝该 key）")
        if tools:
            return _ok(f"MCP 已连接（{len(tools)} 个工具可用）")
        if body.get("error"):
            return _err(f"MCP 错误：{str(body['error'])[:150]}")
        return _err("MCP 未返回工具，请检查 key 或开通的接口权限")
    except Exception as e:  # noqa: BLE001
        return _err(str(e)[:200])


async def _probe_openai(key: str) -> Dict[str, Any]:
    if not key:
        return _err("未填写")
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as c:
            r = await c.get("https://api.openai.com/v1/models",
                            headers={"Authorization": f"Bearer {key}"})
        if r.status_code == 200:
            return _ok("密钥有效")
        if r.status_code in (401, 403):
            return _err(f"鉴权失败 (HTTP {r.status_code})")
        return _err(f"HTTP {r.status_code}")
    except Exception as e:
        return _err(f"调用失败：{e}")


async def _probe_url(url: str, *, label: str = "URL") -> Dict[str, Any]:
    if not url:
        return _err("未填写")
    if not url.startswith(("http://", "https://")):
        return _err("应以 http(s):// 开头")
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT, follow_redirects=True) as c:
            r = await c.get(url)
        # Any sub-500 code means the server is alive and responding.
        if r.status_code < 500:
            return _ok(f"可达 (HTTP {r.status_code})")
        return _err(f"HTTP {r.status_code}（服务端错误）")
    except httpx.ConnectError:
        return _err("连接被拒绝（服务未启动？）")
    except httpx.TimeoutException:
        return _err(f"超时（>{_DEFAULT_TIMEOUT}s）")
    except Exception as e:
        return _err(f"{label} 检测失败：{e}")


async def _probe_ivyea_agent(url: str) -> Dict[str, Any]:
    if not url:
        return _err("未填写")
    if not url.startswith(("http://", "https://")):
        return _err("应以 http(s):// 开头")
    headers: dict[str, str] = {}
    token = _hub_get("ivyea_agent_token")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as c:
            r = await c.get(url.rstrip("/") + "/health", headers=headers)
        if r.status_code == 200:
            data = r.json()
            version = data.get("version") or ""
            cards = (data.get("knowledge") or {}).get("cards")
            return _ok(f"已连接 IvyeaAgent {version} · 知识卡 {cards}")
        if r.status_code in (401, 403):
            return _err("Token 不正确或服务要求认证")
        return _err(f"HTTP {r.status_code}")
    except Exception as e:
        return _err(f"连接失败：{e}")


def _probe_bin(path: str) -> Dict[str, Any]:
    if not path:
        return _err("未填写")
    p = Path(path)
    if not p.exists():
        # Fall back: try resolving via PATH if just a name was given
        if not p.is_absolute():
            w = shutil.which(path)
            if w:
                return _ok(f"通过 PATH 找到：{w}")
        return _err("文件不存在")
    if not p.is_file():
        return _err("不是一个文件")
    # Windows has no executable bit; treat .exe/.cmd/.bat as executable.
    if _WINDOWS:
        executable = p.suffix.lower() in (".exe", ".cmd", ".bat", ".ps1") or not p.suffix
    else:
        executable = os.access(p, os.X_OK)
    if not executable:
        return _err("文件无可执行权限")
    return _ok(str(p))


def _probe_db(path: str) -> Dict[str, Any]:
    if not path:
        return _err("未填写")
    p = Path(path)
    if not p.exists():
        return _err("文件不存在")
    if not p.is_file():
        return _err("不是一个文件")
    try:
        conn = sqlite3.connect(str(p), timeout=2.0)
        try:
            row = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()
            n = row[0] if row else 0
        finally:
            conn.close()
        size = p.stat().st_size
        kb = size / 1024
        return _ok(f"SQLite 有效（{n} 个表，{kb:.1f} KB）")
    except sqlite3.DatabaseError as e:
        return _err(f"不是有效的 SQLite 文件：{e}")
    except Exception as e:
        return _err(f"打开失败：{e}")


def _probe_dir(path: str) -> Dict[str, Any]:
    if not path:
        return _err("未填写")
    p = Path(path).expanduser()
    if not p.exists():
        return _err("目录不存在")
    if not p.is_dir():
        return _err("不是一个目录")
    try:
        items = list(p.iterdir())
        return _ok(f"目录存在（{len(items)} 个条目）")
    except PermissionError:
        return _err("无读取权限")
    except Exception as e:
        return _err(f"读取失败：{e}")


async def _probe_feishu_webhook(url: str) -> Dict[str, Any]:
    """POST a small probe message. The webhook config (keyword / signature)
    may reject it — that's actually still useful info."""
    if not url:
        return _err("未填写")
    if not url.startswith("https://"):
        return _err("应以 https:// 开头")
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as c:
            r = await c.post(url, json={
                "msg_type": "text",
                "content": {"text": f"IvyeaOps 配置测试 · {time.strftime('%H:%M:%S')}"},
            })
        if r.status_code == 200:
            try:
                data = r.json()
                code = data.get("code") or data.get("StatusCode") or 0
                if code in (0, None):
                    return _ok("已发送测试消息，请检查群消息")
                msg = data.get("msg", "") or data.get("StatusMessage", "")
                # Common: code=19021 关键词未命中 / code=11212 签名校验失败
                if "keyword" in msg.lower() or "关键词" in msg:
                    return _err(f"关键词校验未通过：{msg}（请把告警消息会包含的关键词配进群机器人）")
                if "sign" in msg.lower() or "签名" in msg:
                    return _err(f"签名校验未通过：{msg}")
                return _err(f"飞书返回 code={code}：{msg}")
            except Exception:
                return _ok("HTTP 200（响应体非 JSON）")
        return _err(f"HTTP {r.status_code}：{r.text[:120]}")
    except Exception as e:
        return _err(f"请求失败：{e}")


async def _probe_feishu_app() -> Dict[str, Any]:
    """Try to get a tenant_access_token using the configured app_id/secret."""
    app_id = _hub_get("alert_app_id")
    app_secret = _hub_get("alert_app_secret")
    chat_id = _hub_get("alert_chat_id")
    if not app_id or not app_secret:
        return _err("App ID 和 App Secret 都需要填写")
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as c:
            r = await c.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
            )
        if r.status_code != 200:
            return _err(f"HTTP {r.status_code}")
        data = r.json()
        if data.get("code") != 0:
            return _err(f"飞书拒绝：code={data.get('code')} msg={data.get('msg')}")
        token = data.get("tenant_access_token")
        if not token:
            return _err("返回中没有 tenant_access_token")
        if not chat_id:
            return _ok("App 凭证有效（tenant_token 已获取）；未填 Chat ID，无法测试消息发送")
        # Try send a test message to chat_id
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as c:
            r = await c.post(
                "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json; charset=utf-8"},
                json={
                    "receive_id": chat_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": f"IvyeaOps 配置测试 · {time.strftime('%H:%M:%S')}"}, ensure_ascii=False),
                },
            )
        data = r.json()
        if data.get("code") == 0:
            return _ok(f"App 凭证有效，已向 {chat_id} 发送测试消息")
        return _err(f"凭证有效但消息发送失败：code={data.get('code')} msg={data.get('msg')}")
    except Exception as e:
        return _err(f"调用失败：{e}")


# ---------------------------------------------------------------------------
# Public dispatch
# ---------------------------------------------------------------------------

_KNOWN_TEXT_PROVIDERS = {"ivyea-agent", "assistant", "deepseek", "codex", "claude", "hermes", "apimart"}
_KNOWN_VISION_PROVIDERS = {"openai", "assistant", "apimart"}


async def _probe_text_provider(provider: str, label: str) -> Dict[str, Any]:
    """Real round-trip through one configured text provider, so a wrong
    key/model/base is caught here instead of at runtime in a panel."""
    from app.services import ai_synthesis_service
    try:
        text = await asyncio.wait_for(
            ai_synthesis_service.generate_text_provider(provider, "只回复两个字：可用"), timeout=40.0)
        return _ok(f"{label} 可用（返回 {len(text)} 字）") if text.strip() else _err(f"{label} 返回空")
    except asyncio.TimeoutError:
        return _err(f"{label} 超时（>40s）")
    except Exception as e:  # noqa: BLE001
        return _err(f"{label} 不可用：{str(e)[:160]}")


def _validate_provider_list(val: str, known: set, label: str) -> Dict[str, Any]:
    items = [p.strip().lower() for p in (val or "").split(",") if p.strip()]
    if not items:
        return _err("未填写")
    bad = [p for p in items if p not in known]
    if bad:
        return _err(f"未知 provider：{', '.join(bad)}（可用：{', '.join(sorted(known))}）")
    return _ok(f"顺序合法：{' → '.join(items)}")


async def _probe_image_gen(base: str = "") -> Dict[str, Any]:
    """Lightweight reachability check for the image-gen endpoint (custom or
    Apimart). Hits {base}/models — a real generation would cost money/time."""
    base = (base or _hub_get("image_base_url") or _hub_get("apimart_base")
            or "https://api.apimart.ai/v1").strip()
    key = (_hub_get("image_api_key") or _hub_get("apimart_key")).strip()
    if not base:
        return _err("未填写接口地址")
    if not key:
        return _err("未填写 API Key（自定义留空时会复用 Apimart Key）")
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as c:
            r = await c.get(base.rstrip("/") + "/models",
                            headers={"Authorization": f"Bearer {key}"})
        if r.status_code == 200:
            return _ok(f"接口可达（{base}）")
        if r.status_code in (401, 403):
            return _err("Key 不正确或无权限")
        if r.status_code == 404:
            return _ok("接口可达，但无 /models（404）；若支持 /images/generations 仍可用")
        return _err(f"HTTP {r.status_code}")
    except Exception as e:  # noqa: BLE001
        return _err(f"连接失败：{str(e)[:120]}")


async def test_value(key: str, value: Optional[str]) -> Dict[str, Any]:
    """Test one setting. `value` is the in-flight (unsaved) value; when
    None or empty, falls back to the persisted value.

    Provider *bundles* (assistant_*, deepseek, …) are validated by a real
    round-trip through the SAVED config — save the section first, then 测试."""
    val = value.strip() if value else _hub_get(key)

    # Map keys to probes
    if key == "apimart_key":
        return await _probe_apimart(val)
    if key == "apimart_base":
        return await _probe_apimart_base(val)
    if key == "sorftime_key":
        return await _probe_sorftime(val)
    if key == "sif_key":
        return await _probe_sif(val)
    if key == "sellersprite_key":
        return await _probe_sellersprite(val)
    if key == "openai_api_key":
        return await _probe_openai(val)
    if key in ("ivyea_agent_url", "ivyea_agent_token"):
        return await _probe_ivyea_agent(_hub_get("ivyea_agent_url") or "http://127.0.0.1:8765")

    # Text-provider configs → real round-trip through the saved config.
    if key == "deepseek_api_key":
        return await _probe_text_provider("deepseek", "DeepSeek")
    if key in ("assistant_provider", "assistant_model", "assistant_api_key", "assistant_base_url"):
        return await _probe_text_provider("assistant", "全局兜底大模型")
    if key in ("ivyea_agent_provider", "ivyea_agent_model", "ivyea_agent_api_key", "ivyea_agent_base_url"):
        return await _probe_ivyea_agent(_hub_get("ivyea_agent_url") or "http://127.0.0.1:8765")
    if key == "text_ai_providers":
        return _validate_provider_list(val, _KNOWN_TEXT_PROVIDERS, "文本 provider 链")
    if key == "vision_ai_providers":
        return _validate_provider_list(val, _KNOWN_VISION_PROVIDERS, "视觉 provider 链")
    if key == "image_base_url":
        return await _probe_image_gen(base=val)
    if key in ("image_api_key", "image_model"):
        return await _probe_image_gen()

    if key == "imgflow_url":
        # imgflow's root may not respond to GET; try /api/health or /
        if not val:
            return _err("未填写")
        for path in ("/api/health", "/"):
            res = await _probe_url(val.rstrip("/") + path, label="imgflow")
            if res["ok"]:
                return res
        return res  # last one
    if key in ("dashboard_url", "terminal_url"):
        return await _probe_url(val)

    if key == "alert_webhook":
        return await _probe_feishu_webhook(val)
    if key in ("alert_app_id", "alert_app_secret", "alert_chat_id"):
        # Test the whole bundle together — single field alone isn't useful.
        return await _probe_feishu_app()

    if key in ("hermes_bin", "codex_bin", "claude_bin", "kiro_cli_bin"):
        return _probe_bin(val)
    if key.endswith("_db"):
        return _probe_db(val)
    if key.endswith("_dir") or key == "brain_root":
        return _probe_dir(val)
    if key in ("hermes_node_bin", "bun_bin"):
        return _probe_dir(val)

    # 兜底：没有专门在线测试的项也给出清楚结果，不再吓人地报"未知配置项"。
    if not val:
        return _err("未配置（留空）")
    return _ok("已保存（此项无在线测试，保存即生效）")


# 一键自检：对每个已配置项跑真实在线测试，让管理员一眼看清"配了但用不了"的项。
_SELF_CHECK_KEYS = [
    ("apimart_key", "图片生成 Apimart Key"),
    ("apimart_base", "Apimart 地址"),
    ("image_base_url", "自定义生图接口"),
    ("text_ai_providers", "文本 provider 链"),
    ("deepseek_api_key", "DeepSeek Key"),
    ("assistant_api_key", "全局兜底大模型"),
    ("ivyea_agent_url", "IvyeaAgent 本地服务"),
    ("vision_ai_providers", "视觉 provider 链"),
    ("openai_api_key", "OpenAI 视觉 Key"),
    ("sorftime_key", "Sorftime Key"),
    ("sif_key", "SIF Key"),
    ("sellersprite_key", "卖家精灵 Key"),
    ("imgflow_url", "图片处理后端"),
    ("alert_webhook", "告警 Webhook"),
]


async def self_check() -> Dict[str, Any]:
    """Run the real online test for every *configured* item; return a matrix so
    the admin sees green/red per item (no more 'saved but errors at runtime')."""
    async def _one(key: str, label: str) -> Dict[str, Any]:
        if not _hub_get(key):
            return {"key": key, "label": label, "status": "skip", "detail": "未配置"}
        try:
            res = await test_value(key, None)
        except Exception as e:  # noqa: BLE001
            res = _err(str(e)[:160])
        return {"key": key, "label": label,
                "status": "ok" if res.get("ok") else "err", "detail": res.get("detail", "")}

    results = list(await asyncio.gather(*[_one(k, lbl) for k, lbl in _SELF_CHECK_KEYS]))
    return {
        "results": results,
        "ok": sum(1 for r in results if r["status"] == "ok"),
        "err": sum(1 for r in results if r["status"] == "err"),
        "skip": sum(1 for r in results if r["status"] == "skip"),
        "total": len(results),
    }


# ---------------------------------------------------------------------------
# Autodetect: scan known locations for the user's already-installed tools
# ---------------------------------------------------------------------------

def autodetect() -> Dict[str, Any]:
    """Return ``{suggestions: {key: value, ...}, scanned: [...]}`` where
    each suggestion is a path we found on disk. The frontend lets the
    user review and selectively apply them. Only suggests when:
      - the key is currently unset in hub_settings, AND
      - the probed path actually exists.
    """
    from app.core import hub_settings as _hs
    current = _hs.load()
    suggestions: Dict[str, str] = {}
    scanned: list[str] = []
    home = Path.home()

    def _suggest_first(key: str, candidates: list[str]) -> None:
        if current.get(key):
            return
        for c in candidates:
            scanned.append(f"{key}: {c}")
            p = Path(c)
            if p.exists():
                suggestions[key] = str(p)
                return

    # --- CLIs (try PATH lookup then common locations) ---
    for name, key in (("hermes", "hermes_bin"), ("codex", "codex_bin"),
                      ("kiro-cli", "kiro_cli_bin")):
        if current.get(key):
            continue
        w = shutil.which(name)
        if w:
            suggestions[key] = w
            scanned.append(f"{key}: found in PATH at {w}")
        else:
            # Try a few well-known absolute locations
            for c in (f"/usr/local/bin/{name}", f"{home}/.local/bin/{name}",
                      f"{home}/.hermes/node/bin/{name}", f"{home}/.bun/bin/{name}"):
                scanned.append(f"{key}: {c}")
                if Path(c).is_file():
                    suggestions[key] = c
                    break

    # claude: avoid the broken .exe shim, walk for a real platform binary
    if not current.get("claude_bin"):
        # First try the PATH-resolved binary's real target
        w = shutil.which("claude")
        if w:
            real = os.path.realpath(w)
            if Path(real).is_file() and not real.endswith(".exe"):
                suggestions["claude_bin"] = real
                scanned.append(f"claude_bin: resolved from PATH → {real}")
        if "claude_bin" not in suggestions:
            for c in [
                f"{home}/.hermes/node/lib/node_modules/@anthropic-ai/claude-code/node_modules/@anthropic-ai/claude-code-linux-x64/claude",
                "/usr/local/bin/claude",
                "/usr/lib/claude-code/claude",
            ]:
                scanned.append(f"claude_bin: {c}")
                if Path(c).is_file():
                    suggestions["claude_bin"] = c
                    break

    # --- Databases ---
    _suggest_first("hermes_db", [str(home / ".hermes/state.db")])
    _suggest_first("codex_db", [str(home / ".codex/state_5.sqlite")])
    _suggest_first("feishu_codex_db", [str(home / "feishu-codex-relay/.codex-home/state_5.sqlite")])
    _suggest_first("kiro_gateway_db", [str(home / "kiro-gateway/usage.db")])
    _suggest_first("kiro_cli_db", [str(home / ".local/share/kiro-cli/data.sqlite3")])

    # --- Directories ---
    _suggest_first("kiro_cli_sessions_dir", [str(home / ".kiro/sessions/cli")])
    _suggest_first("claude_projects_dir", [str(home / ".claude/projects")])
    _suggest_first("brain_root", [str(home / "brain")])

    # --- PATH augment dirs ---
    _suggest_first("hermes_node_bin", [str(home / ".hermes/node/bin")])
    _suggest_first("bun_bin", [str(home / ".bun/bin")])

    return {"suggestions": suggestions, "scanned": scanned}
