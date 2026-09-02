"""IvyeaAgent integration endpoints for IvyeaOps."""
from __future__ import annotations

import base64
import json as _json
import logging
import re
import asyncio
import threading as _threading
import time as _time
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.security import require_user, require_user_info, require_admin
from app.services import agent_mcp
from app.services import console_sessions
from app.services import ivyea_agent_service as svc
from app.services import ivyea_ops_tools

logger = logging.getLogger("ivyea.routers.ivyea_agent")


router = APIRouter(dependencies=[Depends(require_user)])
bridge_router = APIRouter()


class CodeBundleBody(BaseModel):
    root: str = Field(..., min_length=1, max_length=1000)
    goal: str = Field(..., min_length=1, max_length=4000)
    test_output: str = Field(default="", max_length=20000)
    limit: int = Field(default=8, ge=1, le=30)


class CodeApplyLoopBody(BaseModel):
    root: str = Field(..., min_length=1, max_length=1000)
    spec: dict[str, Any] = Field(default_factory=dict)
    test_command: str = Field(default="", max_length=1000)
    execute: bool = False
    timeout: int = Field(default=120, ge=1, le=1800)
    persist: bool = True


class ServiceStartBody(BaseModel):
    host: str = Field(default="127.0.0.1", min_length=1, max_length=120)
    port: int = Field(default=8765, ge=1, le=65535)
    allow_remote: bool = False
    api_token: str = Field(default="", max_length=4000)
    wait: bool = True
    timeout: float = Field(default=10.0, ge=1.0, le=60.0)


class ServiceStopBody(BaseModel):
    timeout: float = Field(default=10.0, ge=1.0, le=60.0)
    force: bool = False


class ServiceAutostartBody(BaseModel):
    host: str = Field(default="127.0.0.1", min_length=1, max_length=120)
    port: int = Field(default=8765, ge=1, le=65535)


class ProviderProbeBody(BaseModel):
    model: str = Field(default="", max_length=200)
    timeout: float = Field(default=30.0, ge=1.0, le=120.0)


class ModelCatalogBody(BaseModel):
    """列一个端点支持哪些模型。

    api_key 允许调用方现给：系统配置页在**保存之前**就要能看清单，而那时新填的
    key 还没落库。留空则由 agent 去它自己的 .env 里找。密钥不落 ops 的盘、不回显。
    """

    provider: str = Field(default="", max_length=80)
    base_url: str = Field(default="", max_length=500)
    api_key: str = Field(default="", max_length=500)
    refresh: bool = False


# 会话 id 会在 agent 那边直接拼成文件名。agent 侧已经在 sessions.path_for 堵了，
# 这里再收一道：越界的 id 根本不该出 ops 的门。
#
# 两头的 ^$ 和 (?:) 都是必须的：pydantic 的 pattern 走的是 **search 而不是 fullmatch**，
# 写成 "^$|[A-Za-z0-9_-]+$" 的话 "../../../tmp/x" 会因为末尾那个 x 被判通过。
_SESSION_ID = r"^(?:|[A-Za-z0-9_-]{1,120})$"


class ChatAttachment(BaseModel):
    """本轮附图在 payload 里的样子：视觉模型读出的文字 + 原图句柄，图片本体不下发。

    为什么要有它：这段文字原先塞在 `system` 里，而 agent 的 system 每轮重建、落盘
    时还被本轮那份覆盖 —— 用户贴图问完一轮，下一轮再问"你刚才怎么看到那张图的"，
    模型手里一个字都没有，只能否认自己看过图。走 attachments 的话 agent 会把它并进
    **user 消息**，跟着历史和存档一起走（agent ≥ v1.15.3）。
    """
    kind: str = Field(default="image", max_length=20)
    name: str = Field(default="", max_length=200)
    ref: str = Field(default="", max_length=200)
    # 代读这张图的视觉模型。用户问"你是怎么看到图的"时，模型得答得出具体是谁读的。
    by: str = Field(default="", max_length=120)
    text: str = Field(default="", max_length=8000)


class ChatBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=20000)
    session_id: str = Field(default="", max_length=120, pattern=_SESSION_ID)
    workspace: str = Field(default="", max_length=1000)
    asin: str = Field(default="", max_length=80)
    ops_context: dict[str, Any] = Field(default_factory=dict)
    # 0 = 不限定，交给 agent serve 的 config 默认（chat_max_tool_steps，默认 200）。
    # 旧默认 12/上限 80 把 serve 端预算压死，复杂运营任务动辄撞"工具步数上限"。
    max_steps: int = Field(default=0, ge=0, le=400)
    persist: bool = True
    plan_mode: bool = True
    inject_retrieval: bool = True
    # ── 以下字段 agent serve 早就支持，只是这个模型没开口子，工作台想按技能跑一轮
    #    或跑一轮纯文本都做不到。留空/False 时由 _chat_payload 剔除，
    #    daemon 看到的 payload 与改动前逐字一致 —— 老调用方零影响。
    skill: str = Field(default="", max_length=200)
    # 本轮用哪个主脑模型（agent ≥ v1.15.4），形如 "openrouter:x-ai/grok-4.6"。
    # 留空 = 用 agent 的全局主脑，且由 _chat_payload 整个剔除 —— 老 daemon 收到的
    # payload 与改动前逐字一致。任务台的模型选择器就是逐轮下发它：agent 的模型本来
    # 是全局的，真按全局切会把 ops 的其他用户和定时任务一起换掉。
    model: str = Field(default="", max_length=200)
    auto_skill: bool = False
    use_tools: bool = True
    turn_id: str = Field(default="", max_length=120)
    task_id: str = Field(default="", max_length=120)
    system: str = Field(default="", max_length=20000)
    # 本轮附图（视觉模型代读出来的文字 + 原图句柄）。空列表时由 _chat_payload 剔除。
    attachments: list[ChatAttachment] = Field(default_factory=list, max_length=4)
    defer_citation_text: bool = False
    # 要模型的思考流（agent ≥ v1.10.3）。默认关，且为 False 时从下发 payload 里剔除 ——
    # 老 daemon 看到的 payload 与改动前逐字一致。
    stream_reasoning: bool = False
    # 审批三档（agent ≥ v1.16）：
    #   none   = 只读，写操作一律不落地（维持今天的默认语义）
    #   remote = 逐项审批，每个写操作弹前端确认卡（agent ≥ v1.9）
    #   auto   = 完全放行，用户已为这一轮一次性授权，写操作不再弹卡
    approval: str = Field(default="none", pattern="^(none|remote|auto)$")
    # 会话来自哪个板块。**ops 自用**，_chat_payload 会把它剔掉再下发 ——
    # agent 不认识这个字段，带过去只会当成未知参数。
    source: str = Field(default="console", pattern="^(console|assistant|brain)$")
    # 这条流的另一端**有人在看、并且画得出选项卡**（agent ≥ v1.16.0）。
    # 只有它为 true 时 agent 才会把 ask_user_question 的选项弹过来 —— 服务端自己
    # 读流的那几处（技能执行、知识库问答）没有人能点，弹了只会白等一个超时。
    # 默认 False，且为 False 时由 _chat_payload 剔除，老 daemon 收到的 payload 不变。
    interactive: bool = False


class ChatSessionCreateBody(BaseModel):
    title: str = Field(default="", max_length=200)
    message: str = Field(default="", max_length=2000)


class KnowledgeUpdateBody(BaseModel):
    id: str = Field(default="", max_length=200)
    card_id: str = Field(default="", max_length=200)
    title: str = Field(default="", max_length=500)
    body: str = Field(default="", max_length=50000)
    source_url: str = Field(default="", max_length=2000)
    source_type: str = Field(default="user", max_length=80)
    confidence: str = Field(default="", max_length=80)
    license: str = Field(default="user_supplied", max_length=200)
    tags: list[str] = Field(default_factory=list)
    confirm: bool = False
    rebuild: bool = True


class KnowledgeUploadApplyBody(BaseModel):
    upload_id: str = Field(..., min_length=1, max_length=200)
    confirm: bool = False
    rebuild: bool = True


class KnowledgeImportDirectoryBody(BaseModel):
    root: str = Field(default="", max_length=1000)
    namespace: str = Field(default="gbrain", min_length=1, max_length=80)
    confirm: bool = False
    rebuild: bool = True
    max_files: int = Field(default=1000, ge=1, le=5000)
    max_file_bytes: int = Field(default=5 * 1024 * 1024, ge=1024, le=25 * 1024 * 1024)


class KnowledgeReviewBody(BaseModel):
    event_id: str = Field(..., min_length=1, max_length=120)
    decision: str = Field(..., pattern="^(approved|rejected|superseded)$")
    reviewer: str = Field(default="local-operator", max_length=80)
    note: str = Field(default="", max_length=1000)
    confirm: bool = False


class KnowledgeVersionRollbackBody(BaseModel):
    card_id: str = Field(..., min_length=1, max_length=240)
    version_id: str = Field(..., min_length=1, max_length=120)
    confirm: bool = False
    rebuild: bool = True


class KnowledgeChangeDraftBody(BaseModel):
    event_id: str = Field(..., min_length=1, max_length=120)
    card_id: str = Field(default="", max_length=240)
    new_card_id: str = Field(default="", max_length=240)
    title: str = Field(default="", max_length=500)
    body: str = Field(default="", max_length=500000)


class KnowledgeChangeApplyBody(KnowledgeChangeDraftBody):
    confirm: bool = False
    rebuild: bool = True


class KnowledgeSyncBody(BaseModel):
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    force: bool = False


class OpsToolsListBody(BaseModel):
    module: str = Field(default="", max_length=80)
    query: str = Field(default="", max_length=500)
    context: dict[str, Any] = Field(default_factory=dict)


class OpsToolCallBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    arguments: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


def _call(fn, *args, **kwargs) -> dict[str, Any]:
    try:
        return fn(*args, **kwargs)
    except svc.IvyeaAgentUnavailable as exc:
        status = svc.ensure_available()
        if status.get("available"):
            try:
                return fn(*args, **kwargs)
            except svc.IvyeaAgentUnavailable as retry_exc:
                raise HTTPException(status_code=503, detail=f"IvyeaAgent 不可用：{retry_exc}") from retry_exc
            except svc.IvyeaAgentError as retry_exc:
                raise HTTPException(status_code=502, detail=str(retry_exc)) from retry_exc
        raise HTTPException(status_code=503, detail=f"IvyeaAgent 不可用：{exc}") from exc
    except svc.IvyeaAgentError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _payload(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _bridge_base_url(request: Request) -> str:
    import os
    configured = (os.getenv("IVYEA_OPS_BRIDGE_URL") or "").strip()
    if configured:
        return configured.rstrip("/")
    return str(request.base_url).rstrip("/") + "/api/ivyea-agent-bridge"


def _with_ops_bridge(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    payload = dict(payload)
    payload["ops_bridge"] = {
        "base_url": _bridge_base_url(request),
        "token": ivyea_ops_tools.issue_bridge_token(),
    }
    ctx = payload.get("ops_context")
    if not isinstance(ctx, dict):
        ctx = {}
        payload["ops_context"] = ctx
    # **把这台机器上真正配好的能力如实告诉 agent。**
    #
    # 起因：用户在任务台说"帮我生成一张主图"，agent 回了一句"我目前没有图像生成
    # 能力"—— 而这台机器上生图链路早就配好了（系统配置 → AI 生图）。板块工具要靠
    # agent 主动调 ivyea_ops_list_tools 才发现得了，而没有任何东西提示它去查，
    # 它就直接下了否定结论。
    #
    # 只报**当前真的可用**的（读配置，不写死）—— 报一个没配 key 的能力，
    # 换来的是 agent 兴冲冲调用然后失败，比不报更糟。
    ctx.setdefault("host_capabilities", _host_capabilities())
    return payload


def _host_capabilities() -> list[str]:
    """本机当前可用的高价值能力，给 agent 当提示。任何探测失败都当"不可用"。"""
    caps: list[str] = []
    try:
        from app.routers.assistant import _image_cfg
        if _image_cfg().get("api_key"):
            caps.append("AI 作图：用 ivyea_ops_call_tool 调 image_generate 提交、"
                        "image_status 查结果。用户要出图/画图/生成主图时直接用，"
                        "不要回答做不到。")
    except Exception:  # noqa: BLE001
        logger.debug("生图能力探测失败（旁路，按不可用处理）", exc_info=True)
    try:
        from app.services import skill_market
        if skill_market.market_enabled():
            caps.append("能力市场已开启：可浏览门道社区技能。")
    except Exception:  # noqa: BLE001
        logger.debug("市场能力探测失败（旁路，按不可用处理）", exc_info=True)
    return caps


@router.get("/status")
def status() -> dict[str, Any]:
    return svc.ensure_available()


@router.get("/version")
def agent_version() -> dict[str, Any]:
    """IvyeaAgent 版本卡片：当前版本 + GitHub 最新版 + 是否有更新（供系统配置显示/更新按钮）。
    frozen=True 时 IvyeaAgent 是打包进 IvyeaOpsServer.exe 的、随 IvyeaOps 一起更新（无法独立升级）。
    latest_known=False 表示这次没能连上 GitHub 查到最新版（别当成"已是最新"，要提示无法检查）。"""
    import sys as _sys
    installed = svc._installed_agent_version("") or svc.agent_version()
    latest = svc.latest_agent_version()
    return {
        "version": svc.agent_version(),
        "installed": installed,
        "latest": latest,
        "latest_known": bool(latest),
        "frozen": bool(getattr(_sys, "frozen", False)),
        "update_available": svc.agent_update_available(installed, latest),
        "available": svc.availability().get("available", False),
    }


_UPGRADE_LOCK = _threading.Lock()
_UPGRADE_STATE: dict[str, Any] = {"phase": "idle", "percent": 0, "before": "", "after": "",
                                  "ok": None, "note": "", "error": ""}


def _upgrade_worker() -> None:
    def _progress(phase: str, pct: int) -> None:
        _UPGRADE_STATE.update(phase=phase, percent=pct)
    try:
        res = svc.upgrade_agent(progress=_progress)
        _UPGRADE_STATE.update(phase="done" if res.get("ok") else "error", percent=100,
                              before=res.get("before", ""), after=res.get("after", ""),
                              ok=res.get("ok"), note=res.get("note", ""),
                              error=res.get("error", ""))
    except Exception as exc:  # noqa: BLE001
        _UPGRADE_STATE.update(phase="error", percent=100, ok=False, error=str(exc))


@router.post("/upgrade")
def upgrade(_admin: str = Depends(require_admin)) -> dict[str, Any]:
    """Start a background IvyeaAgent upgrade (pip -U from git + serve restart) and
    return immediately. The UI polls /ivyea-agent/upgrade/progress for a progress
    bar — no more blocking the request until a slow pip times out."""
    with _UPGRADE_LOCK:
        if _UPGRADE_STATE["phase"] in ("preparing", "downloading", "restarting"):
            return {"started": True, "already_running": True}
        _UPGRADE_STATE.update(phase="preparing", percent=0, before="", after="",
                              ok=None, note="", error="")
        _threading.Thread(target=_upgrade_worker, daemon=True, name="ivyea-agent-upgrade").start()
    return {"started": True}


@router.get("/upgrade/progress")
def upgrade_progress(_admin: str = Depends(require_admin)) -> dict[str, Any]:
    return dict(_UPGRADE_STATE)


@router.get("/bootstrap")
def bootstrap() -> dict[str, Any]:
    return _call(svc.bootstrap)


@router.get("/manifest")
def manifest() -> dict[str, Any]:
    return _call(svc.manifest)


# 新增的可选字段：取这些"等于没传"的值时直接从 payload 剔除，让 daemon 走它自己
# 的默认分支。这样一次普通对话发出去的 payload 与加字段之前完全一样，不会因为多
# 塞了几个 ""/False 改变 serve 的行为。
_CHAT_OPTIONAL_DEFAULTS: dict[str, Any] = {
    "skill": "",
    "model": "",
    "auto_skill": False,
    "turn_id": "",
    "task_id": "",
    "system": "",
    "attachments": [],
    "defer_citation_text": False,
    "stream_reasoning": False,
    "interactive": False,
    "approval": "none",
    # source 是 ops 自己的记账字段（会话来自任务台/AI问答/知识库），agent 不认识它。
    # 放进 defaults 里只是为了默认值被剔除；非默认值另有 _pop_ops_only 兜底。
    "source": "console",
}


def _chat_payload(body: ChatBody) -> dict[str, Any]:
    """max_steps<=0 时从 payload 里剔除，serve 端回落到 config 默认（200）。"""
    payload = _payload(body)
    if int(payload.get("max_steps") or 0) <= 0:
        payload.pop("max_steps", None)
    for key, blank in _CHAT_OPTIONAL_DEFAULTS.items():
        if payload.get(key) == blank:
            payload.pop(key, None)
    # use_tools 默认 True；只有显式关掉才需要告诉 daemon（它的默认也是带工具）。
    if payload.get("use_tools") is not False:
        payload.pop("use_tools", None)
    # source 无论取什么值都不该出现在下发给 daemon 的 payload 里。
    payload.pop("source", None)
    return payload


# ── 写操作审批的归属登记 ────────────────────────────────────────────────────
# 批准一个 permission_request 就是**授权一次真实写入**，所以必须确认按下确认的人
# 就是发起这轮对话的人 —— 否则任何登录用户只要猜到（或看到）一个 request_id，
# 就能替别人批准改广告、开领星可写开关。
#
# request_id 由 agent daemon 现场生成，ops 事先不知道。所以在**转发** SSE 时顺
# 手记一笔：字节原样透传（心跳注释行必须完好，否则慢工具轮次会被中间层掐断），
# 只额外扫一眼帧里有没有 permission_request，有就登记归属。
_APPROVAL_OWNERS: dict[str, tuple[str, float]] = {}
_APPROVAL_OWNERS_LOCK = _threading.Lock()
_APPROVAL_OWNER_TTL = 45 * 60.0     # 比 agent 侧 10 分钟的审批超时宽裕得多


def _notify_approval(title: str, op_type: str) -> None:
    """发送放后台线程，且任何失败都吞掉 —— 这里在转发 agent 的流式帧，
    绝不能因为一个 webhook 慢把对话卡住。"""
    try:
        from app.services import notify
        if not notify.webhook_url():
            return

        def _fire() -> None:
            try:
                notify.send_sync("approval.needed", "有操作在等你确认",
                                 f"{op_type}：{title}".strip("：") or "Agent 请求执行一个操作",
                                 level="warn")
            except Exception:  # noqa: BLE001
                logger.debug("审批通知失败（旁路，已忽略）", exc_info=True)

        _threading.Thread(target=_fire, name="notify-approval", daemon=True).start()
    except Exception:  # noqa: BLE001
        logger.debug("审批通知调度失败（旁路，已忽略）", exc_info=True)


def _remember_approval_owner(request_id: str, principal: str) -> None:
    now = _time.time()
    with _APPROVAL_OWNERS_LOCK:
        stale = [k for k, (_, ts) in _APPROVAL_OWNERS.items() if now - ts > _APPROVAL_OWNER_TTL]
        for key in stale:
            _APPROVAL_OWNERS.pop(key, None)
        _APPROVAL_OWNERS[request_id] = (principal, now)


def _approval_owner(request_id: str) -> str | None:
    with _APPROVAL_OWNERS_LOCK:
        row = _APPROVAL_OWNERS.get(request_id)
    if not row:
        return None
    principal, ts = row
    if _time.time() - ts > _APPROVAL_OWNER_TTL:
        return None
    return principal


# 自动生成的会话标题最多这么长。左栏一行放得下，多了只会被裁成省略号。
_TITLE_MAX = 18

_TITLE_PROMPT = (
    "给下面这段对话起一个标题。要求：\n"
    "1. 只输出标题本身，不要引号、不要句号、不要「标题：」之类的前缀\n"
    "2. 中文，不超过 14 个字\n"
    "3. 概括这段对话在**做什么**，而不是复述用户的第一句话\n\n"
    "用户：{ask}\n\n助手：{answer}\n"
)


def _auto_title_session(session_id: str) -> None:
    """给会话起个名字 —— 按**内容**起，而不是拿第一条指令顶上去。

    左栏此前显示的是用户打的第一句话。可第一句话往往是"帮我看下这个""继续"
    "这个报错怎么回事"，十条会话有六条长得一模一样，列表因此变成一堆认不出来的
    重复项 —— 用户只能一条条点开找。ChatGPT 那种"看完这一轮再起名"的做法之所以
    有用，就是因为标题说的是**这段对话在做什么**。

    只在标题还空着时做一次；生成失败就什么也不改（列表自动退回显示第一句话，
    和改动前一样）。整个过程在后台线程里跑，绝不挡住这一轮的回答。
    """
    try:
        row = console_sessions.session_row(session_id)
        if row is None or str(row.get("title") or "").strip():
            return                      # 手动改过名 / 已经起过名 —— 不覆盖
        detail = svc.chat_session(session_id, turns=1, before=1)
        msgs = ((detail or {}).get("session") or {}).get("messages") or []
        ask = answer = ""
        for m in msgs:
            role, content = str(m.get("role") or ""), str(m.get("content") or "")
            if role == "user" and not ask:
                ask = console_sessions.clean_preview(content)
            elif role == "assistant" and content.strip() and not answer:
                answer = content
        if not ask.strip():
            return
        from app.services.ai_synthesis_service import generate_text
        raw = asyncio.run(generate_text(
            _TITLE_PROMPT.format(ask=ask[:600], answer=answer[:800] or "（这一轮还没有正文）"),
            inject_retrieval=False))
        # 模型偶尔会连着解释一起吐出来 —— 只取第一行，并把包裹用的标点剥掉。
        # [K1] 这类引用标记也要清掉：关了检索注入之后正常不会再有，但真漏出来一个，
        # 标题就会变成"…无法读取[K2"这种半截样子（实测过一次）。
        title = re.sub(r"\s*\[K\d+\]?\s*$", "",
                       (raw or "").strip().splitlines()[0]).strip().strip('"\'「」『』 　:：')
        if not title or len(title) > _TITLE_MAX * 2:
            return                      # 明显不是个标题（吐了一整段）→ 宁可不改
        console_sessions.update_session(session_id, title=title[:_TITLE_MAX])
    except Exception:  # noqa: BLE001 — 起名失败只是列表里显示第一句话，不值得打扰用户
        return


def _tee_session_events(chunks: Any, principal: str, workspace: str = "",
                        source: str = "console", persist: bool = True) -> Any:
    """原样转发 SSE 字节，同时从流里捞两件 ops 需要记账的事：

    - ``permission_request`` → 登记审批归属（谁能批这一步）
    - ``start`` → 登记会话归属（session_id 是 agent 现场生成的，只有流里才拿得到）

    只读不改：先 yield 再解析，任何解析异常都不许影响转发 —— 记账失败最坏是让
    用户点确认时被判 404（agent 侧会超时拒绝，方向安全）、或会话没进左栏列表；
    而弄坏转发会直接毁掉整轮对话。
    """
    buf = b""
    live_sid = ""
    try:
        for chunk in chunks:
            yield chunk
            try:
                buf += chunk
                while b"\n\n" in buf:
                    frame, buf = buf.split(b"\n\n", 1)
                    is_start = b"event: start" in frame
                    is_req = b"permission_request" in frame
                    is_timeout = b"permission_timeout" in frame
                    if not (is_start or is_req or is_timeout):
                        continue
                    for line in frame.split(b"\n"):
                        if not line.startswith(b"data:"):
                            continue
                        data = _json.loads(line[5:].strip().decode("utf-8", "replace"))
                        if is_start:
                            sid = str(data.get("session_id") or "")
                            # persist=False 的轮次 agent 不落盘（跟进建议、各处的一次性
                            # 调用都走这条）。照样建索引的话，就会攒下一堆指向不存在会话
                            # 的孤儿行 —— 界面上看不见（列表要和 agent 实存的对得上），
                            # 但只增不减。
                            if sid and persist:
                                console_sessions.register_session(sid, principal, workspace, source)
                                live_sid = sid
                            continue
                        rid = str(data.get("request_id") or "")
                        if not rid:
                            continue
                        if is_timeout:
                            # 超时被自动拒 —— 这也是一条要留下的决定，而且是最容易
                            # 被忽略的那种（没人点，但那一步确实没执行）。
                            console_sessions.record_approval_decision(rid, "timeout")
                            continue
                        _remember_approval_owner(rid, principal)
                        title = str(data.get("title") or "")
                        console_sessions.record_approval_request(
                            rid, str(data.get("session_id") or ""), principal,
                            title, str(data.get("op_type") or ""))
                        # 推一条到用户配的渠道。**这是"手机上审批"能成立的前提** ——
                        # 人不在电脑前时，没有这条推送他根本不知道有东西在等他，
                        # agent 就会一直卡到超时被自动拒。
                        _notify_approval(title, str(data.get("op_type") or ""))
                # 单帧异常大（final 会带整段会话）时别把内存吃着不放
                if len(buf) > 2_000_000:
                    buf = buf[-4096:]
            except Exception:  # noqa: BLE001 — 记账失败绝不能影响转发
                buf = b""
    finally:
        # 这一轮转发完了（正常收尾、用户中断、断链都会走到这里）才给会话起名：
        # 起名要读这一轮的正文、还要调一次模型 —— 放在流里做等于让用户多等。
        if live_sid:
            _threading.Thread(target=_auto_title_session, args=(live_sid,),
                              name="console-auto-title", daemon=True).start()


def _resolve_workspace(body: ChatBody, user: str) -> tuple[dict[str, Any], str]:
    """把 payload 里的工作区**名字**换算成真实目录，返回 (payload, 名字)。

    这两件事很容易混：`ChatBody.workspace` 最终落到 `ToolContext.workspace`，那是
    **agent 文件类工具的工作目录**；而任务台前端送上来的是给人看的分组名（可能是
    中文）。不换算就会把"选品调研"当成一个目录路径 —— 相对路径的文件操作全指向
    一个不存在的地方。分组仍然按名字记（tee 用它登记会话归属）。
    """
    payload = _chat_payload(body)
    name = str(body.workspace or "")
    if name:
        payload["workspace"] = console_sessions.workspace_path(name, user)
        if not payload["workspace"]:
            payload.pop("workspace", None)      # 没绑目录 = 用 agent 的默认 cwd
    return payload, name


@router.post("/chat")
def chat(body: ChatBody, request: Request,
         user: str = Depends(require_user)) -> dict[str, Any]:
    payload, _ = _resolve_workspace(body, user)
    return _call(svc.chat, _with_ops_bridge(payload, request))


@router.post("/chat/stream")
def chat_stream(body: ChatBody, request: Request,
                user: str = Depends(require_user)) -> StreamingResponse:
    status = svc.ensure_available()
    if not status.get("available"):
        raise HTTPException(status_code=503, detail=f"IvyeaAgent 不可用：{status.get('error') or '服务未连接'}")
    payload, ws_name = _resolve_workspace(body, user)
    return StreamingResponse(
        _tee_session_events(
            svc.chat_stream(_with_ops_bridge(payload, request)),
            user, ws_name, body.source, body.persist,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


class ChatPermissionBody(BaseModel):
    request_id: str = Field(..., min_length=1, max_length=120)
    session_id: str = Field(default="", max_length=120, pattern=_SESSION_ID)
    choice: str = Field(..., pattern="^(approve|session|deny|abort)$")


@router.post("/chat/permission")
def chat_permission(body: ChatPermissionBody,
                    user: str = Depends(require_user)) -> dict[str, Any]:
    """回送一次写操作审批决策，解开 agent 侧阻塞的那一步。"""
    owner = _approval_owner(body.request_id)
    if owner is None:
        # 没登记过 = 已超时被拒、轮次已收尾，或 ops 重启丢了记录。一律当失效，
        # 不放行。agent 侧那一步最终会被超时拒绝，失败方向是安全的。
        raise HTTPException(status_code=404, detail="该审批请求不存在或已失效")
    if owner != user:
        raise HTTPException(status_code=403, detail="无权处理他人会话的审批请求")
    try:
        out = _call(svc.chat_permission, {"request_id": body.request_id, "choice": body.choice})
    except HTTPException as exc:
        # daemon 对"未知/已过期"回 404，而 _call 把 agent 的一切非 2xx 都翻成 502。
        # 502 在界面上读作"服务器坏了"，但这里的真相通常是**另一个标签页已经点过了**
        # ——同一个人开两个页签、或手快点了两下，都会走到这。实测并发点两次确实
        # 一次 200 一次 502。翻成 409 并说人话。
        if exc.status_code == 502 and "HTTP 404" in str(exc.detail):
            raise HTTPException(
                status_code=409,
                detail="这条审批已经被处理过了（可能是另一个页签点的），或者已经超时失效。",
            ) from exc
        raise
    # agent 确认收下之后才留痕 —— 先记再发的话，agent 那边没收到（超时/断连）
    # 就会留下一条"已批准"，而那一步其实根本没执行。
    if out.get("ok"):
        console_sessions.record_approval_decision(body.request_id, body.choice)
    return out


class ChatInjectBody(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=120, pattern=_SESSION_ID)
    text: str = Field(..., min_length=1, max_length=8000)


@router.post("/chat/inject")
def chat_inject(body: ChatInjectBody,
                info: dict[str, Any] = Depends(require_user_info)) -> dict[str, Any]:
    """把一条追加指令送进这条会话**正在跑的那一轮**。

    这是"任务跑起来之后还能补一句"的落点：agent 在两个工具步之间把它作为一条
    真实的用户消息插进当前上下文，模型下一步就看得见。

    没有活轮时回 `accepted: false`（不是错误）—— 前端据此把这句话当成下一轮发出去。
    """
    principal, is_admin = _principal_info(info)
    if not console_sessions.can_access(body.session_id, principal, is_admin):
        raise HTTPException(status_code=403, detail="这条会话不属于你")
    return _call(svc.chat_inject, {"session_id": body.session_id, "text": body.text})


class ChatQuestionBody(BaseModel):
    request_id: str = Field(..., min_length=1, max_length=120)
    # **必填**：归属按会话判（见下），不是可选的补充信息。
    session_id: str = Field(..., min_length=1, max_length=120, pattern=_SESSION_ID)
    answers: dict[str, str] = Field(default_factory=dict)


@router.post("/chat/question")
def chat_question(body: ChatQuestionBody,
                  info: dict[str, Any] = Depends(require_user_info)) -> dict[str, Any]:
    """回送一次选项卡的选择。

    **归属按会话判，不按内存里的 request_id 登记表判。** 那张表是给写操作审批用的：
    批准一次真实写入必须认准"按下确认的就是发起这轮的人"，所以它宁可严格到 ops
    一重启就全部失效。选项卡不是写操作，用同一套的代价是：ops 重启之后，用户面前
    那张卡片就点不动了（agent 那边还老老实实等着人选），只能干等五分钟超时。

    会话归属是落在库里的，重启还在；而"这张卡还有效吗"本来就该由 agent 说了算
    （它超时之后自己按推荐项继续，回 404）。两边各管各的那一半。
    """
    principal, is_admin = _principal_info(info)
    if not body.answers:
        raise HTTPException(status_code=400, detail="answers 不能为空")
    if not console_sessions.can_access(body.session_id, principal, is_admin):
        raise HTTPException(status_code=403, detail="无权回答他人会话的选项卡")
    try:
        return _call(svc.chat_question, {"request_id": body.request_id,
                                         "answers": dict(body.answers)})
    except HTTPException as exc:
        # daemon 对"未知/已过期"回 404，而 _call 把非 2xx 一律翻成 502 ——
        # 502 在界面上读作"服务器坏了"，真相通常是另一个页签已经选过了。
        if exc.status_code == 502 and "HTTP 404" in str(exc.detail):
            raise HTTPException(
                status_code=409,
                detail="这张选项卡已经被处理过了（可能是另一个页签选的），或者已经超时。",
            ) from exc
        raise


class ChatCancelBody(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=120, pattern=_SESSION_ID)


@router.post("/chat/cancel")
def chat_cancel(body: ChatCancelBody,
                info: dict[str, Any] = Depends(require_user_info)) -> dict[str, Any]:
    """真的停掉这条会话正在跑的那一轮。

    此前任务台的「停止」只是前端 abort 掉自己那条事件流 —— 轮次在 agent 那边照跑、
    照烧 token。用户的原话："有的任务不想做了却无法终止，难道要一直烧 token 吗"。

    这里让它真停：agent 在模型流的下一个事件或下一个工具步边界收摊，已经跑出来的
    正文和执行过程照常落盘。正在执行中的那一个工具调用不会被打断（写文件、跑命令
    中途砸断只会留下半个现场）。
    """
    principal, is_admin = _principal_info(info)
    if not console_sessions.can_access(body.session_id, principal, is_admin):
        raise HTTPException(status_code=403, detail="这条会话不属于你")
    return _call(svc.chat_cancel, {"session_id": body.session_id})


@router.get("/chat/live-sessions")
def chat_live_sessions(info: dict[str, Any] = Depends(require_user_info)) -> dict[str, Any]:
    """此刻真的有一轮在跑的会话（只回你自己的）。左栏的闪烁标记读它。

    agent 不在或版本太老时回 `available: false` 而不是空列表：**"问不到"不等于
    "没有在跑的"**，前端据此不显示标记，而不是把正在执行的会话画成已停。
    """
    principal, is_admin = _principal_info(info)
    try:
        rows = (_call(svc.chat_live_sessions) or {}).get("sessions") or []
    except HTTPException:
        return {"ok": True, "available": False, "sessions": []}
    mine = [r for r in rows
            if console_sessions.can_access(str(r.get("id") or ""), principal, is_admin)]
    return {"ok": True, "available": True, "sessions": mine}


@router.get("/chat/sessions")
def chat_sessions(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    return _call(svc.chat_sessions, limit)


@router.get("/chat/sessions/{session_id}")
def chat_session(session_id: str,
                 turns: int = Query(8, ge=1, le=100),
                 before: int | None = Query(None, ge=0)) -> dict[str, Any]:
    """历史会话详情，**按轮**分页。

    按条分页是这个板块吃过的亏：一次提问能产生几十条消息，按条切必然把用户自己
    发的那句话挤出窗口（agent 侧此前固定末 30 条，413 条消息的会话刷新后 15 次
    提问只剩 1 次）。before 是"从第几轮往前取"，翻更早的对话时传上一页的 from。
    """
    return _call(svc.chat_session, session_id, turns, before)


@router.get("/chat/sessions/{session_id}/live")
def chat_session_live(session_id: str,
                      from_seq: int = Query(0, alias="from", ge=0),
                      info: dict[str, Any] = Depends(require_user_info)) -> StreamingResponse:
    """订阅这条会话正在跑的那一轮。

    任务台切走再切回来、刷新、甚至换台机器打开同一条会话，都从这里把执行过程接上
    —— 此前那份进度只活在发起它的那个标签页的内存里，页面一卸载就没了，用户看到的
    是"只剩自己发的那句话，后台明明在跑却什么都看不到"。

    **要鉴权**：这条流里跑的是整轮对话的正文和执行细节。
    """
    principal, is_admin = _principal_info(info)
    if not console_sessions.can_access(session_id, principal, is_admin):
        raise HTTPException(status_code=403, detail="这条会话不属于你")
    return StreamingResponse(
        svc.chat_session_live(session_id, from_seq),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/chat/sessions/{session_id}")
def chat_session_delete(session_id: str) -> dict[str, Any]:
    return _call(svc.chat_session_delete, session_id)


@router.post("/chat/sessions")
def chat_create(body: ChatSessionCreateBody) -> dict[str, Any]:
    return _call(svc.chat_create, _payload(body))


@router.get("/skills")
def skills() -> dict[str, Any]:
    return _call(svc.skills)


@router.get("/skills/search")
def skills_search(
    q: str = Query("", max_length=1000),
    limit: int = Query(8, ge=1, le=50),
) -> dict[str, Any]:
    if not q.strip():
        return _call(svc.skills)
    return _call(svc.skills_search, q, limit)


# ── 任务台会话与工作区 ──────────────────────────────────────────────────────

def _principal_info(info: dict[str, Any]) -> tuple[str, bool]:
    """(邮箱, 是不是管理员)。用于会话归属过滤。"""
    return str(info.get("email") or info.get("id") or ""), (info.get("role") == "admin")


class ConsoleSessionPatch(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    workspace: str | None = Field(default=None, max_length=120)


class ConsoleWorkspaceBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    path: str = Field(default="", max_length=1000)


# 一次从 agent 捞多少条来做过滤/搜索。**不是页大小** —— 归属、来源、搜索都在
# ops 这边算，所以要先拿到足够大的一批才谈得上翻页。agent 那边是本地文件扫描，
# 实测 162 个会话热态 55ms。
_SESSION_SCAN = 500


# 用 Annotated 而不是 `x: str = Query("")`：后者的**默认值是 Query 对象本身**，
# 直接调用这个函数（测试就是这么调的）会把它原样传下去，然后在 SQL 绑定处炸成
# "unsupported type"。踩过两次 —— 每加一个查询参数就连坐一批测试。
@router.get("/console/sessions")
def console_session_list(
    workspace: Annotated[str, Query(max_length=120)] = "",
    source: Annotated[str, Query(max_length=20)] = "",
    q: Annotated[str, Query(max_length=120)] = "",
    offset: Annotated[int, Query(ge=0, le=5000)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 60,
    info: dict[str, Any] = Depends(require_user_info),
) -> dict[str, Any]:
    """任务台左栏的会话列表：agent 那边的正文摘要 + ops 这边的归属/工作区/自定义标题。

    **按归属过滤**：agent 的会话库是整机共享的，原样端出来等于把同事的对话摆在
    每个人眼前。管理员看全部（那是他自己的机器），普通用户只看自己开的；
    索引里没有的历史会话对普通用户不可见。

    **搜索和分页都在服务端**：左栏一次只显示一页，纯前端过滤只能过滤"已经拿到的
    那一页"，搜早期的会话会一无所获 —— 那比没有搜索更糟，因为它看着像是真的没有。
    """
    principal, is_admin = _principal_info(info)
    index = console_sessions.owned_sessions(principal, is_admin, workspace, source)
    agent_ok = True
    try:
        listing = (_call(svc.chat_sessions, _SESSION_SCAN) or {}).get("sessions") or []
    except HTTPException:
        # agent 不在时别让左栏静默变成"0 条" —— 那看着像会话都没了。
        # 明确告诉前端是读不到，不是真的空。
        listing, agent_ok = [], False

    rows: list[dict[str, Any]] = []
    for item in listing:
        sid = str(item.get("id") or "")
        meta = index.get(sid)
        # agent 落盘时记下的"这条会话是在哪儿开的"。终端里敲 `ivyea chat` 开的会话
        # 带 origin="cli"；网页开的和装这个字段之前的老会话都是空串。
        origin = str(item.get("origin") or "")
        if meta is None:
            # 未登记：管理员能看到（机器上的历史会话），普通用户不给。
            if not is_admin or workspace:
                continue
            # 终端会话在这里**推得出来源**：agent 标了 origin，不再是"没有来源可判"
            # 的裸会话，所以按来源筛「终端」时要留下它。其余未登记的会话仍然无源可判，
            # 一按来源筛就照旧排除 —— 混进结果里就是噪音。
            #
            # 注意这里**只读不写**：不顺手把它登记进索引表。register_session 的归属
            # 是"不覆盖"的，后台顺手登记等于替用户做了一个之后只能手改库才能撤销的
            # 决定。真要落行，等他自己动手改名时再落（见 console_session_patch）。
            guessed = "cli" if origin == "cli" else ""
            if source and source != guessed:
                continue
            meta = {"workspace": "", "title": "", "principal": "", "source": guessed}
        preview = console_sessions.clean_preview(item.get("preview") or "")
        rows.append({
            "id": sid,
            "title": meta.get("title") or preview or sid,
            "preview": preview,
            "turns": item.get("turns") or 0,
            "updated": item.get("updated") or 0,
            "workspace": meta.get("workspace") or "",
            "owner": meta.get("principal") or "",
            "source": meta.get("source") or "",
            "indexed": sid in index,
            # 终端会话开在哪个目录 —— **只作为展示标签**，不是工作区。
            # 绝不能拿它去建 console_workspaces 行：给工作区绑目录是一次授权
            # （agent 的文件类工具会落在那儿，本来仅限管理员手动绑），从 cwd
            # 自动建等于静默把一片文件系统访问面开出去。
            "cwd": str(item.get("cwd") or ""),
            # 这条会话此刻有没有一轮在跑（老 agent 不回这个字段 → 不显示标记，
            # 而不是画成"已停"）。
            "running": bool(item.get("running")),
        })
    needle = q.strip().lower()
    if needle:
        # 标题（含用户自己改的名）和首句摘要都能搜到 —— 正文在 agent 那边，
        # 逐条读进来做全文搜索会把这个接口拖成秒级，先不做，界面上也没暗示能搜正文。
        rows = [r for r in rows
                if needle in str(r.get("title") or "").lower()
                or needle in str(r.get("preview") or "").lower()]
    rows.sort(key=lambda r: r.get("updated") or 0, reverse=True)
    total = len(rows)
    page = rows[offset:offset + limit]
    # 每个工作区的**真实**条数。左栏此前把"当前这页里属于它的条数"当成计数显示，
    # 于是一个有 211 条会话的工作区在只加载了 60 条时显示成 60 —— 看着像会话丢了。
    # 分页前的 rows 就是全量，这里顺手数一遍，不额外查库。
    ws_counts: dict[str, int] = {}
    for r in rows:
        key = r.get("workspace") or console_sessions.DEFAULT_WORKSPACE
        ws_counts[key] = ws_counts.get(key, 0) + 1
    workspaces = console_sessions.list_workspaces(principal, is_admin)
    for w in workspaces:
        w["count"] = ws_counts.get(w["name"], 0)
    return {"ok": True, "sessions": page, "agent_available": agent_ok,
            "total": total, "offset": offset,
            # 让前端不必自己算 —— 算错了就是"加载更多"点了没反应
            "has_more": offset + len(page) < total,
            "workspaces": workspaces}


@router.patch("/console/sessions/{session_id}")
def console_session_patch(session_id: str, body: ConsoleSessionPatch,
                          info: dict[str, Any] = Depends(require_user_info)) -> dict[str, Any]:
    principal, is_admin = _principal_info(info)
    if not console_sessions.can_access(session_id, principal, is_admin):
        raise HTTPException(status_code=403, detail="无权修改他人的会话")
    # 索引里没这一行的会话（终端里开的、装这套之前就有的）先补一行再改：
    # update_session 是纯 UPDATE，没有行就**一声不响地什么也不做** —— 用户改完名
    # 看着像成功了，刷新一下又变回去。这是登记这类会话的唯一时机：他自己动手改名，
    # 而不是列表接口后台顺手替他决定归属（register_session 的归属不覆盖，
    # 判错了只能手改库）。
    if console_sessions.session_row(session_id) is None:
        detail = _call(svc.chat_sessions, _SESSION_SCAN) or {}
        origin = next((str(s.get("origin") or "") for s in (detail.get("sessions") or [])
                       if str(s.get("id") or "") == session_id), "")
        console_sessions.register_session(session_id, principal,
                                          source="cli" if origin == "cli" else "console")
    console_sessions.update_session(session_id, title=body.title, workspace=body.workspace)
    return {"ok": True, "session_id": session_id}


@router.delete("/console/sessions/{session_id}")
def console_session_delete(session_id: str,
                           info: dict[str, Any] = Depends(require_user_info)) -> dict[str, Any]:
    """删会话：先删 agent 那边的正文，再清掉索引。"""
    principal, is_admin = _principal_info(info)
    if not console_sessions.can_access(session_id, principal, is_admin):
        raise HTTPException(status_code=403, detail="无权删除他人的会话")
    try:
        _call(svc.chat_session_delete, session_id)
    except HTTPException as exc:
        # 404 = agent 那边本来就没有（手工删过/过期），索引照样要清干净。
        # 502/503 = agent 不可达 —— 这时**绝不能报成功**：正文还原封不动躺在磁盘上，
        # 只把索引删掉的话，用户看着条目消失以为删干净了，其实内容还在
        # （管理员的列表里还会再冒出来）。实测踩过：agent 短暂不可达时删除"成功"，
        # 文件仍在。
        if exc.status_code not in (404,):
            raise HTTPException(
                status_code=503,
                detail="IvyeaAgent 暂时不可达，会话内容没有被删除。请稍后重试。",
            ) from exc
    console_sessions.forget_session(session_id)
    return {"ok": True, "deleted": session_id}


class ImportedMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., min_length=1, max_length=100000)


class ImportedSession(BaseModel):
    """一条从别处（localStorage / 旧板块）搬过来的会话。

    id 这里不必再排除 Windows 保留设备名：它最终会被加上 `imp-<来源>-` 前缀，
    拼出来的东西不可能等于 CON/NUL 这些。
    """
    id: str = Field(..., min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    created: float = 0.0
    messages: list[ImportedMessage] = Field(default_factory=list, max_length=200)


class ConsoleImportBody(BaseModel):
    source: str = Field(default="assistant", pattern="^(assistant|brain)$")
    sessions: list[ImportedSession] = Field(default_factory=list, max_length=100)


@router.post("/console/sessions/import")
def console_session_import(body: ConsoleImportBody,
                           info: dict[str, Any] = Depends(require_user_info)) -> dict[str, Any]:
    """把外部会话搬进 agent 的会话库（不跑模型，只落盘）。

    **幂等**靠 id 前缀做到：同一条来源会话每次都算出同一个 agent id，
    agent 的 import 是按 id 覆盖写 —— 重复导入是覆盖，不会生出第二条。
    所以浏览器上重复点、或者两个标签页同时点，结果都一样。

    前缀还顺带把导入来的 id 和 agent 自己生成的（时间戳-毫秒-随机）分开，
    互相撞不上。
    """
    principal, _is_admin = _principal_info(info)
    imported: list[str] = []
    skipped = 0
    for item in body.sessions:
        msgs = [{"role": m.role, "content": m.content} for m in item.messages if m.content.strip()]
        if not msgs:
            skipped += 1
            continue
        sid = f"imp-{body.source}-{item.id}"
        payload: dict[str, Any] = {"id": sid, "messages": msgs}
        if item.created > 0:
            payload["created"] = item.created
        data = _call(svc.chat_import, payload)
        if not data.get("ok"):
            skipped += 1
            continue
        console_sessions.register_session(sid, principal, "", body.source)
        imported.append(sid)
    return {"ok": True, "imported": imported, "count": len(imported), "skipped": skipped}


class ConsolePresetBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    skill: str = Field(default="", max_length=200)
    # 与任务台的三档一致：none 只读 / remote 逐项审批 / auto 完全放行
    approval: str = Field(default="none", pattern="^(none|remote|auto)$")
    workspace: str = Field(default="", max_length=120)
    # 人设：整段进这一轮的系统提示。上限比 note 大得多，但不能没有 ——
    # 它每轮都要占上下文。
    system: str = Field(default="", max_length=4000)
    note: str = Field(default="", max_length=500)


@router.get("/console/approvals/pending")
def console_pending_approvals(info: dict[str, Any] = Depends(require_user_info)) -> dict[str, Any]:
    """我名下所有还没决定的审批，跨会话。

    这是"手机上点同意/拒绝"的入口。按会话查的那个接口在手机上用不了 ——
    要先知道是哪个会话、点进去、再在长对话里找到那张卡片。

    **只返回自己的**：principal 直接取自会话身份，不接受查询参数指定别人。
    """
    principal, _ = _principal_info(info)
    # 先对账再取：agent 报的"还在等"是唯一真相，我们这张表只是流水账。不对账的话
    # 页面关掉/断链/agent 重启留下的僵尸卡片会一直挂在这里，点了只会 409。
    try:
        console_sessions.expire_stale_approvals(svc.pending_permissions())
    except Exception:  # noqa: BLE001 — 对账失败不能让整页打不开
        # 大不了多显示一张过期卡片；但要留下痕迹，否则"待审批莫名其妙没清干净"
        # 会变成一个查不出来的问题。
        logger.debug("待审批对账失败", exc_info=True)
    return {"ok": True, "approvals": console_sessions.pending_approvals(principal)}


@router.get("/console/sessions/{session_id}/approvals")
def console_session_approvals(session_id: str,
                              info: dict[str, Any] = Depends(require_user_info)) -> dict[str, Any]:
    """一条会话的审批留痕。刷新页面、隔天回来翻，记录都还在。"""
    principal, is_admin = _principal_info(info)
    if not console_sessions.can_access(session_id, principal, is_admin):
        raise HTTPException(status_code=403, detail="这条会话不属于你")
    return {"ok": True, "approvals": console_sessions.session_approvals(session_id)}


@router.get("/console/presets")
def console_preset_list(info: dict[str, Any] = Depends(require_user_info)) -> dict[str, Any]:
    principal, _ = _principal_info(info)
    return {"ok": True, "presets": console_sessions.list_presets(principal)}


@router.post("/console/presets")
def console_preset_save(body: ConsolePresetBody,
                        info: dict[str, Any] = Depends(require_user_info)) -> dict[str, Any]:
    principal, _ = _principal_info(info)
    try:
        row = console_sessions.save_preset(
            body.name, principal, skill=body.skill, approval=body.approval,
            workspace=body.workspace, note=body.note, system=body.system)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "preset": row}


@router.delete("/console/presets/{name}")
def console_preset_delete(name: str,
                          info: dict[str, Any] = Depends(require_user_info)) -> dict[str, Any]:
    principal, _ = _principal_info(info)
    if not console_sessions.delete_preset(name, principal):
        raise HTTPException(status_code=404, detail="预设不存在")
    return {"ok": True, "deleted": name}


@router.get("/console/workspaces")
def console_workspace_list(info: dict[str, Any] = Depends(require_user_info)) -> dict[str, Any]:
    principal, is_admin = _principal_info(info)
    return {"ok": True, "workspaces": console_sessions.list_workspaces(principal, is_admin)}


@router.post("/console/workspaces")
def console_workspace_create(body: ConsoleWorkspaceBody,
                             info: dict[str, Any] = Depends(require_user_info)) -> dict[str, Any]:
    principal, is_admin = _principal_info(info)
    try:
        row = console_sessions.create_workspace(body.name, principal, body.path,
                                                is_admin=is_admin)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "workspace": row}


@router.delete("/console/workspaces/{name}")
def console_workspace_delete(name: str,
                             info: dict[str, Any] = Depends(require_user_info)) -> dict[str, Any]:
    principal, is_admin = _principal_info(info)
    try:
        moved = console_sessions.delete_workspace(name, principal, is_admin)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # 只解散分组，不删里面的会话 —— 删一个工作区不该顺手毁掉一堆对话。
    return {"ok": True, "deleted": name, "sessions_moved": moved}


class VisionDescribeBody(BaseModel):
    images: list[str] = Field(..., min_length=1, max_length=4)
    prompt: str = Field(default="", max_length=2000)


@router.post("/vision/describe")
async def vision_describe(body: VisionDescribeBody,
                          _user: str = Depends(require_user)) -> dict[str, Any]:
    """把图片读成文字 —— 任务台的"贴图"靠它。

    为什么在 ops 这边读而不是把图直接丢给 agent：ops 这边的视觉链（系统配置里的
    vision_*）是 Listing、技能商店一直在用的那条，配没配、好不好使有据可依；
    agent 侧另有一条自己的三档降级链（主脑直读 / 视觉旁路 / 本地 CV 度量），
    两条链的配置互相独立，用户机器上很可能只配了一边。合并成一条是另一个决定，
    见 ADR-0020，别顺手在这里改。

    代价要说清楚：Agent 拿到的是**图片的描述**而不是图片本身，精细看图会有损耗。
    读出来的文字走 chat 的 `attachments` 字段（不是 `system`）—— 理由同见 ADR-0020。
    """
    from app.services import ai_synthesis_service

    imgs = [u for u in body.images if isinstance(u, str) and u.startswith("data:image/")]
    if not imgs:
        raise HTTPException(status_code=400, detail="images 必须是 data:image/... 开头的 data URI")
    # 单张 ~8MB 的 base64 就够放一张高清截图了；再大多半是误传视频/原图
    for u in imgs:
        if len(u) > 8 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="单张图片过大（>8MB），请压缩后再试")

    prompt = (body.prompt or "").strip() or (
        "请客观描述这张图里的内容：文字原样抄出来，表格按行列讲清楚，"
        "图表说明坐标轴和关键数值。不要臆测图外的信息。"
    )
    chunks: list[str] = []
    provider = ""
    error = ""
    async for prov, chunk in ai_synthesis_service.stream_vision(prompt, imgs):
        if prov == "error":
            error = chunk
            break
        provider = prov
        chunks.append(chunk)
    text = "".join(chunks).strip()
    if error or not text:
        raise HTTPException(status_code=503, detail=error or "视觉模型没有返回内容，请稍后重试")
    return {"ok": True, "provider": provider, "text": text}


class AgentMCPBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    transport: str = Field(..., pattern="^(http|sse|stdio)$")
    url: str = Field(default="", max_length=2000)
    command: str = Field(default="", max_length=1000)
    args: list[str] = Field(default_factory=list, max_length=40)
    headers: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    trusted: bool = False


@router.get("/mcp/servers")
def agent_mcp_servers() -> dict[str, Any]:
    """agent 的 MCP 注册表 + Claude Code 的（只读）。

    两套注册表长期被混为一谈：ops 原有的 /api/mcp/servers 管的是 Claude 的
    ~/.claude.json，管不到 agent —— 而真正决定「工作台里的 Agent 能连哪些数据源」
    的是 ~/.ivyea/mcp.json。这里一次把两边都摆出来，分区展示。
    """
    return {
        "ok": True,
        "servers": agent_mcp.list_servers(),
        "claude_servers": agent_mcp.claude_servers(),
        "managed": sorted(agent_mcp.MANAGED_SERVERS),
    }


# ── 记忆管理 ────────────────────────────────────────────────────────────────
#
# 记忆里装的是"这个人是谁、他定过什么规矩、agent 从他身上推断出了什么"。
# 在此之前这些只能从命令行看 —— 看不见就不敢信，推断错了也没地方改。
#
# 权限分两档：**读要登录、写要管理员**。记忆含个人信息与经营数据，
# 而写入会直接改变 agent 以后的行为（核心记忆每轮都进上下文）。


class MemoryWriteBody(BaseModel):
    operation: str = "add"
    name: str = ""
    content: str = ""
    category: str = ""
    description: str = ""
    keywords: str = ""
    links: str = ""
    scope: str = ""
    valid_from: str = ""
    valid_until: str = ""


class MemoryNameBody(BaseModel):
    name: str = ""


class MemoryIrrelevantBody(BaseModel):
    """key 是界面上显示的那个 "category/name" 串。"""

    key: str = ""


class MemoryCoreBody(BaseModel):
    block: str = ""
    operation: str = "append"
    content: str = ""
    old: str = ""


class MemoryPruneBody(BaseModel):
    days: int = 0
    dry_run: bool = True


@router.get("/memory/list")
def memory_list(scope: str = "", include_expired: bool = False,
                _user: str = Depends(require_user)) -> dict[str, Any]:
    return _call(svc.request_json, "GET",
                 f"/v1/memory/list?scope={quote(scope)}"
                 f"&include_expired={'1' if include_expired else '0'}")


@router.get("/memory/get")
def memory_get(name: str, category: str = "",
               _user: str = Depends(require_user)) -> dict[str, Any]:
    return _call(svc.request_json, "GET",
                 f"/v1/memory/get?name={quote(name)}&category={quote(category)}")


@router.get("/memory/history")
def memory_history(name: str, category: str = "",
                   _user: str = Depends(require_user)) -> dict[str, Any]:
    return _call(svc.request_json, "GET",
                 f"/v1/memory/history?name={quote(name)}&category={quote(category)}")


@router.get("/memory/pending")
def memory_pending(_user: str = Depends(require_user)) -> dict[str, Any]:
    return _call(svc.request_json, "GET", "/v1/memory/pending")


@router.get("/memory/stats")
def memory_stats(_user: str = Depends(require_user)) -> dict[str, Any]:
    return _call(svc.request_json, "GET", "/v1/memory/stats")


@router.get("/memory/core")
def memory_core(block: str = "", _user: str = Depends(require_user)) -> dict[str, Any]:
    return _call(svc.request_json, "GET", f"/v1/memory/core?block={quote(block)}")


@router.get("/memory/episodes")
def memory_episodes(query: str = "", limit: int = 30,
                    _user: str = Depends(require_user)) -> dict[str, Any]:
    return _call(svc.request_json, "GET",
                 f"/v1/memory/episodes?query={quote(query)}&limit={int(limit)}")


@router.post("/memory/write")
def memory_write(body: MemoryWriteBody,
                 _admin: str = Depends(require_admin)) -> dict[str, Any]:
    """人工增改删。写进去的会被标成"用户亲口说的"（满置信），
    而且从此 agent 的反思不许再改它。"""
    return _call(svc.request_json, "POST", "/v1/memory/write", _payload(body))


@router.post("/memory/confirm")
def memory_confirm(body: MemoryNameBody,
                   _admin: str = Depends(require_admin)) -> dict[str, Any]:
    """确认一条推断。这是**唯一**能让置信度越过"不确定线"的路径 ——
    自动攒够观察次数也只是转正，仍然标着推断。"""
    return _call(svc.request_json, "POST", "/v1/memory/confirm", _payload(body))


@router.post("/memory/reject")
def memory_reject(body: MemoryNameBody,
                  _admin: str = Depends(require_admin)) -> dict[str, Any]:
    return _call(svc.request_json, "POST", "/v1/memory/reject", _payload(body))


@router.post("/memory/irrelevant")
def memory_irrelevant(body: MemoryIrrelevantBody,
                      _admin: str = Depends(require_admin)) -> dict[str, Any]:
    """「这条跟我问的没关系」。

    界面上每轮都画着"回忆了哪几条"，顺手点一下的成本几乎为零 —— 这是误召唯一
    可持续的发现渠道。agent 那边会扣掉那次不该记的命中，并按次数在遗忘打分里降权。
    """
    return _call(svc.request_json, "POST", "/v1/memory/irrelevant", _payload(body))


@router.post("/memory/core")
def memory_core_write(body: MemoryCoreBody,
                      _admin: str = Depends(require_admin)) -> dict[str, Any]:
    """改核心记忆。它每轮都进上下文，改错的影响面比普通记忆大得多。"""
    return _call(svc.request_json, "POST", "/v1/memory/core", _payload(body))


@router.post("/memory/reflect")
def memory_reflect(_admin: str = Depends(require_admin)) -> dict[str, Any]:
    return _call(svc.request_json, "POST", "/v1/memory/reflect", {"force": True})


@router.post("/memory/prune")
def memory_prune(body: MemoryPruneBody,
                 _admin: str = Depends(require_admin)) -> dict[str, Any]:
    """清理过期对话记录。**不可逆**，所以默认 dry_run，且 agent 侧首次真删前会备份 DB。"""
    return _call(svc.request_json, "POST", "/v1/memory/prune", _payload(body))


@router.post("/mcp/servers")
def agent_mcp_upsert(body: AgentMCPBody, _admin: str = Depends(require_admin)) -> dict[str, Any]:
    """新增/更新一台 MCP 服务器。

    仅管理员：stdio 型的 command 会被 agent 拿去起进程，等于赋予执行能力。
    """
    try:
        row = agent_mcp.upsert_server(body.name, _payload(body))
    except agent_mcp.AgentMCPError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **row}


@router.delete("/mcp/servers/{name}")
def agent_mcp_delete(name: str, _admin: str = Depends(require_admin)) -> dict[str, Any]:
    try:
        removed = agent_mcp.remove_server(name)
    except agent_mcp.AgentMCPError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail=f"未配置该 MCP 服务器：{name}")
    return {"ok": True, "removed": name}


@router.get("/model/providers")
def model_providers() -> dict[str, Any]:
    return _call(svc.model_providers)


@router.get("/model/providers/{provider_id}/models")
def provider_models(provider_id: str, refresh: bool = False) -> dict[str, Any]:
    return _call(svc.provider_models, provider_id, refresh)


# ── 订阅登录（Claude / Codex / Gemini / Qwen / Copilot）─────────────────────
#
# **一律 require_admin。** 这些凭据存在服务器上、由 agent 全局共用：谁登录，这台
# agent 上所有用户的对话和所有定时任务都在烧谁的订阅额度。这不是普通用户该按的开关。

_AUTH_PROVIDERS = {"qwen-oauth", "openai-codex", "kimi-code",
                   "anthropic-oauth", "google-gemini-cli", "copilot"}


class AuthActionBody(BaseModel):
    session: str = Field(default="", max_length=120)
    # 粘回来的东西：Claude 的 `code#state`、Gemini 的整条回调 URL、Copilot 的 token。
    value: str = Field(default="", max_length=8000)


def _checked_auth_provider(provider_id: str) -> str:
    """provider id 会被拼进转发给 agent 的路径，只放行清单里那五个。"""
    pid = (provider_id or "").strip()
    if pid not in _AUTH_PROVIDERS:
        raise HTTPException(status_code=404, detail=f"这个 provider 不走登录流程：{provider_id}")
    return pid


@router.get("/auth")
def auth_status(_admin: str = Depends(require_admin)) -> dict[str, Any]:
    return _call(svc.auth_status)


@router.post("/auth/{provider_id}/start")
def auth_start(provider_id: str, _admin: str = Depends(require_admin)) -> dict[str, Any]:
    return _call(svc.auth_start, _checked_auth_provider(provider_id))


@router.post("/auth/{provider_id}/poll")
def auth_poll(provider_id: str, body: AuthActionBody,
              _admin: str = Depends(require_admin)) -> dict[str, Any]:
    return _call(svc.auth_poll, _checked_auth_provider(provider_id), body.session)


@router.post("/auth/{provider_id}/complete")
def auth_complete(provider_id: str, body: AuthActionBody,
                  _admin: str = Depends(require_admin)) -> dict[str, Any]:
    return _call(svc.auth_complete, _checked_auth_provider(provider_id), body.session, body.value)


@router.post("/auth/{provider_id}/logout")
def auth_logout(provider_id: str, _admin: str = Depends(require_admin)) -> dict[str, Any]:
    return _call(svc.auth_logout, _checked_auth_provider(provider_id))


@router.post("/model/catalog")
def model_catalog(body: ModelCatalogBody) -> dict[str, Any]:
    return _call(svc.model_catalog, _payload(body))


@router.post("/model/providers/{provider_id}/probe")
def provider_probe(provider_id: str, body: ProviderProbeBody) -> dict[str, Any]:
    return _call(svc.provider_probe, provider_id, {"model": body.model, "timeout": body.timeout})


@router.get("/ops-tools")
def ops_tools(module: str = "", query: str = "") -> dict[str, Any]:
    return ivyea_ops_tools.list_tools(module=module, query=query)


@router.post("/ops-tools/call")
async def ops_tool_call(body: OpsToolCallBody) -> dict[str, Any]:
    return await ivyea_ops_tools.call_tool(body.name, body.arguments)


@router.get("/retrieval/status")
def retrieval_status() -> dict[str, Any]:
    return _call(svc.retrieval_status)


@router.get("/retrieval/embeddings")
def retrieval_embeddings() -> dict[str, Any]:
    return _call(svc.retrieval_embeddings)


@router.post("/retrieval/sync")
def retrieval_sync() -> dict[str, Any]:
    return _call(svc.retrieval_sync)


@router.get("/knowledge/watchlist")
def knowledge_watchlist() -> dict[str, Any]:
    return _call(svc.knowledge_watchlist)


@router.get("/knowledge/governance")
def knowledge_governance() -> dict[str, Any]:
    return _call(svc.knowledge_governance)


@router.get("/knowledge/coverage")
def knowledge_coverage() -> dict[str, Any]:
    return _call(svc.knowledge_coverage)


@router.get("/knowledge/freshness")
def knowledge_freshness() -> dict[str, Any]:
    return _call(svc.knowledge_freshness)


@router.get("/knowledge/quality")
def knowledge_quality() -> dict[str, Any]:
    return _call(svc.knowledge_quality)


@router.get("/knowledge/changes")
def knowledge_changes(
    limit: int = Query(50, ge=1, le=500),
    status: str = Query(default="", pattern="^(|pending|approved|rejected|superseded)$"),
) -> dict[str, Any]:
    return _call(svc.knowledge_changes, limit, status)


@router.get("/knowledge/reviews")
def knowledge_reviews(
    limit: int = Query(100, ge=1, le=1000), event_id: str = Query(default="", max_length=120),
) -> dict[str, Any]:
    return _call(svc.knowledge_reviews, limit, event_id)


@router.get("/knowledge/publications")
def knowledge_publications(
    limit: int = Query(100, ge=1, le=1000), event_id: str = Query(default="", max_length=120),
) -> dict[str, Any]:
    return _call(svc.knowledge_publications, limit, event_id)


@router.get("/knowledge/versions")
def knowledge_versions(
    card_id: str = Query(default="", max_length=240), limit: int = Query(100, ge=1, le=1000),
) -> dict[str, Any]:
    return _call(svc.knowledge_versions, card_id, limit)


@router.get("/knowledge/evidence")
def knowledge_evidence(limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    return _call(svc.knowledge_evidence, limit)


@router.get("/knowledge/evidence/schema")
def knowledge_evidence_schema() -> dict[str, Any]:
    return _call(svc.knowledge_evidence_schema)


@router.get("/knowledge/changes/{event_id}/packet")
def knowledge_change_packet(
    event_id: str, card_id: str = Query(default="", max_length=240),
) -> dict[str, Any]:
    return _call(svc.knowledge_change_packet, event_id, card_id)


@router.post("/knowledge/changes/review")
def knowledge_review_change(
    body: KnowledgeReviewBody, _admin: str = Depends(require_admin),
) -> dict[str, Any]:
    payload = _payload(body)
    payload["reviewer"] = _admin
    payload["reviewer_source"] = "ops_authenticated_admin"
    return _call(svc.knowledge_review_change, payload)


@router.post("/knowledge/versions/rollback")
def knowledge_version_rollback(
    body: KnowledgeVersionRollbackBody, _admin: str = Depends(require_admin),
) -> dict[str, Any]:
    payload = _payload(body)
    payload["actor"] = _admin
    payload["actor_source"] = "ops_authenticated_admin"
    return _call(svc.knowledge_version_rollback, payload)


@router.post("/knowledge/evidence/draft")
def knowledge_evidence_draft(
    body: dict[str, Any], _admin: str = Depends(require_admin),
) -> dict[str, Any]:
    payload = dict(body)
    payload["actor"] = _admin
    payload["actor_source"] = "ops_authenticated_admin"
    return _call(svc.knowledge_evidence_draft, payload)


@router.post("/knowledge/evidence/apply")
def knowledge_evidence_apply(
    body: dict[str, Any], _admin: str = Depends(require_admin),
) -> dict[str, Any]:
    payload = dict(body)
    payload["actor"] = _admin
    payload["actor_source"] = "ops_authenticated_admin"
    return _call(svc.knowledge_evidence_apply, payload)


@router.post("/knowledge/changes/draft")
def knowledge_change_draft(
    body: KnowledgeChangeDraftBody, _admin: str = Depends(require_admin),
) -> dict[str, Any]:
    return _call(svc.knowledge_change_draft, _payload(body))


@router.post("/knowledge/changes/apply")
def knowledge_change_apply(
    body: KnowledgeChangeApplyBody, _admin: str = Depends(require_admin),
) -> dict[str, Any]:
    return _call(svc.knowledge_change_apply, _payload(body))


@router.post("/knowledge/sync")
def knowledge_sync(
    body: KnowledgeSyncBody, _admin: str = Depends(require_admin),
) -> dict[str, Any]:
    return _call(svc.knowledge_sync, _payload(body))


@router.get("/knowledge/cards")
def knowledge_cards(limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
    return _call(svc.knowledge_cards, limit)


@router.get("/knowledge/cards/{card_id:path}")
def knowledge_card(card_id: str) -> dict[str, Any]:
    """一张知识卡的原文。供任务台里「引用知识」那几条内部协议地址跳转过来。"""
    return _call(svc.knowledge_card, card_id)


@router.get("/knowledge/search")
def knowledge_search(q: str = Query("", max_length=1000), limit: int = Query(8, ge=1, le=50)) -> dict[str, Any]:
    return _call(svc.knowledge_search, q, limit)


@router.get("/knowledge/files")
def knowledge_files(limit: int = Query(500, ge=1, le=1000)) -> dict[str, Any]:
    return _call(svc.knowledge_files, limit)


@router.get("/knowledge/uploads")
def knowledge_uploads(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    return _call(svc.knowledge_uploads, limit)


@router.get("/knowledge/file")
def knowledge_file(path: str = Query(..., min_length=1, max_length=1000)) -> dict[str, Any]:
    return _call(svc.knowledge_file, path)


@router.delete("/knowledge/file")
def knowledge_delete_file(path: str = Query(..., min_length=1, max_length=1000)) -> dict[str, Any]:
    return _call(svc.knowledge_delete_file, path)


@router.post("/knowledge/update/draft")
def knowledge_update_draft(body: KnowledgeUpdateBody) -> dict[str, Any]:
    return _call(svc.knowledge_update_draft, _payload(body))


@router.post("/knowledge/update/apply")
def knowledge_update_apply(body: KnowledgeUpdateBody) -> dict[str, Any]:
    return _call(svc.knowledge_update_apply, _payload(body))


@router.post("/knowledge/upload")
async def knowledge_upload(
    file: UploadFile = File(...),
    title: str = Form(""),
    id: str = Form(""),
    source_url: str = Form(""),
    source_type: str = Form("user"),
    confidence: str = Form(""),
    license: str = Form("user_supplied"),
    tags: str = Form(""),
    confirm: bool = Form(False),
    rebuild: bool = Form(True),
) -> dict[str, Any]:
    data = await file.read(25 * 1024 * 1024 + 1)
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件过大，最大 25MB")
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    return _call(
        svc.knowledge_upload,
        {
            "filename": file.filename or "upload",
            "content_base64": base64.b64encode(data).decode("ascii"),
            "title": title,
            "id": id,
            "source_url": source_url,
            "source_type": source_type or "user",
            "confidence": confidence,
            "license": license or "user_supplied",
            "tags": tag_list,
            "confirm": confirm,
            "rebuild": rebuild,
        },
    )


#: 会话附件的大小上限。比知识库那条（25MB）小：知识库是长期资产，值得为它多等；
#: 而这个是"随这一轮带下去"的，抽出来的正文还要占本轮上下文，大得没有意义。
_SESSION_FILE_MAX_BYTES = 10 * 1024 * 1024


@router.post("/session-files")
async def session_file_extract(
    file: UploadFile = File(...),
    _user: str = Depends(require_user),
) -> dict[str, Any]:
    """把一份文档抽成正文，**只给这一轮对话用，不进知识库**。

    此前任务台上传任何文件都直接走 knowledge/upload 进了知识库并重建索引 ——
    用户的原话是"有些文件只是会话的时候用，并不需要纳入知识库"。这条路就是那个
    "只用一次"的出口：不落盘、不建索引、不留档，抽完正文就把字交给前端，随下一条
    消息作为 attachment 带给 agent。

    想长期留着的，仍然走 knowledge/upload（界面上是一个显式的勾选）。
    """
    data = await file.read(_SESSION_FILE_MAX_BYTES + 1)
    if len(data) > _SESSION_FILE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="文件过大，会话附件最大 10MB")
    if not data:
        raise HTTPException(status_code=400, detail="文件是空的")
    try:
        out = _call(svc.files_extract, {
            "filename": file.filename or "upload",
            "content_base64": base64.b64encode(data).decode("ascii"),
        })
    except HTTPException as exc:
        # 老 agent 没有这个端点 → 它回 404 `not_found`，经 _call 变成一条 502 加一串
        # 原始报文。**必须翻成人话，而且绝不能静默退回"那就入库吧"** —— 那恰恰是
        # 被抱怨的那个行为，用户会以为自己只是传了个附件，结果知识库被悄悄写脏。
        if "not_found" in str(exc.detail or "") or "/v1/files/extract" in str(exc.detail or ""):
            raise HTTPException(
                status_code=501,
                detail="当前 IvyeaAgent 版本还不支持会话附件（需要升级）。"
                       "升级前，文件只能走「收进知识库」那条路。") from exc
        raise
    text = str(out.get("text") or "").strip()
    if not text:
        # 抽不出字就明说，别塞一条空壳附件下去 —— 那会让模型以为自己拿到了材料。
        # 报错是给用户看的，所以把 agent 的 warning 码翻成人话，别把
        # `unknown_binary_or_empty_text` 这种东西直接甩到界面上。
        # **按具体度排序，不是按 warnings 的顺序**：agent 会同时给出
        # `unknown_binary_or_empty_text` 和 `looks_binary`，而前者排在前面 ——
        # 照 warnings 的顺序取第一条，用户看到的永远是那句最含糊的。
        why = (
            ("looks_binary", "它看起来是二进制文件（压缩包、可执行文件之类），里面没有可读的文字"),
            ("pdf_text_extraction_unavailable", "这个 PDF 里没有文字层，多半是扫描件，需要先 OCR"),
            ("docx_text_extraction_failed", "这个 docx 没能解开"),
            ("xlsx_text_extraction_unavailable", "这个表格没能解开"),
            ("unknown_binary_or_empty_text", "没认出这个格式，也没读到文字"),
        )
        got = set(out.get("warnings") or [])
        hints = [msg for code, msg in why if code in got]
        raise HTTPException(
            status_code=422,
            detail=f"没能从「{file.filename}」里读出文字：{hints[0] if hints else '没认出这个格式'}。"
                   "可以先转成 PDF/Word/Markdown/txt 里带文字的那种再传。")
    # 原件也留一份。抽出来的正文是给模型看的，但用户回头翻记录时要能把当初传的
    # 那份 PDF **下回来** —— 只留文字的话，"我上传过一份报价单"就剩一个点不开的
    # 文件名。存不下不算失败：正文已经拿到了，这一轮照样能用。
    from app.routers import assistant as _assistant
    try:
        url = _assistant.store_session_file(data, file.filename or "upload")
    except Exception:  # noqa: BLE001
        logger.warning("会话附件原件没存下（不影响这一轮）", exc_info=True)
        url = ""
    return {"ok": True, "name": file.filename or "upload", "text": text, "url": url,
            "chars": out.get("chars") or len(text), "truncated": bool(out.get("truncated")),
            "warnings": out.get("warnings") or []}


@router.post("/knowledge/uploads/apply")
def knowledge_upload_apply(body: KnowledgeUploadApplyBody) -> dict[str, Any]:
    return _call(svc.knowledge_upload_apply, _payload(body))


@router.post("/knowledge/import-directory")
def knowledge_import_directory(body: KnowledgeImportDirectoryBody) -> dict[str, Any]:
    return _call(svc.knowledge_import_directory, _payload(body))


@router.post("/code/bundle")
def code_bundle(body: CodeBundleBody) -> dict[str, Any]:
    return _call(
        svc.code_bundle,
        {
            "root": body.root,
            "goal": body.goal,
            "test_output": body.test_output,
            "limit": body.limit,
        },
    )


@router.post("/code/apply-loop")
def code_apply_loop(body: CodeApplyLoopBody) -> dict[str, Any]:
    return _call(
        svc.code_apply_loop,
        {
            "root": body.root,
            "spec": body.spec,
            "test_command": body.test_command,
            "execute": body.execute,
            "timeout": body.timeout,
            "persist": body.persist,
        },
    )


@router.get("/service/status")
def service_status(host: str = "", port: int | None = None) -> dict[str, Any]:
    return _call(svc.service_status, host, port)


@router.get("/service/logs")
def service_logs(lines: int = 80) -> dict[str, Any]:
    return _call(svc.service_logs, lines)


@router.post("/service/start")
def service_start(body: ServiceStartBody) -> dict[str, Any]:
    return _call(
        svc.service_start,
        {
            "host": body.host,
            "port": body.port,
            "allow_remote": body.allow_remote,
            "api_token": body.api_token,
            "wait": body.wait,
            "timeout": body.timeout,
        },
    )


@router.post("/service/stop")
def service_stop(body: ServiceStopBody) -> dict[str, Any]:
    return _call(svc.service_stop, {"timeout": body.timeout, "force": body.force})


@router.post("/service/autostart")
def service_autostart(body: ServiceAutostartBody) -> dict[str, Any]:
    return _call(svc.service_autostart, {"host": body.host, "port": body.port})


def _bridge_principal(authorization: str) -> dict[str, Any]:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="missing bridge bearer token")
    return ivyea_ops_tools.activate_bridge_principal(token.strip())


@bridge_router.post("/tools")
def bridge_tools(body: OpsToolsListBody, authorization: str = Header(default="")) -> dict[str, Any]:
    principal = _bridge_principal(authorization)
    return ivyea_ops_tools.list_tools(module=body.module, query=body.query, principal=principal)


@bridge_router.post("/call")
async def bridge_call(body: OpsToolCallBody, authorization: str = Header(default="")) -> dict[str, Any]:
    principal = _bridge_principal(authorization)
    return await ivyea_ops_tools.call_tool(body.name, body.arguments, principal=principal)
