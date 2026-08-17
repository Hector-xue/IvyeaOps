"""Controlled IvyeaOps tool bridge for embedded IvyeaAgent chat.

The local IvyeaAgent process should not import or know IvyeaOps internals.
Instead, Ops exposes a short-lived, signed bridge token and this registry of
safe board actions. Each tool declares its board permission and parameter
schema; execution rehydrates the principal into ``current_user`` so existing
per-user storage and permission boundaries still apply.
"""
from __future__ import annotations

import inspect
import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Union

from fastapi import HTTPException
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core import permissions as module_permissions
from app.core.config import settings

_BRIDGE_SALT = "ivyea-ops.ivyea-agent.bridge"
_BRIDGE_MAX_AGE_SECONDS = 15 * 60
_serializer = URLSafeTimedSerializer(settings.secret_key, salt=_BRIDGE_SALT)


Handler = Callable[[dict[str, Any]], Union[Any, Awaitable[Any]]]


@dataclass(frozen=True)
class OpsTool:
    name: str
    module: str
    title: str
    description: str
    parameters: dict[str, Any]
    handler: Handler
    destructive: bool = False
    long_running: bool = False


def _principal() -> dict[str, Any]:
    from app.core.security import ADMIN_ID, current_user
    cu = current_user.get()
    if cu:
        return {
            "id": cu.get("id"),
            "role": cu.get("role", "user"),
            "email": cu.get("email", ""),
            "permissions": list(cu.get("permissions") or []),
            "position": cu.get("position", ""),
        }
    return {"id": ADMIN_ID, "role": "admin", "email": settings.admin_user, "permissions": []}


def issue_bridge_token() -> str:
    principal = _principal()
    return _serializer.dumps({"principal": principal, "iat": int(time.time())})


def principal_from_token(token: str) -> dict[str, Any]:
    try:
        data = _serializer.loads(token, max_age=_BRIDGE_MAX_AGE_SECONDS)
    except SignatureExpired as exc:
        raise HTTPException(status_code=401, detail="IvyeaAgent bridge token expired") from exc
    except BadSignature as exc:
        raise HTTPException(status_code=401, detail="IvyeaAgent bridge token invalid") from exc
    principal = data.get("principal") if isinstance(data, dict) else None
    if not isinstance(principal, dict) or not principal.get("role"):
        raise HTTPException(status_code=401, detail="IvyeaAgent bridge token missing principal")
    return {
        "id": principal.get("id"),
        "role": principal.get("role", "user"),
        "email": principal.get("email", ""),
        "permissions": list(principal.get("permissions") or []),
        "position": principal.get("position", ""),
    }


def activate_bridge_principal(token: str) -> dict[str, Any]:
    from app.core.security import current_user
    principal = principal_from_token(token)
    current_user.set(principal)
    return principal


def _can_access(module: str, principal: dict[str, Any] | None = None) -> bool:
    principal = principal or _principal()
    if principal.get("role") == "admin":
        return True
    if module in module_permissions.BASE_MODULES:
        return True
    return module in (principal.get("permissions") or [])


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _limit(value: Any, max_chars: int = 24000) -> Any:
    text = json.dumps(_jsonable(value), ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return _jsonable(value)
    return {
        "truncated": True,
        "max_chars": max_chars,
        "preview": text[:max_chars],
    }


def _obj(**props: Any) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": []}


def _str(desc: str = "", default: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "string"}
    if desc:
        out["description"] = desc
    if default is not None:
        out["default"] = default
    return out


def _int(desc: str = "", default: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "integer"}
    if desc:
        out["description"] = desc
    if default is not None:
        out["default"] = default
    return out


def _num(desc: str = "", default: float | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "number"}
    if desc:
        out["description"] = desc
    if default is not None:
        out["default"] = default
    return out


def _arr(item: dict[str, Any], desc: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {"type": "array", "items": item}
    if desc:
        out["description"] = desc
    return out


async def _home_list_watch(args: dict[str, Any]) -> Any:
    from app.routers import home
    return home.list_watch(_user="bridge")


async def _home_add_watch(args: dict[str, Any]) -> Any:
    from app.routers import home
    return home.add_watch(home.WatchIn(
        asin=str(args.get("asin") or "").strip().upper(),
        marketplace=str(args.get("marketplace") or "US").strip().upper(),
        kind=str(args.get("kind") or "competitor").strip(),
        label=str(args.get("label") or "").strip(),
    ), _user="bridge")


async def _home_pulse(args: dict[str, Any]) -> Any:
    from app.routers import home
    return await home.pulse(home.PulseReq(
        asin=str(args.get("asin") or "").strip().upper(),
        marketplace=str(args.get("marketplace") or "US").strip().upper(),
    ), _user="bridge")


async def _market_history(args: dict[str, Any]) -> Any:
    from app.routers import market
    return market.get_history(_user="bridge")


async def _market_collect(args: dict[str, Any]) -> Any:
    from app.routers import market
    mode = str(args.get("mode") or "keyword").strip().lower()
    query = str(args.get("query") or "").strip()
    marketplace = str(args.get("marketplace") or "US").strip().upper()
    data_source = str(args.get("data_source") or "sorftime").strip().lower()
    if not query:
        raise ValueError("query is required")

    async def _progress(_step: str, _done: int, _total: int) -> None:
        return None

    service = market._pipeline_for(data_source)
    if mode == "asin":
        data, errors = await service.asin_pipeline(query, marketplace, _progress)
    else:
        mode = "keyword"
        data, errors = await service.keyword_pipeline(query, marketplace, _progress)
    return {"mode": mode, "query": query, "marketplace": marketplace,
            "data_source": data_source, "data_source_label": market._source_label(data_source),
            "data": _limit(data), "warnings": errors}


async def _market_generate_report(args: dict[str, Any]) -> Any:
    from app.routers import market
    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    mode = str(args.get("mode") or "keyword").strip().lower()
    marketplace = str(args.get("marketplace") or "US").strip().upper()
    data_source = str(args.get("data_source") or "sorftime").strip().lower()
    res = await market.generate_report(mode, query, marketplace, data_source)
    # 报告已落库到市场调研历史；正文截断，避免回灌爆上下文（agent 可再 read 历史拿全文）。
    return {"id": res["id"], "mode": res["mode"], "query": res["query"],
            "marketplace": res["marketplace"], "provider": res["provider"],
            "data_source": res["data_source"], "data_source_label": res["data_source_label"],
            "elapsed_s": res["elapsed_s"], "warnings": res.get("warnings") or [],
            "saved_to": "market_history",
            "report": _limit(res["report"], 12000)}


async def _playbook_history(args: dict[str, Any]) -> Any:
    from app.routers import playbook
    return playbook.get_history(_user="bridge")


async def _image_generate(args: dict[str, Any]) -> Any:
    """作图。**这是个长任务**：提交后返回 task_id，由 agent 自己轮询状态。

    为什么要把它做成工具：工作台已经有一条配好的作图链路（系统配置 → AI 生图），
    但它只挂在「AI 作图」那个页面上 —— 用户在任务台说"给我画一张主图"，
    agent 手里没有任何手段去调它，只能回一句"我做不到"。而这条链路本来就在
    这台机器上配好了。
    """
    from app.routers import assistant

    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt is required")
    refs = [u for u in (args.get("image_urls") or []) if isinstance(u, str) and u.strip()]
    req = assistant.ImageReq(
        prompt=prompt,
        size=str(args.get("size") or "1024x1024"),
        n=max(1, min(int(args.get("n") or 1), 4)),
        image_urls=refs or None,
    )
    out = await assistant.image_submit(req)
    return {**out, "note": "已提交作图任务。用 image_status 查询进度，通常十几秒到一分钟。"}


async def _image_status(args: dict[str, Any]) -> Any:
    from app.routers import assistant
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("task_id is required")
    return await assistant.image_status(task_id)


async def _playbook_generate_report(args: dict[str, Any]) -> Any:
    from app.routers import playbook
    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    res = await playbook.generate_report(
        str(args.get("mode") or "keyword").strip().lower(),
        query, str(args.get("marketplace") or "US").strip().upper(),
        str(args.get("price") or ""), str(args.get("cost") or ""),
        str(args.get("data_source") or "sorftime").strip().lower())
    return {"id": res["id"], "mode": res["mode"], "query": res["query"],
            "marketplace": res["marketplace"], "provider": res["provider"],
            "data_source": res["data_source"], "data_source_label": res["data_source_label"],
            "elapsed_s": res["elapsed_s"], "warnings": res.get("warnings") or [],
            "saved_to": "playbook_history", "report": _limit(res["report"], 12000)}


async def _asin_audit_start(args: dict[str, Any]) -> Any:
    from app.routers import amazon
    return await amazon.audit_start(amazon.AuditStartBody(
        asin=str(args.get("asin") or "").strip().upper(),
        marketplace=str(args.get("marketplace") or "US").strip().upper(),
        mode=str(args.get("mode") or "full").strip() or "full",
        runner=str(args.get("runner") or "auto").strip() or "auto",
    ), _user="bridge")


async def _asin_audit_list(args: dict[str, Any]) -> Any:
    from app.routers import amazon
    return amazon.audit_list(limit=int(args.get("limit") or 20), _user="bridge")


async def _asin_audit_status(args: dict[str, Any]) -> Any:
    from app.routers import amazon
    return amazon.audit_get(str(args.get("job_id") or ""), _user="bridge")


async def _ad_audit_list(args: dict[str, Any]) -> Any:
    from app.routers import ad_audit
    return ad_audit.ad_list(limit=int(args.get("limit") or 20), _user="bridge")


async def _ad_audit_status(args: dict[str, Any]) -> Any:
    from app.routers import ad_audit
    return ad_audit.ad_get(str(args.get("job_id") or ""), _user="bridge")


async def _ad_audit_start(args: dict[str, Any]) -> Any:
    from app.routers import ad_audit
    return await ad_audit.ad_start(ad_audit.AdStartBody(
        job_id=str(args.get("job_id") or ""),
        goal=str(args.get("goal") or "profit"),
        output_mode=str(args.get("output_mode") or "report"),
        asin=str(args.get("asin") or "").strip().upper(),
        product_notes=str(args.get("product_notes") or ""),
        protected_keywords=list(args.get("protected_keywords") or []),
        runner=str(args.get("runner") or "auto"),
        daily_budgets=dict(args.get("daily_budgets") or {}),
    ), _user="bridge")


async def _listing_projects(args: dict[str, Any]) -> Any:
    from app.routers import listing
    return listing.list_projects(_user="bridge")


async def _listing_create_project(args: dict[str, Any]) -> Any:
    from app.routers import listing
    return await listing.create_project(listing.CreateProjectReq(
        asin=str(args.get("asin") or "").strip().upper(),
        marketplace=str(args.get("marketplace") or "US").strip().upper(),
        supplier_url=str(args.get("supplier_url") or "") or None,
    ), _user="bridge")


async def _listing_get_project(args: dict[str, Any]) -> Any:
    from app.routers import listing
    return listing.get_project(str(args.get("project_id") or ""), _user="bridge")


async def _listing_scrape(args: dict[str, Any]) -> Any:
    from app.routers import listing
    return await listing.scrape(str(args.get("project_id") or ""), _user="bridge")


async def _listing_copy_jobs(args: dict[str, Any]) -> Any:
    from app.routers import listing
    return listing.list_copy_jobs(_user="bridge")


async def _listing_create_copy_job(args: dict[str, Any]) -> Any:
    from app.routers import listing
    return await listing.create_copy_job(listing.CopyJobReq(
        marketplace=str(args.get("marketplace") or "US").strip().upper(),
        product_type=str(args.get("product_type") or "").strip(),
        asins=list(args.get("asins") or []),
        product_notes=str(args.get("product_notes") or ""),
        project_id=str(args.get("project_id") or "") or None,
    ), _user="bridge")


async def _listing_start_copy_job(args: dict[str, Any]) -> Any:
    from app.routers import listing
    return await listing.start_copy_job(str(args.get("job_id") or ""), _user="bridge")


async def _listing_get_copy_job(args: dict[str, Any]) -> Any:
    from app.routers import listing
    return listing.get_copy_job(str(args.get("job_id") or ""), _user="bridge")


async def _deep_keyword(args: dict[str, Any]) -> Any:
    from app.routers import deep_analysis
    return await deep_analysis.keyword_competition(deep_analysis.KeywordReq(
        keyword=str(args.get("keyword") or ""),
        country=str(args.get("country") or "US"),
        asin=str(args.get("asin") or ""),
    ))


async def _deep_competitor(args: dict[str, Any]) -> Any:
    from app.routers import deep_analysis
    return await deep_analysis.competitor_lookup(deep_analysis.CompetitorReq(
        asin=str(args.get("asin") or ""),
        country=str(args.get("country") or "US"),
        time_type=str(args.get("time_type") or "lately"),
        time_value=str(args.get("time_value") or "7"),
    ))


async def _deep_traffic(args: dict[str, Any]) -> Any:
    from app.routers import deep_analysis
    return await deep_analysis.traffic_diagnosis(deep_analysis.TrafficReq(
        asin=str(args.get("asin") or ""),
        country=str(args.get("country") or "US"),
    ))


async def _deep_history(args: dict[str, Any]) -> Any:
    from app.routers import deep_analysis
    return deep_analysis.get_history(_user="bridge")


async def _deep_generate_report(args: dict[str, Any]) -> Any:
    from app.routers import deep_analysis
    tool = str(args.get("tool") or "").strip().lower()
    query = str(args.get("query") or args.get("asin") or args.get("keyword") or "").strip()
    if not query:
        raise ValueError("query (keyword or ASIN) is required")
    res = await deep_analysis.generate_report(tool, query, str(args.get("country") or "US").strip().upper())
    return {"id": res["id"], "tool": res["tool"], "title": res["title"], "query": res["query"],
            "country": res["country"], "elapsed_s": res["elapsed_s"],
            "saved_to": "deep_analysis_history", "report": _limit(res["report"], 12000)}


async def _news_latest(args: dict[str, Any]) -> Any:
    from app.routers import news
    return news.list_news(date=str(args.get("date") or "") or None, category=str(args.get("category") or "") or None, _user="bridge")


async def _news_refresh(args: dict[str, Any]) -> Any:
    from app.routers import news
    return await news.trigger_refresh(_user="bridge")


async def _monitor_snapshot(args: dict[str, Any]) -> Any:
    from app.routers import monitor
    return monitor.snapshot(_user="bridge")


async def _monitor_services(args: dict[str, Any]) -> Any:
    from app.routers import monitor
    return monitor.services(_user="bridge")


async def _monitor_token_usage(args: dict[str, Any]) -> Any:
    from app.routers import monitor
    return monitor.token_usage(_user="bridge")


async def _skill_tools_list(args: dict[str, Any]) -> Any:
    from app.routers import skill_tools
    return skill_tools.list_tools(
        q=str(args.get("query") or ""),
        category=str(args.get("category") or ""),
        limit=int(args.get("limit") or 30),
    )


async def _lingxing_status(args: dict[str, Any]) -> Any:
    from app.routers import lingxing
    return await lingxing.status()


async def _lingxing_dashboard(args: dict[str, Any]) -> Any:
    from app.routers import lingxing
    return await lingxing.dashboard(
        sids=str(args.get("sids") or ""),
        days=int(args.get("days") or 7),
    )


async def _lingxing_optimizer(args: dict[str, Any]) -> Any:
    from app.routers import lingxing
    return await lingxing.optimizer_run(
        sid=int(args.get("sid") or 0),
        days=int(args.get("days") or 0),
    )


async def _lingxing_operate_tickets(args: dict[str, Any]) -> Any:
    from app.routers import lingxing
    return await lingxing.operate_tickets(limit=int(args.get("limit") or 50))


async def _lingxing_operate_enable(args: dict[str, Any]) -> Any:
    from app.routers import lingxing
    return await lingxing.operate_enable()


async def _lingxing_operate_disable(args: dict[str, Any]) -> Any:
    from app.routers import lingxing
    return await lingxing.operate_disable()


TOOLS: tuple[OpsTool, ...] = (
    OpsTool("home_list_watch", "home", "Home 监控清单", "列出 Home 页已关注的 ASIN。", _obj(), _home_list_watch),
    OpsTool("home_add_watch", "home", "添加 ASIN 监控", "把一个 ASIN 添加到 Home 监控清单。", _obj(
        asin=_str("10 位 ASIN"), marketplace=_str(default="US"), kind=_str("competitor 或 own", "competitor"), label=_str("显示名"),
    ), _home_add_watch, destructive=True),
    OpsTool("home_pulse", "home", "刷新 ASIN 快照", "抓取一个 ASIN 的最新 Home 指标并写入快照。", _obj(
        asin=_str("10 位 ASIN"), marketplace=_str(default="US"),
    ), _home_pulse, destructive=True, long_running=True),
    OpsTool("market_history", "market", "市场调研历史", "读取市场调研板块历史报告。", _obj(), _market_history),
    OpsTool("market_generate_report", "market", "生成市场调研报告",
            "对关键词或 ASIN 跑完整市场调研：按 data_source 采集 Sorftime 或卖家精灵数据 + AI 合成完整报告，"
            "并保存到「市场调研」板块历史（用户在该板块即可看到）。用户要做市场调研/出报告时用它，"
            "不要用 market_collect_data（那只采数据不出报告）。", _obj(
                mode=_str("keyword 或 asin", default="keyword"),
                query=_str("关键词或 ASIN"),
                marketplace=_str("站点，如 US/UK/DE", default="US"),
                data_source=_str("sorftime 或 sellersprite", default="sorftime")),
            _market_generate_report, long_running=True),
    OpsTool("market_collect_data", "market", "市场数据采集", "按关键词或 ASIN 从指定数据源采集原始市场数据，不调用二次 AI 合成。", _obj(
        mode=_str("keyword 或 asin", "keyword"), query=_str("关键词或 ASIN"), marketplace=_str(default="US"),
        data_source=_str("sorftime 或 sellersprite", default="sorftime"),
    ), _market_collect, long_running=True),
    OpsTool("playbook_history", "playbook", "打法历史", "读取打法/Launch Playbook 历史报告。", _obj(), _playbook_history),
    OpsTool("image_generate", "tools", "AI 作图",
            "按提示词生成图片（也可传参考图做图生图）。**用户提出任何作图/出图/"
            "画一张/生成主图之类的需求时用它** —— 这台机器上已经配好了生图链路。"
            "返回 task_id，再用 image_status 查结果。", _obj(
                prompt=_str("画面描述，越具体越好；中文英文都行"),
                size=_str("尺寸，如 1024x1024 / 1024x1536", default="1024x1024"),
                n=_int("生成张数，1-4", default=1),
                image_urls=_arr(_str(), "原图（给了就是图生图/改图，会保留原图内容）。"
                                        "用户附图时系统会给你一个 ivyea-ref:// 句柄，"
                                        "原样填进来即可；也接受 http(s) 图片地址，"
                                        "比如上一轮出的图 —— 那就是在它基础上继续改")),
            _image_generate, long_running=True),
    OpsTool("image_status", "tools", "作图任务状态",
            "查询 image_generate 提交的任务，完成后返回图片地址。", _obj(
                task_id=_str("image_generate 返回的任务 ID")),
            _image_status),
    OpsTool("playbook_generate_report", "playbook", "生成打法推荐",
            "对关键词或 ASIN 跑完整打法/Launch 推荐：按 data_source 采集 Sorftime 或卖家精灵数据 + AI 合成,"
            "并保存到「打法推荐」板块历史。用户要打法/Launch 方案时用它。", _obj(
                mode=_str("keyword 或 asin", default="keyword"),
                query=_str("关键词或 ASIN"),
                marketplace=_str("站点，如 US/UK/DE", default="US"),
                price=_str("售价，可空"), cost=_str("成本，可空"),
                data_source=_str("sorftime 或 sellersprite", default="sorftime")),
            _playbook_generate_report, long_running=True),
    OpsTool("asin_audit_start", "tools", "启动 ASIN 深度审计", "启动分析工具中的 ASIN 深度审计任务，返回 job_id。", _obj(
        asin=_str("10 位 ASIN"), marketplace=_str(default="US"), mode=_str("full 或 rewrite_only", "full"), runner=_str("auto/ivyea-agent/hermes/codex/claude", "auto"),
    ), _asin_audit_start, destructive=True, long_running=True),
    OpsTool("asin_audit_list", "tools", "ASIN 审计列表", "列出最近 ASIN 审计任务。", _obj(limit=_int(default=20)), _asin_audit_list),
    OpsTool("asin_audit_status", "tools", "ASIN 审计状态", "查询 ASIN 审计任务状态和结果。", _obj(job_id=_str("任务 ID")), _asin_audit_status),
    OpsTool("ad_audit_list", "tools", "广告审计列表", "列出最近广告报表审计任务。", _obj(limit=_int(default=20)), _ad_audit_list),
    OpsTool("ad_audit_status", "tools", "广告审计状态", "查询广告审计任务状态和结果。", _obj(job_id=_str("任务 ID")), _ad_audit_status),
    OpsTool("ad_audit_start", "tools", "启动广告审计", "对已上传的广告报表 job_id 启动审计。", _obj(
        job_id=_str("已上传报表任务 ID"), goal=_str("profit/new_launch/relaunch/clearance", "profit"),
        output_mode=_str("report 或 xlsx_plan", "report"), asin=_str("可选 ASIN"),
        product_notes=_str("产品说明"), protected_keywords=_arr(_str(), "保护词"), runner=_str(default="auto"),
        daily_budgets={"type": "object", "description": "source_id 到日预算的映射"},
    ), _ad_audit_start, destructive=True, long_running=True),
    OpsTool("listing_projects", "listing", "Listing 项目列表", "列出 Listing 工作台项目。", _obj(), _listing_projects),
    OpsTool("listing_create_project", "listing", "创建 Listing 项目", "按 ASIN 创建 Listing 工作台项目。", _obj(
        asin=_str("10 位 ASIN"), marketplace=_str(default="US"), supplier_url=_str("可选供应商链接"),
    ), _listing_create_project, destructive=True),
    OpsTool("listing_get_project", "listing", "Listing 项目详情", "读取一个 Listing 项目详情。", _obj(project_id=_str("项目 ID")), _listing_get_project),
    OpsTool("listing_scrape", "listing", "采集 Listing 数据", "为 Listing 项目采集 Amazon 页面数据和主图。", _obj(project_id=_str("项目 ID")), _listing_scrape, destructive=True, long_running=True),
    OpsTool("listing_copy_jobs", "listing", "Listing 文案任务列表", "列出 Listing 批量文案任务。", _obj(), _listing_copy_jobs),
    OpsTool("listing_create_copy_job", "listing", "创建 Listing 文案任务", "创建文案生成任务，后续可启动并查询。", _obj(
        marketplace=_str(default="US"), product_type=_str("产品类型"), asins=_arr(_str(), "竞品 ASIN 列表"),
        product_notes=_str("产品说明"), project_id=_str("关联项目 ID"),
    ), _listing_create_copy_job, destructive=True),
    OpsTool("listing_start_copy_job", "listing", "启动 Listing 文案任务", "启动已创建的 Listing 文案任务。", _obj(job_id=_str("任务 ID")), _listing_start_copy_job, destructive=True, long_running=True),
    OpsTool("listing_get_copy_job", "listing", "Listing 文案任务状态", "查询 Listing 文案任务状态和结果。", _obj(job_id=_str("任务 ID")), _listing_get_copy_job),
    OpsTool("deep_keyword_competition", "tools", "关键词竞争分析", "调用深度分析工具箱的关键词竞争分析。", _obj(
        keyword=_str("关键词"), country=_str(default="US"), asin=_str("可选对标 ASIN"),
    ), _deep_keyword, long_running=True),
    OpsTool("deep_competitor_lookup", "tools", "竞品流量词反查", "调用深度分析工具箱反查 ASIN 关键词信号。", _obj(
        asin=_str("ASIN"), country=_str(default="US"), time_type=_str(default="lately"), time_value=_str(default="7"),
    ), _deep_competitor, long_running=True),
    OpsTool("deep_analysis_history", "tools", "分析工具历史", "读取分析工具板块的历史分析报告。", _obj(), _deep_history),
    OpsTool("deep_generate_report", "tools", "生成分析报告",
            "跑一项深度分析(关键词竞争/竞品反查/流量诊断)并 AI 合成 Markdown 报告，"
            "保存到「分析工具」板块历史。用户要做这类分析/出报告时用它。", _obj(
                tool=_str("keyword(关键词竞争) / competitor(竞品反查) / traffic(流量诊断)"),
                query=_str("关键词(keyword 时) 或 ASIN(competitor/traffic 时)"),
                country=_str("站点，如 US/UK", default="US")),
            _deep_generate_report, long_running=True),
    OpsTool("deep_traffic_diagnosis", "tools", "流量异动诊断", "调用深度分析工具箱诊断 ASIN 流量异常。", _obj(
        asin=_str("ASIN"), country=_str(default="US"),
    ), _deep_traffic, long_running=True),
    OpsTool("news_latest", "news", "资讯摘要", "读取资讯板块最新或指定日期摘要。", _obj(date=_str("YYYY-MM-DD，可空"), category=_str("ai_industry 或 amazon_seller，可空")), _news_latest),
    OpsTool("news_refresh", "news", "刷新资讯", "触发资讯板块 RSS 抓取和 AI 摘要生成。", _obj(), _news_refresh, destructive=True, long_running=True),
    OpsTool("monitor_snapshot", "servmon", "服务器快照", "读取 CPU、内存、磁盘、网络快照。", _obj(), _monitor_snapshot),
    OpsTool("monitor_services", "servmon", "服务状态", "读取服务器监控中的 systemd 服务状态。", _obj(), _monitor_services),
    OpsTool("monitor_token_usage", "servmon", "Token 用量", "读取 Token 用量和模型成本概览。", _obj(), _monitor_token_usage),
    OpsTool("skill_tools_list", "skill-hub", "Skill 工具列表", "列出 Skill Tools 板块可运行工具。", _obj(query=_str("搜索词"), category=_str("分类"), limit=_int(default=30)), _skill_tools_list),
    OpsTool("lingxing_status", "admin", "领星状态", "读取领星集成开关和后端配置状态。", _obj(), _lingxing_status),
    OpsTool("lingxing_dashboard", "admin", "领星广告大盘", "读取领星广告数据大盘。", _obj(sids=_str("逗号分隔 SID，空=全部"), days=_int(default=7)), _lingxing_dashboard, long_running=True),
    OpsTool("lingxing_optimizer", "admin", "领星规则优化候选", "运行领星店铺广告规则引擎，生成候选操作。", _obj(sid=_int("店铺 SID"), days=_int(default=0)), _lingxing_optimizer, long_running=True),
    OpsTool("lingxing_operate_tickets", "admin", "领星操作工单", "列出领星操作工单。", _obj(limit=_int(default=50)), _lingxing_operate_tickets),
    OpsTool("lingxing_operate_enable", "admin", "开启领星操作开关", "开启领星可写态；实际写入仍需三重复核和人工确认。", _obj(), _lingxing_operate_enable, destructive=True),
    OpsTool("lingxing_operate_disable", "admin", "关闭领星操作开关", "关闭领星可写态，恢复只读。", _obj(), _lingxing_operate_disable, destructive=True),
)

_TOOL_BY_NAME = {tool.name: tool for tool in TOOLS}


def _public_tool(tool: OpsTool) -> dict[str, Any]:
    return {
        "name": tool.name,
        "module": tool.module,
        "title": tool.title,
        "description": tool.description,
        "parameters": tool.parameters,
        "destructive": tool.destructive,
        "long_running": tool.long_running,
    }


def list_tools(module: str = "", query: str = "", principal: dict[str, Any] | None = None) -> dict[str, Any]:
    module = (module or "").strip()
    q = (query or "").strip().lower()
    principal = principal or _principal()
    rows: list[dict[str, Any]] = []
    for tool in TOOLS:
        if module and tool.module != module:
            continue
        if not _can_access(tool.module, principal):
            continue
        haystack = f"{tool.name} {tool.module} {tool.title} {tool.description}".lower()
        if q and q not in haystack:
            continue
        rows.append(_public_tool(tool))
    return {
        "ok": True,
        "tools": rows,
        "modules": sorted({row["module"] for row in rows}),
        "principal": {"role": principal.get("role"), "email": principal.get("email", "")},
    }


async def call_tool(name: str, arguments: dict[str, Any] | None = None, principal: dict[str, Any] | None = None) -> dict[str, Any]:
    tool = _TOOL_BY_NAME.get((name or "").strip())
    if not tool:
        return {"ok": False, "error": "tool_not_found", "tool": name}
    principal = principal or _principal()
    if not _can_access(tool.module, principal):
        return {"ok": False, "error": "permission_denied", "tool": tool.name, "module": tool.module}
    args = arguments if isinstance(arguments, dict) else {}
    try:
        result = tool.handler(args)
        if inspect.isawaitable(result):
            result = await result
    except HTTPException as exc:
        return {"ok": False, "error": "http_error", "status_code": exc.status_code, "detail": exc.detail, "tool": tool.name}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "tool_error", "detail": str(exc), "tool": tool.name}
    return {
        "ok": True,
        "tool": _public_tool(tool),
        "result": _limit(result),
    }
