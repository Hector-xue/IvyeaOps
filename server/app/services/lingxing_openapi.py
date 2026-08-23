"""领星 OpenAPI adapter — the data + write backbone behind the gateway.

Implements the exact LingXing OpenAPI contract (verified against the official
docs + the open-source ``codeYi/lingxing`` SDK and live-tested):

* token lifecycle: ``access-token`` / ``refresh``, cached in
  ``data_dir/lingxing_token.json`` with expiry + auto-refresh;
* request signing: ``ksort`` params → ``k=v&…`` (empty-string skipped, arrays
  JSON-encoded, null→'null') → ``MD5().upper()`` → AES-128-ECB(key=appId,
  PKCS7) → base64;
* assembly: common params (``access_token``/``timestamp``/``app_key``/``sign``)
  in the query string, full params as JSON body;
* read/write route classification (writes gated by the operate switch upstream).

All policy (switches, audit, triple-review, human-confirm) lives in the gateway
(:mod:`app.services.lingxing_service`); this module is a thin, correct transport.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from cryptography.hazmat.primitives import padding as _pad
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.core import hub_settings as _hs
from app.core.config import settings

_TOKEN_PATH_GET = "/api/auth-server/oauth/access-token"
_TOKEN_PATH_REFRESH = "/api/auth-server/oauth/refresh"
_REQUEST_TIMEOUT_S = 60.0
_TOKEN_SKEW_S = 120  # refresh this many seconds before actual expiry


class LingXingOpenAPIError(RuntimeError):
    pass


# --- read/write route classification ----------------------------------------
# The operate switch + triple-review are the real guard — this is the backstop
# that stops a read-path call from ever hitting a mutating route.
#
# 判定看**最后一段的动词**，不做全路径子串匹配。子串匹配栽过两次，两个方向都栽：
#
# * 误判读为写：``/basicOpen/promotionalActivities/manage/list``（查促销活动列表）
#   撞上 ``/manage/`` —— 但那里的 manage 是"管理促销"这个业务名词，不是动词。
#   结果整条促销数据在只读通道上被拒。
# * 漏判写为读：``/basicOpen/adReport/spTarget/archiveNegatives``（归档否定词，
#   真·写操作）一个标记都没撞上，被当成读。真正拦住它的只有操作开关，
#   backstop 这一层是空的。
#
# 领星的路由都是「命名空间/动作」结构，动作在最后一段且以动词打头
# （``putSpCampaign`` / ``addKeywords`` / ``archiveNegatives`` / ``list``）。
# 取最后一段开头那串连续小写字母做**全词**比对：``addressList`` 取到的是
# "address" 而不是 "add"，天然不会误伤。
_WRITE_VERBS = frozenset({
    "put", "post", "create", "add", "update", "modify", "edit", "save", "set",
    "delete", "del", "remove", "archive", "cancel", "confirm", "submit",
    "approve", "reject", "operate", "adjust", "enable", "disable", "bind",
    "unbind", "push", "sync", "import", "upload", "batch",
})

# 整段就是这些词的（不是词头），一样算写。``/fba/operate/xxx`` 这类把动词放在
# 中间的路由靠这条兜住。
_WRITE_SEGMENTS = frozenset({
    "put", "create", "add", "update", "modify", "edit", "save", "delete",
    "remove", "archive", "cancel", "operate", "adjust", "import", "upload",
})

#: 明确登记的写路由 —— ops 只会调这几条写接口（与 ``lingxing_operate.OP_TYPES``
#: 对应，测试里钉住两者一致）。登记表优先于任何启发式。
WRITE_ROUTES = frozenset({
    "/basicOpen/adReport/manage/putSpCampaign",
    "/basicOpen/adReport/manage/putSpKeyword",
    "/basicOpen/adReport/manage/putSpTarget",
    "/basicOpen/adReport/manage/putSpAdGroup",
    "/basicOpen/adReport/spTarget/addKeywords",
    "/basicOpen/adReport/spTarget/addNegativeKeywords",
    "/basicOpen/adReport/spTarget/archiveNegatives",
})

_LEADING_LOWER = re.compile(r"^[a-z]+")


def _norm(route: str) -> str:
    return "/" + (route or "").strip().strip("/")


def classify_route(route: str) -> str:
    r = (route or "").strip()
    if not r:
        return "unknown"
    if _norm(r) in WRITE_ROUTES:
        return "write"
    segments = [s for s in r.split("/") if s]
    if not segments:
        return "unknown"
    if any(s.lower() in _WRITE_SEGMENTS for s in segments):
        return "write"
    head = _LEADING_LOWER.match(segments[-1])
    if head and head.group(0) in _WRITE_VERBS:
        return "write"
    return "read"


# --- signing ----------------------------------------------------------------
def _filter_array(v: Any) -> Any:
    """Recursively drop None entries (mirrors SignService::filter_array)."""
    if isinstance(v, dict):
        return {k: _filter_array(x) for k, x in v.items() if x is not None}
    if isinstance(v, list):
        return [_filter_array(x) for x in v if x is not None]
    return v


def _php_json(v: Any) -> str:
    """json_encode(JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES): compact, no
    space, unicode/slashes unescaped."""
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def make_sign(params: Dict[str, Any], app_id: str) -> str:
    parts = []
    for k in sorted(params.keys()):
        v = params[k]
        if isinstance(v, (dict, list)):
            parts.append(f"{k}=" + _php_json(_filter_array(v)))
        elif v == "":
            continue  # empty string skipped (matches SDK)
        else:
            if isinstance(v, bool):
                v = "true" if v else "false"
            elif v is None:
                v = "null"
            parts.append(f"{k}={v}")
    canonical = "&".join(parts)
    md5_upper = hashlib.md5(canonical.encode("utf-8")).hexdigest().upper()
    padder = _pad.PKCS7(128).padder()
    data = padder.update(md5_upper.encode("utf-8")) + padder.finalize()
    enc = Cipher(algorithms.AES(app_id.encode("utf-8")), modes.ECB()).encryptor()
    ct = enc.update(data) + enc.finalize()
    return base64.b64encode(ct).decode("ascii")


# --- config / token cache ---------------------------------------------------
def _host() -> str:
    return (_hs.get("lingxing_openapi_host") or "").strip().rstrip("/")


def _appid() -> str:
    return (_hs.get("lingxing_openapi_appid") or "").strip()


def _secret() -> str:
    return (_hs.get("lingxing_openapi_secret") or "").strip()


def is_configured() -> bool:
    return bool(_host() and _appid() and _secret())


def _token_path() -> Path:
    return settings.data_dir / "lingxing_token.json"


def _load_token() -> Dict[str, Any]:
    try:
        return json.loads(_token_path().read_text("utf-8"))
    except Exception:
        return {}


def _save_token(tok: Dict[str, Any]) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    p = _token_path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(tok, ensure_ascii=False), "utf-8")
    tmp.replace(p)


_token_lock = asyncio.Lock()


async def _fetch_token(client: httpx.AsyncClient) -> Dict[str, Any]:
    r = await client.post(f"{_host()}{_TOKEN_PATH_GET}",
                          params={"appId": _appid(), "appSecret": _secret()})
    return _unwrap_token(r)


async def _refresh_token(client: httpx.AsyncClient, refresh_token: str) -> Dict[str, Any]:
    r = await client.post(f"{_host()}{_TOKEN_PATH_REFRESH}",
                          params={"appId": _appid(), "refreshToken": refresh_token})
    return _unwrap_token(r)


def _unwrap_token(r: httpx.Response) -> Dict[str, Any]:
    if r.status_code >= 400:
        raise LingXingOpenAPIError(f"令牌接口 HTTP {r.status_code}: {r.text[:200]}")
    try:
        body = r.json()
    except Exception as e:
        raise LingXingOpenAPIError(f"令牌响应非 JSON: {r.text[:200]}") from e
    if str(body.get("code")) not in ("200", "0"):
        raise LingXingOpenAPIError(f"令牌接口错误 {body.get('code')}: {body.get('msg') or body.get('message')}")
    data = body.get("data") or {}
    at = data.get("access_token")
    if not at:
        raise LingXingOpenAPIError("令牌响应缺少 access_token")
    return {
        "access_token": at,
        "refresh_token": data.get("refresh_token", ""),
        "expire_at": time.time() + float(data.get("expires_in") or 0),
    }


async def _ensure_token(client: httpx.AsyncClient) -> str:
    """Return a valid access_token, fetching/refreshing as needed (locked)."""
    if not is_configured():
        raise LingXingOpenAPIError("未配置领星 OpenAPI 凭证 (appid/secret/host)")
    async with _token_lock:
        tok = _load_token()
        now = time.time()
        if tok.get("access_token") and float(tok.get("expire_at", 0)) - _TOKEN_SKEW_S > now:
            return tok["access_token"]
        # try refresh, else full fetch
        new: Optional[Dict[str, Any]] = None
        if tok.get("refresh_token"):
            try:
                new = await _refresh_token(client, tok["refresh_token"])
            except LingXingOpenAPIError:
                new = None
        if new is None:
            new = await _fetch_token(client)
        _save_token(new)
        return new["access_token"]


# --- request ----------------------------------------------------------------
async def call(route: str, params: Optional[Dict[str, Any]] = None, *,
               method: str = "POST") -> Dict[str, Any]:
    """Signed OpenAPI call. Transport only — gating/audit handled by the gateway.

    ``route`` is the business path, e.g. ``/erp/sc/data/seller/lists``.
    """
    params = dict(params or {})
    method = method.upper()
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S, verify=True) as client:
        access_token = await _ensure_token(client)
        common = {"access_token": access_token, "timestamp": int(time.time()),
                  "app_key": _appid()}
        full = {**params, **common}
        sign = make_sign(full, _appid())
        if method == "GET":
            query = {**full, "sign": sign}
            body = None
        else:
            query = {**common, "sign": sign}
            body = _php_json(full)
        url = f"{_host()}/{route.strip('/')}"
        headers = {"Content-Type": "application/json"}
        try:
            if method == "GET":
                r = await client.get(url, params=query, headers=headers)
            else:
                r = await client.request(method, url, params=query, content=body, headers=headers)
        except httpx.HTTPError as e:
            raise LingXingOpenAPIError(f"领星 OpenAPI 连接失败: {e}") from e
    if r.status_code >= 400:
        raise LingXingOpenAPIError(f"领星 OpenAPI HTTP {r.status_code}: {r.text[:300]}")
    try:
        body_json = r.json()
    except Exception as e:
        raise LingXingOpenAPIError(f"领星 OpenAPI 响应非 JSON: {r.text[:300]}") from e
    code = str(body_json.get("code"))
    if code not in ("200", "0", "1"):  # LingXing success codes vary by endpoint
        # surface but let caller decide; many endpoints use code=200 + success flag
        if body_json.get("success") is not True and code not in ("200", "0"):
            raise LingXingOpenAPIError(
                f"领星 OpenAPI 业务错误 code={code} msg={body_json.get('message') or body_json.get('msg')}")
    return body_json


async def verify() -> Dict[str, Any]:
    """End-to-end credential + signature check: fetch a token and make one cheap
    signed read call. Returns a small diagnostic. Used by the gateway probe."""
    if not is_configured():
        raise LingXingOpenAPIError("未配置领星 OpenAPI 凭证")
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
        token = await _ensure_token(client)
    # A canonical, side-effect-free read: Amazon seller (store) list.
    res = await call("/erp/sc/data/seller/lists", {}, method="GET")
    data = res.get("data")
    n = len(data) if isinstance(data, list) else None
    return {
        "ok": True,
        "token_acquired": bool(token),
        "probe_route": "/erp/sc/data/seller/lists",
        "probe_code": str(res.get("code")),
        "probe_seller_count": n,
        "probe_msg": res.get("message") or res.get("msg") or "",
    }
