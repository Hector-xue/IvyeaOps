"""自定义 MCP 数据源注册表。

内置的三家（Sorftime / 卖家精灵 / SIF）是写死的服务模块，各有各的工具名和
字段契约。这里管的是**用户自己加的** MCP 数据源：存一份「工具名 + 参数模板 +
字段映射」的配置，运行时由 custom_source_provider 翻译成 ops 内部那套
provider-neutral 的返回结构。

设计约束：
  · 内置三家一行不动 —— 自定义源的 id 一律带 ``custom:`` 前缀，和内置 id
    永不撞名，历史缓存/快照按 data_source 分区也就天然隔离。
  · 配置存 hub_settings 的 ``custom_data_sources``（JSON 字符串）。新加的 key
    默认空数组，老用户升级后行为与升级前完全一致。
  · 密钥不回传前端明文：读接口把 auth.value / headers 的值摘掉，只留
    ``*_set`` 布尔；保存时留空 = 沿用原值（不是清空），否则改个名字就把 key
    冲没了。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

_SETTING_KEY = "custom_data_sources"

PREFIX = "custom:"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

SURFACES = ("home", "market", "playbook")

TRANSPORTS = ("http", "sse")

# "auto" 是**未解析**状态：自动配置会挨个试出真正管用的那种，然后写回具体值。
# 用户界面上没有「鉴权方式」这个选项，就是靠这一档。
AUTH_MODES = ("auto", "none", "query", "header", "bearer")

# 一个数据源能提供的能力。名字与 sellersprite_service 的同名函数逐字对齐 ——
# 那组函数就是 ops 内部的事实契约，新源必须长成一样才能插进现有板块。
CAPABILITIES = (
    "keyword_pipeline",
    "asin_pipeline",
    "home_asin_pulse",
    "home_keyword_pulse",
    "home_keyword_extends",
    "home_keyword_purchase_evidence",
    "home_category",
    "home_market_metrics",
    "home_keyword_trend_series",
    "home_product_trend_series",
)

# 板块要能用起来，至少得有这些能力。**这是推导规则，不是校验规则** ——
# 配了什么能力就在什么板块出现，用户不需要（也不应该）自己勾板块。
SURFACE_REQUIRES: Dict[str, tuple[str, ...]] = {
    "home": ("home_asin_pulse",),
    "market": ("keyword_pipeline",),
    "playbook": ("keyword_pipeline",),
}

SURFACE_LABELS: Dict[str, str] = {
    "home": "首页驾驶舱", "market": "市场调研", "playbook": "打法推荐",
}

# 报错里只许出现这些名字。用户没见过 keyword_pipeline 这种内部标识符，
# 拿它写报错等于什么都没说。
CAPABILITY_LABELS: Dict[str, str] = {
    "keyword_pipeline": "关键词采集",
    "asin_pipeline": "ASIN 采集",
    "home_asin_pulse": "ASIN 监控卡片",
    "home_keyword_pulse": "关键词监控卡片",
    "home_keyword_extends": "拓展词",
    "home_keyword_purchase_evidence": "关键词购买佐证",
    "home_category": "类目大盘",
    "home_market_metrics": "大盘指标",
    "home_keyword_trend_series": "关键词趋势",
    "home_product_trend_series": "ASIN 销量趋势",
}


def cap_label(cap_id: str) -> str:
    return CAPABILITY_LABELS.get(cap_id, cap_id)


def derive_surfaces(capabilities: Dict[str, Any]) -> List[str]:
    """配了什么能力，就在什么板块出现。"""
    return [s for s in SURFACES
            if all(c in capabilities for c in SURFACE_REQUIRES.get(s, ()))]

_MASK = ""     # 回传前端的占位：空串 + *_set 标记


class RegistryError(RuntimeError):
    """配置非法 —— 路由层转 400，不要变成 500。"""


# ── 存取 ─────────────────────────────────────────────────────────────────────

def _crypt_secrets(item: Dict[str, Any], fn) -> Dict[str, Any]:
    """对配置里的凭据字段逐个 encrypt/decrypt。

    hub_settings 是**按字段名**决定加不加密的（core/secrets.is_secret_key），
    而 ``custom_data_sources`` 这个名字不命中 key/secret/token 任何一个词 ——
    整块 JSON 会原样明文落盘。所以密钥的加解密在这里自己收口，别指望上层。
    两处都用同一个 secrets 模块，主密钥丢失时的行为也和其它配置一致。
    """
    out = dict(item)
    auth = dict(out.get("auth") or {})
    if isinstance(auth.get("value"), str):
        auth["value"] = fn(auth["value"])
    out["auth"] = auth
    headers = out.get("headers")
    if isinstance(headers, dict):
        out["headers"] = {k: (fn(v) if isinstance(v, str) else v) for k, v in headers.items()}
    return out


def _load_raw() -> List[Dict[str, Any]]:
    from app.core import hub_settings, secrets as _secrets
    blob = hub_settings.get(_SETTING_KEY) or ""
    if isinstance(blob, list):          # 容忍早期误存成 list 的情况
        raw = [s for s in blob if isinstance(s, dict)]
    else:
        text = str(blob).strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except Exception:               # noqa: BLE001 — 配置损坏时按空处理，别让整页 500
            return []
        raw = [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []
    return [_crypt_secrets(item, _secrets.decrypt) for item in raw]


def _save_raw(sources: List[Dict[str, Any]]) -> None:
    from app.core import hub_settings, secrets as _secrets
    on_disk = [_crypt_secrets(item, _secrets.encrypt) for item in sources]
    hub_settings.save({_SETTING_KEY: json.dumps(on_disk, ensure_ascii=False)})


def _strip_prefix(source_id: str) -> str:
    value = str(source_id or "").strip()
    return value[len(PREFIX):] if value.startswith(PREFIX) else value


def is_custom(source_id: str) -> bool:
    """``source_id`` 是否指向一个**已注册且启用**的自定义源。"""
    return get(source_id) is not None


def full_id(slug: str) -> str:
    return f"{PREFIX}{slug}"


def get(source_id: str, *, include_disabled: bool = False) -> Optional[Dict[str, Any]]:
    """按 id 取配置（明文，含密钥）—— 仅供后端调用方使用。"""
    if not str(source_id or "").startswith(PREFIX):
        return None
    slug = _strip_prefix(source_id)
    for item in _load_raw():
        if item.get("id") == slug and (include_disabled or item.get("enabled", True)):
            return item
    return None


def list_all(*, redact: bool = True) -> List[Dict[str, Any]]:
    items = _load_raw()
    return [_redact(dict(item)) for item in items] if redact else items


# ── 密钥摘除 / 回填 ──────────────────────────────────────────────────────────

def _redact(item: Dict[str, Any]) -> Dict[str, Any]:
    auth = dict(item.get("auth") or {})
    item["auth"] = {**auth, "value": _MASK, "value_set": bool(str(auth.get("value") or "").strip())}
    headers = item.get("headers") or {}
    if isinstance(headers, dict):
        item["headers"] = dict.fromkeys(headers, _MASK)
        item["headers_set"] = sorted(k for k, v in headers.items() if str(v or "").strip())
    return item


def _restore_secrets(incoming: Dict[str, Any], previous: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """留空的密钥字段沿用旧值 —— 前端拿到的是掩码，原样提交不能把 key 抹掉。"""
    if not previous:
        return incoming
    auth = dict(incoming.get("auth") or {})
    if not str(auth.get("value") or "").strip():
        auth["value"] = (previous.get("auth") or {}).get("value", "")
    incoming["auth"] = auth
    old_headers = previous.get("headers") or {}
    headers = dict(incoming.get("headers") or {})
    for key, value in headers.items():
        if not str(value or "").strip() and isinstance(old_headers, dict) and old_headers.get(key):
            headers[key] = old_headers[key]
    incoming["headers"] = headers
    return incoming


# ── 校验 ─────────────────────────────────────────────────────────────────────

def _require(cond: bool, message: str) -> None:
    if not cond:
        raise RegistryError(message)


def _clean_capability(name: str, spec: Any) -> Dict[str, Any]:
    _require(isinstance(spec, dict), f"「{cap_label(name)}」的配置必须是对象")
    spec = dict(spec)
    if name in ("keyword_pipeline", "asin_pipeline"):
        steps = spec.get("steps")
        _require(isinstance(steps, list) and steps, f"「{cap_label(name)}」至少要配 1 个采集步骤")
        cleaned_steps = []
        for index, step in enumerate(steps):
            _require(isinstance(step, dict), f"「{cap_label(name)}」第 {index + 1} 步必须是对象")
            tool = str(step.get("tool") or "").strip()
            _require(bool(tool), f"「{cap_label(name)}」第 {index + 1} 步没填工具名")
            cleaned_steps.append({
                "label": str(step.get("label") or tool).strip(),
                "tool": tool,
                "args": step.get("args") if isinstance(step.get("args"), dict) else {},
            })
        return {"steps": cleaned_steps}
    tool = str(spec.get("tool") or "").strip()
    _require(bool(tool), f"「{cap_label(name)}」没填工具名")
    out: Dict[str, Any] = {
        "tool": tool,
        "args": spec.get("args") if isinstance(spec.get("args"), dict) else {},
    }
    for key in ("record", "rows", "fields", "row_fields", "extra_tool", "extra_args", "summary_fields"):
        if spec.get(key) not in (None, "", {}):
            out[key] = spec[key]
    return out


def validate(item: Dict[str, Any]) -> Dict[str, Any]:
    """规范化 + 校验一份配置，返回可落盘的干净对象。"""
    _require(isinstance(item, dict), "配置必须是对象")
    slug = _strip_prefix(str(item.get("id") or "").strip().lower())
    _require(bool(_SLUG_RE.match(slug)),
                 "标识 id 只能用小写字母、数字、下划线和短横线，1-32 位")

    name = str(item.get("name") or "").strip() or slug
    transport = str(item.get("transport") or "http").strip().lower()
    _require(transport in TRANSPORTS, f"transport 只支持 {'/'.join(TRANSPORTS)}")

    url = str(item.get("url") or "").strip()
    _require(url.startswith("https://") or url.startswith("http://"),
                 "MCP 端点要填完整地址，以 https:// 开头")
    # 端点写成 http 是 ops 自己踩过的坑（密钥会明文过网），这里只警告不拦截：
    # 局域网自建网关确实可能是 http。
    auth_in = item.get("auth") if isinstance(item.get("auth"), dict) else {}
    mode = str(auth_in.get("mode") or "auto").strip().lower()
    _require(mode in AUTH_MODES, "鉴权方式不认识")
    if mode == "query":
        _require(bool(str(auth_in.get("name") or "").strip()), "URL 参数鉴权要填参数名（如 key）")
    if mode == "header":
        _require(bool(str(auth_in.get("name") or "").strip()), "Header 鉴权要填 Header 名")

    headers = item.get("headers") if isinstance(item.get("headers"), dict) else {}
    headers = {str(k).strip(): str(v or "") for k, v in headers.items() if str(k).strip()}

    caps_in = item.get("capabilities") if isinstance(item.get("capabilities"), dict) else {}
    capabilities: Dict[str, Any] = {}
    for cap, spec in caps_in.items():
        if cap not in CAPABILITIES:
            continue                       # 未知能力静默丢弃，不为一个手滑的键报错
        if not spec:
            continue
        capabilities[cap] = _clean_capability(cap, spec)

    # 板块是**推导**出来的，不接受调用方指定 —— 上一版让用户自己勾，勾了却还没配
    # 对应能力就保存不了，连"自动配置"这个按钮都被这条校验拦死。板块是结果，
    # 不是输入。
    surfaces = derive_surfaces(capabilities)

    timeout = item.get("timeout")
    try:
        timeout = float(timeout) if timeout else 40.0
    except (TypeError, ValueError):
        timeout = 40.0

    return {
        "id": slug,
        "name": name,
        "enabled": bool(item.get("enabled", True)),
        "transport": transport,
        "url": url,
        "auth": {
            "mode": mode,
            "name": str(auth_in.get("name") or "").strip(),
            "value": str(auth_in.get("value") or ""),
        },
        "headers": headers,
        "handshake": bool(item.get("handshake", True)),
        "envelope": str(item.get("envelope") or "").strip(),
        "timeout": max(5.0, min(timeout, 300.0)),
        "surfaces": surfaces,
        "capabilities": capabilities,
        "note": str(item.get("note") or "").strip(),
    }


def hydrate(item: Dict[str, Any]) -> Dict[str, Any]:
    """把前端提交的（密钥被掩码的）配置补全成可直接调用的明文配置，但**不落盘**。

    探测和试跑都要用它：用户只改了个工具名就点"试一下"，不该被逼着把 key 重新
    输一遍。
    """
    slug = _strip_prefix(str(item.get("id") or "").strip().lower())
    previous = next((s for s in _load_raw() if s.get("id") == slug), None)
    return validate(_restore_secrets(dict(item), previous))


# ── 写入 ─────────────────────────────────────────────────────────────────────

def save(item: Dict[str, Any]) -> Dict[str, Any]:
    """新增或更新一个自定义源，返回脱敏后的结果。"""
    slug = _strip_prefix(str(item.get("id") or "").strip().lower())
    previous = next((s for s in _load_raw() if s.get("id") == slug), None)
    cleaned = validate(_restore_secrets(dict(item), previous))
    sources = [s for s in _load_raw() if s.get("id") != cleaned["id"]]
    sources.append(cleaned)
    sources.sort(key=lambda s: str(s.get("name") or s.get("id") or ""))
    _save_raw(sources)
    return _redact(dict(cleaned))


def delete(source_id: str) -> bool:
    slug = _strip_prefix(source_id)
    sources = _load_raw()
    kept = [s for s in sources if s.get("id") != slug]
    if len(kept) == len(sources):
        return False
    _save_raw(kept)
    return True
