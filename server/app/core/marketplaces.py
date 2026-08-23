"""亚马逊站点元数据：marketplace_id → 站点代码 / 名称 / 时区 / 货币。

**为什么以 marketplace_id 为主键**：它是亚马逊自己的常量，一个站点一个，永不变。
店铺名（"欧洲-UK"）是用户自己起的，`country` 字段领星给的是中文（"英国"），
两者都不能当键。领星的店铺列表接口每行都带 `marketplace_id`，直接用它。

**时区是干什么用的**：领星的促销活动时间是**站点当地时间的裸字符串**
（`"2026-08-24 23:59:00"`，不带时区）。要算"还剩几小时结束"，必须先按站点时区
把它变成带时区的时刻——把 UK 的 23:59 当成服务器所在的东八区去算，倒计时会差
7~8 个小时，正好是"以为还有一天、其实已经结束了"这种最坏的错法。

表里 11 个站点（AE/UK/IT/DE/FR/ES/NL/SE/TR/PL/JP）的 id 已用真实店铺列表逐条
核对过；其余是亚马逊公开的固定常量。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

try:  # Python 3.9+ 标准库；缺 tzdata 的精简镜像里可能拿不到
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - 环境兜底
    ZoneInfo = None  # type: ignore[assignment]


# marketplace_id -> (站点代码, 中文名, IANA 时区, 货币)
MARKETPLACES: Dict[str, Dict[str, str]] = {
    # 北美
    "ATVPDKIKX0DER":  {"code": "US", "name": "美国", "tz": "America/Los_Angeles", "currency": "USD"},
    "A2EUQ1WTGCTBG2": {"code": "CA", "name": "加拿大", "tz": "America/Toronto", "currency": "CAD"},
    "A1AM78C64UM0Y8": {"code": "MX", "name": "墨西哥", "tz": "America/Mexico_City", "currency": "MXN"},
    "A2Q3Y263D00KWC": {"code": "BR", "name": "巴西", "tz": "America/Sao_Paulo", "currency": "BRL"},
    # 欧洲
    "A1F83G8C2ARO7P": {"code": "UK", "name": "英国", "tz": "Europe/London", "currency": "GBP"},
    "A1PA6795UKMFR9": {"code": "DE", "name": "德国", "tz": "Europe/Berlin", "currency": "EUR"},
    "A13V1IB3VIYZZH": {"code": "FR", "name": "法国", "tz": "Europe/Paris", "currency": "EUR"},
    "APJ6JRA9NG5V4":  {"code": "IT", "name": "意大利", "tz": "Europe/Rome", "currency": "EUR"},
    "A1RKKUPIHCS9HS": {"code": "ES", "name": "西班牙", "tz": "Europe/Madrid", "currency": "EUR"},
    "A1805IZSGTT6HS": {"code": "NL", "name": "荷兰", "tz": "Europe/Amsterdam", "currency": "EUR"},
    "A2NODRKZP88ZB9": {"code": "SE", "name": "瑞典", "tz": "Europe/Stockholm", "currency": "SEK"},
    "A1C3SOZRARQ6R3": {"code": "PL", "name": "波兰", "tz": "Europe/Warsaw", "currency": "PLN"},
    "A33AVAJ2PDY3EV": {"code": "TR", "name": "土耳其", "tz": "Europe/Istanbul", "currency": "TRY"},
    "AMEN7PMS3EDWL":  {"code": "BE", "name": "比利时", "tz": "Europe/Brussels", "currency": "EUR"},
    # 亚太 / 中东
    "A1VC38T7YXB528": {"code": "JP", "name": "日本", "tz": "Asia/Tokyo", "currency": "JPY"},
    "A39IBJ37TRP1C6": {"code": "AU", "name": "澳大利亚", "tz": "Australia/Sydney", "currency": "AUD"},
    "A19VAU5U5O7RUS": {"code": "SG", "name": "新加坡", "tz": "Asia/Singapore", "currency": "SGD"},
    "A2VIGQ35RCS4UG": {"code": "AE", "name": "阿联酋", "tz": "Asia/Dubai", "currency": "AED"},
    "A17E79C6D8DWNP": {"code": "SA", "name": "沙特", "tz": "Asia/Riyadh", "currency": "SAR"},
    "A21TJRUUN4KGV":  {"code": "IN", "name": "印度", "tz": "Asia/Kolkata", "currency": "INR"},
    "ARBP9OOSHTCHU":  {"code": "EG", "name": "埃及", "tz": "Africa/Cairo", "currency": "EGP"},
}

#: 站点代码 → marketplace_id（反查用；同代码只会有一个 id）
BY_CODE: Dict[str, str] = {v["code"]: k for k, v in MARKETPLACES.items()}

#: 领星 `country` 字段给的是中文名，做一层兜底反查。
_BY_CN_NAME: Dict[str, str] = {v["name"]: k for k, v in MARKETPLACES.items()}

_UNKNOWN = {"code": "", "name": "", "tz": "UTC", "currency": ""}


def lookup(marketplace_id: Optional[str] = None, *, country: str = "",
           code: str = "") -> Dict[str, str]:
    """按 marketplace_id 查站点；查不到时依次退回中文国名、站点代码。

    一律返回一个字典（时区兜底 UTC），**绝不抛异常** —— 一个不认识的新站点
    不该让整块促销面板打不开；上层看 ``code`` 为空就知道这行没认出来。
    """
    if marketplace_id:
        hit = MARKETPLACES.get(str(marketplace_id).strip())
        if hit:
            return hit
    if country:
        hit = MARKETPLACES.get(_BY_CN_NAME.get(str(country).strip(), ""))
        if hit:
            return hit
    if code:
        hit = MARKETPLACES.get(BY_CODE.get(str(code).strip().upper(), ""))
        if hit:
            return hit
    return dict(_UNKNOWN)


def timezone_name(marketplace_id: Optional[str] = None, *, country: str = "",
                  code: str = "") -> str:
    return lookup(marketplace_id, country=country, code=code)["tz"]


def tzinfo(marketplace_id: Optional[str] = None, *, country: str = "",
           code: str = "") -> Any:
    """站点时区对象；zoneinfo 不可用或时区名无效时回退 UTC。"""
    name = timezone_name(marketplace_id, country=country, code=code)
    if ZoneInfo is None:
        from datetime import timezone
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 — 缺 tzdata 的环境不该让面板挂掉
        from datetime import timezone
        return timezone.utc


def store_meta(row: Dict[str, Any]) -> Dict[str, Any]:
    """把领星店铺列表的一行normalize成面板用的店铺元数据。

    ``has_ads_setting`` 值得单独拎出来：实测未开广告的店（本机 TR/PL）调广告报表
    会回 ``code=102 参数不合法``。按这个标志跳过，比让用户看一排报错强。
    """
    meta = lookup(row.get("marketplace_id"), country=str(row.get("country") or ""))
    return {
        "sid": int(row["sid"]) if str(row.get("sid", "")).lstrip("-").isdigit() else row.get("sid"),
        "name": row.get("name") or "",
        "marketplace_id": row.get("marketplace_id") or "",
        "code": meta["code"],
        "country": row.get("country") or meta["name"],
        "region": row.get("region") or "",
        "tz": meta["tz"],
        "currency": meta["currency"],
        "seller_id": row.get("seller_id") or "",
        "has_ads": bool(row.get("has_ads_setting")),
    }
