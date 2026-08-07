"""GBrain web API for the IvyeaOps knowledge base UI."""
from __future__ import annotations

import asyncio
import codecs
import json
import time
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.proc import no_window_kwargs
from app.core.security import require_user, require_user_info
from app.services import brain_chat_service as bc
from app.services import gbrain_service as gb
from app.services import console_sessions
from app.services import ivyea_agent_service as ia


router = APIRouter(dependencies=[Depends(require_user)])


class SearchBody(BaseModel):
    query: str = Field(..., min_length=1, max_length=gb.MAX_QUERY_CHARS)
    mode: str = Field("search", pattern="^(search|query)$")


class FileWriteBody(BaseModel):
    path: str = Field(..., min_length=1, max_length=240)
    content: str = Field(..., max_length=gb.MAX_WRITE_BYTES)


class PageBody(BaseModel):
    slug: str = Field(..., min_length=1, max_length=200)


class ChatSessionCreateBody(BaseModel):
    title: str | None = Field(default=None, max_length=80)
    mode: str = Field(default="knowledge", pattern="^(knowledge|general|amazon_operator)$")


class ChatSessionUpdateBody(BaseModel):
    title: str | None = Field(default=None, max_length=80)
    archived: bool | None = None


class ChatMessageBody(BaseModel):
    content: str = Field(..., min_length=1, max_length=bc.MAX_CHAT_CHARS)


class ChatStreamBody(BaseModel):
    content: str = Field("", max_length=bc.MAX_CHAT_CHARS)
    regenerate: bool = False
    category: str | None = Field(None, max_length=40)


class IngestTextBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=bc.MAX_INGEST_TEXT_CHARS)
    import_after_save: bool = True


class IngestUrlBody(BaseModel):
    url: str = Field(..., min_length=8, max_length=2000)
    import_after_save: bool = True


def _handle(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (gb.GBrainError, bc.BrainChatError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _ivyea_front_door() -> bool:
    """The governed IvyeaAgent knowledge base is the front door for search and
    pages; the legacy GBrain markdown store is only a fallback when the local
    IvyeaAgent service is down."""
    return bc.ivyea_chat_available()


def _ia_search(query: str, mode: str) -> dict[str, Any]:
    res = ia.knowledge_search(query, limit=12)
    items: list[dict[str, Any]] = []
    for r in (res.get("results") or []):
        items.append({
            "slug": r.get("id"),
            "score": r.get("score", 0),
            "snippet": r.get("snippet") or "",
            "title": r.get("title") or "",
            "source_url": r.get("source_url") or "",
            "marketplaces": r.get("marketplaces") or [],
        })
    return {"mode": mode, "query": query, "raw": "", "items": items, "source": "ivyea-agent"}


def _ia_page(slug: str) -> dict[str, Any]:
    res = ia.knowledge_card(slug)
    card = res.get("card") or {}
    body = str(card.get("body") or "")
    src = card.get("source_url") or ""
    header = f"> 来源：{src}\n\n" if src else ""
    return {"slug": slug, "content": (header + body) if body else body,
            "title": card.get("title") or "", "source_url": src, "source": "ivyea-agent"}


def _ia_list_files() -> dict[str, Any]:
    res = ia.knowledge_cards(limit=1000)
    files: list[dict[str, Any]] = []
    for c in (res.get("cards") or []):
        cid = c.get("id") or ""
        files.append({
            "path": cid,                       # card id doubles as the read key
            "name": c.get("title") or cid,
            "summary": (c.get("snippet") or "")[:100],
            "size": 0,
            "category": c.get("category") or "",
            "scope": c.get("scope") or "",
            "marketplaces": c.get("marketplaces") or [],
        })
    files.sort(key=lambda f: (str(f.get("category")), str(f.get("name"))))
    return {"files": files, "source": "ivyea-agent"}


def _ia_read_file(path: str) -> dict[str, Any]:
    # The pages tab is an editor; return the raw card body (no injected "> 来源"
    # header) so an edit round-trips cleanly through write_file.
    res = ia.knowledge_card(path)
    card = res.get("card") or {}
    return {"path": path, "content": str(card.get("body") or ""),
            "title": card.get("title") or "", "source_url": card.get("source_url") or "", "source": "ivyea-agent"}


def _strip_source_header(text: str) -> str:
    """Defensively drop a leading '> 来源：…' blockquote (added by _ia_page) if it
    slipped into an edited body."""
    lines = text.split("\n")
    i = 0
    while i < len(lines) and (lines[i].startswith("> 来源：") or not lines[i].strip()):
        if lines[i].startswith("> 来源："):
            i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1
            return "\n".join(lines[i:])
        i += 1
    return text


def _ia_upload_result(resp: dict[str, Any], preview: str = "") -> dict[str, Any]:
    # With confirm=True the applied card lands under resp["apply"]["card"];
    # otherwise only a draft/upload record exists.
    apply = resp.get("apply") or {}
    card = apply.get("card") or resp.get("card") or {}
    up = resp.get("upload") or {}
    card_id = card.get("id") or ""
    saved = card.get("path") or card_id or up.get("extracted_path") or "已保存到 IvyeaAgent 知识库"
    # The applied card only carries a body_hash, not the body, so fall back to the
    # source text the caller ingested for the preview/summary.
    body = str(card.get("body") or preview or "")
    # Align with BrainUploadResponse so the RESULT panel renders (the frontend
    # reads warnings/markdown_preview/analysis unconditionally).
    return {
        "saved_path": saved,
        "import_status": "ok",
        "card_id": card_id,
        "warnings": list(apply.get("warnings") or []),
        "markdown_preview": body[:4000],
        "analysis": {
            "title": card.get("title") or "",
            "directory": card.get("category") or "inbox",
            "tags": card.get("tags") or [],
            "summary": (card.get("snippet") or body[:200]),
            "content_type": card.get("content_type") or "note",
            "confidence": 1.0,
            "source": "ivyea-agent",
        },
        "source": "ivyea-agent",
    }


def _ia_upload_bytes(
    filename: str,
    data: bytes,
    title: str | None,
    category: str | None = None,
    tags: list[str] | None = None,
    preview: str | None = None,
) -> dict[str, Any]:
    import base64
    tag_list = [str(t) for t in (tags or ([category] if category else [])) if str(t).strip()]
    payload = {
        "filename": filename or "upload.txt",
        "content_base64": base64.b64encode(data).decode("ascii"),
        "title": (title or "").strip(),
        "source_type": "user",
        "tags": tag_list,
        "confirm": True,   # save + apply into the governed knowledge base in one step
        "rebuild": True,
    }
    if preview is None:
        try:
            preview = data.decode("utf-8")  # text uploads; binary (pdf/xlsx) → no preview
        except UnicodeDecodeError:
            preview = ""
    return _ia_upload_result(ia.knowledge_upload(payload), preview=preview)


_ANALYSIS_FIELDS_SPEC = (
    "- title: 中文短标题，最多 40 字\n"
    "- directory: inbox / amazon/ads / amazon/products / amazon/messages / "
    "amazon/suppliers / amazon/market / compliance 之一\n"
    "- tags: 3-6 个短标签组成的数组\n"
    "- summary: 80-160 字中文摘要\n"
    "- content_type: note / amazon_ads / amazon_product / buyer_message / "
    "supplier_note / market_note / compliance\n"
    "- confidence: 0 到 1 的数字"
)


def _analysis_from_obj(obj: dict[str, Any], content: str) -> dict[str, Any]:
    """Merge a (possibly empty) model-produced analysis JSON with a rules-based
    fallback so every field is always present and sane."""
    fb = bc._fallback_ingest_analysis((content or "").strip())
    tags = obj.get("tags")
    if not isinstance(tags, list) or not tags:
        tags = fb["tags"]
    try:
        confidence = float(obj.get("confidence", fb["confidence"]))
    except (TypeError, ValueError):
        confidence = fb["confidence"]
    return {
        "title": (str(obj.get("title") or "").strip()[:80] or fb["title"]),
        "directory": (str(obj.get("directory") or "").strip() or fb["directory"]),
        "tags": [str(t).strip()[:40] for t in tags if str(t).strip()][:6],
        "summary": (str(obj.get("summary") or "").strip()[:500] or fb["summary"]),
        "content_type": (str(obj.get("content_type") or "").strip() or fb["content_type"]),
        "confidence": max(0.0, min(1.0, confidence)),
        "source": "ivyea-agent" if obj else "rules_fallback",
    }


async def _ia_ingest_analysis(text: str) -> dict[str, Any]:
    """Auto-analyze pasted/cleaned text into a title/tags/summary via the unified
    text chain (IvyeaAgent → global fallback → …), with a rules-based fallback so
    ingest never depends on the model being reachable."""
    from app.services import ai_synthesis_service

    clean = (text or "").strip()
    prompt = (
        "你是私有知识库的入库分类器。只返回严格 JSON，不要解释、不要用代码块。\n"
        "分析下面的文本，生成字段：\n"
        f"{_ANALYSIS_FIELDS_SPEC}\n\n"
        f"【待入库文本】\n{clean[:8000]}"
    )
    obj: dict[str, Any] = {}
    try:
        raw = (await ai_synthesis_service.generate_text(prompt)).strip()
        obj = bc._extract_json_object(raw) or {}
    except Exception:  # noqa: BLE001 — degrade to rules fallback below
        obj = {}
    return _analysis_from_obj(obj, clean)


async def _ia_ingest_analyzed(text: str, analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    """Analyze (unless an analysis is provided) then store the text as a governed
    user card, returning a result the RESULT panel can render."""
    if analysis is None:
        analysis = await _ia_ingest_analysis(text)
    stem = (analysis.get("title") or "note").strip()[:40] or "note"
    result = _ia_upload_bytes(
        f"{stem}.md", text.encode("utf-8"),
        title=analysis.get("title"), tags=analysis.get("tags"), preview=text,
    )
    # The AI analysis is richer than what the applied card echoes back — surface it.
    result["analysis"] = analysis
    if not result.get("markdown_preview"):
        result["markdown_preview"] = text[:4000]
    return result


@router.get("/overview")
def overview() -> dict[str, Any]:
    # Self-heal first: auto-init the DB + auto-wire Ollama embedding so the board
    # works without manual setup. If the DB still can't come up (e.g. incompatible
    # gbrain version), return the readiness info — with an actionable hint — instead
    # of letting gb.overview() raise the raw "No database URL" error.
    try:
        ready = gb.ensure_ready()
    except Exception as e:  # noqa: BLE001 — never let self-heal break the board
        ready = {"db_ready": False, "version_compatible": True, "actions": [], "hint": str(e)}
    if not ready.get("db_ready"):
        return {"ready": ready, "embed_configured": False, "stats": {},
                "brain_root": "", "gbrain_bin": "", "doctor_status": "not_ready",
                "search_mode": "unknown", "git_dirty": False, "git_status": ""}
    ov = _handle(gb.overview)
    ov["ready"] = ready
    return ov


@router.get("/stats")
def stats() -> dict[str, Any]:
    return _handle(gb.stats)


@router.get("/doctor")
def doctor() -> dict[str, Any]:
    return _handle(gb.doctor)


@router.post("/search")
def search(body: SearchBody) -> dict[str, Any]:
    if _ivyea_front_door():
        try:
            return _ia_search(body.query, body.mode)
        except Exception:  # noqa: BLE001 — degrade to legacy GBrain search
            pass
    return _handle(gb.search, body.query, body.mode)


@router.get("/page/{slug:path}")
def get_page(slug: str) -> dict[str, Any]:
    if _ivyea_front_door():
        try:
            return _ia_page(slug)
        except Exception:  # noqa: BLE001 — degrade to legacy GBrain page
            pass
    return _handle(gb.get_page, slug)


@router.post("/page")
def get_page_post(body: PageBody) -> dict[str, Any]:
    if _ivyea_front_door():
        try:
            return _ia_page(body.slug)
        except Exception:  # noqa: BLE001 — degrade to legacy GBrain page
            pass
    return _handle(gb.get_page, body.slug)


@router.get("/files")
def list_files() -> dict[str, Any]:
    if _ivyea_front_door():
        try:
            return _ia_list_files()
        except Exception:  # noqa: BLE001 — degrade to legacy GBrain file list
            pass
    return _handle(gb.list_files)


@router.get("/file")
def read_file(path: str = Query(..., min_length=1, max_length=240)) -> dict[str, Any]:
    # A card id (e.g. "policies.account_health_ca") has no path separator; a
    # legacy GBrain file always does. Route card ids to the governed KB.
    if _ivyea_front_door() and "/" not in path:
        try:
            return _ia_read_file(path)
        except Exception:  # noqa: BLE001 — degrade to legacy GBrain read
            pass
    return _handle(gb.read_file, path)


@router.put("/file")
def write_file(body: FileWriteBody, user: str = Depends(require_user)) -> dict[str, Any]:
    _ = user
    # IvyeaAgent front door: card ids have no path separator. Route user-card
    # edits through the governed update/apply flow; official (builtin) cards are
    # not editable here — they go through the governance review/publish flow.
    if _ivyea_front_door() and "/" not in body.path:
        cid = body.path
        if not cid.startswith("user."):
            raise HTTPException(status_code=400, detail="这是 IvyeaAgent 官方治理知识卡，请在「治理中心」走审核发布流程编辑，不能直接改写。")
        try:
            title = (ia.knowledge_card(cid).get("card") or {}).get("title") or cid
        except Exception:  # noqa: BLE001
            title = cid
        try:
            resp = ia.knowledge_card_update(cid, title, _strip_source_header(body.content))
        except ia.IvyeaAgentError as e:
            raise HTTPException(status_code=400, detail=f"保存到 IvyeaAgent 失败：{e}") from e
        if not resp.get("ok"):
            raise HTTPException(status_code=400, detail=f"保存失败：{resp.get('result') or resp}")
        return {"ok": True, "path": cid, "saved_path": cid, "source": "ivyea-agent"}
    return _handle(gb.write_file, body.path, body.content)


@router.delete("/file")
def delete_file(path: str = Query(..., min_length=1, max_length=240), user: str = Depends(require_user)) -> dict[str, Any]:
    _ = user
    if _ivyea_front_door() and "/" not in path:
        cid = path
        if not cid.startswith("user."):
            raise HTTPException(status_code=400, detail="IvyeaAgent 官方治理知识卡不能在此删除，请到「治理中心」处理。")
        try:
            real = ia.knowledge_user_card_path(cid)
            if not real:
                raise HTTPException(status_code=404, detail="未找到该用户知识卡对应的文件，无法删除。")
            resp = ia.knowledge_delete_file(real)
        except ia.IvyeaAgentError as e:
            raise HTTPException(status_code=400, detail=f"从 IvyeaAgent 删除失败：{e}") from e
        if not resp.get("ok"):
            raise HTTPException(status_code=400, detail="删除失败。")
        return {"ok": True, "removed": [cid], "removed_card_ids": resp.get("removed_card_ids") or [cid], "source": "ivyea-agent"}
    return _handle(gb.delete_file, path)


@router.post("/import")
def import_brain() -> dict[str, Any]:
    return _handle(gb.import_brain)


@router.get("/git/status")
def git_status() -> dict[str, str]:
    return _handle(gb.git_status)


@router.post("/upload")
async def upload_knowledge(
    file: UploadFile = File(...),
    category: str = Form("inbox"),
    title: str | None = Form(None),
    import_after_save: bool = Form(True),
) -> dict[str, Any]:
    data = await file.read(bc.MAX_UPLOAD_BYTES + 1)
    if _ivyea_front_door():
        try:
            return _ia_upload_bytes(file.filename or "upload.txt", data, title, category)
        except Exception:  # noqa: BLE001 — degrade to legacy GBrain upload
            pass
    return _handle(bc.upload_knowledge, file.filename or "upload", data, category, title, import_after_save)


@router.get("/uploads")
def uploads(limit: int = Query(50, ge=1, le=100)) -> dict[str, Any]:
    return _handle(bc.list_uploads, limit)


@router.post("/ingest/text")
async def ingest_text(body: IngestTextBody) -> dict[str, Any]:
    if _ivyea_front_door():
        try:
            return await _ia_ingest_analyzed(body.text)
        except Exception:  # noqa: BLE001 — degrade to legacy GBrain ingest
            pass
    return _handle(bc.ingest_pasted_text, body.text, body.import_after_save)


@router.post("/ingest/url")
async def ingest_url(body: IngestUrlBody) -> dict[str, Any]:
    """Fetch URL, extract content via AI into clean Markdown, then ingest."""
    import httpx
    import re

    # A browser-like UA + Accept headers cut down on the 403 / anti-bot blocks
    # a bare "IvyeaOps/1.0" agent triggers on many sites.
    fetch_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(body.url, headers=fetch_headers)
            resp.raise_for_status()
            html = resp.text
    except httpx.HTTPStatusError as e:
        raise HTTPException(400, f"抓取失败：目标站点返回 {e.response.status_code}（可能有反爬限制），请换一个链接或改用「粘贴文本」。")
    except Exception as e:
        raise HTTPException(400, f"抓取失败：{e}")

    # Basic HTML to text extraction
    html = re.sub(r"<(script|style|noscript|header|footer|nav)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "\n", html)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text).strip()

    if len(text) < 30:
        raise HTTPException(400, "页面内容为空或无法解析，请换一个链接或改用「粘贴文本」。")

    # Truncate for AI processing (smaller window = faster single round-trip).
    raw_text = text[:8000]

    # ONE round-trip through the unified text chain (IvyeaAgent → global fallback
    # → DeepSeek …): clean the scrape into Markdown, then a ```json``` metadata
    # block for classification. A single call keeps us well under the client
    # timeout (two sequential model calls previously blew past 180s on real
    # pages). Markdown stays free-text (reliable); only the small trailing block
    # is JSON. This replaced the old hard-wired Codex CLI that failed with an
    # opaque "session_id: …" error whenever that one provider was down.
    from app.services import ai_synthesis_service

    prompt = (
        "你是网页正文整理 + 知识库分类器。先输出整理后的干净 Markdown 正文"
        "（只保留正文，去掉导航/广告/页脚/cookie 提示，用 #/##/### 分层，保持原文语言），"
        "然后另起一行输出一个用 ```json 和 ``` 包裹的元数据块，字段：\n"
        f"{_ANALYSIS_FIELDS_SPEC}\n"
        "严格顺序：Markdown 正文在前，最后是 ```json{...}``` 元数据，不要其它解释。\n\n"
        f"来源URL: {body.url}\n\n原始文本：\n{raw_text}"
    )
    raw_out = ""
    try:
        raw_out = (await ai_synthesis_service.generate_text(prompt)).strip()
    except Exception:  # noqa: BLE001 — fall back to raw extracted text below
        raw_out = ""

    # Split the cleaned Markdown from the trailing JSON metadata block.
    markdown, obj = "", {}
    if raw_out:
        meta = re.search(r"```json\s*(\{.*?\})\s*```", raw_out, re.DOTALL)
        if meta:
            markdown = raw_out[: meta.start()].strip()
            obj = bc._extract_json_object(meta.group(1)) or {}
        else:
            markdown = raw_out  # model ignored the metadata format; keep the body

    # Never hard-fail once we actually have page content: if the AI step is
    # unavailable or returned nothing, ingest the extracted text as-is.
    content = markdown or raw_text
    analysis = _analysis_from_obj(obj, content)
    body_text = f"> 来源：{body.url}\n\n{content}"

    # Ingest through the IvyeaAgent front door with auto title/tags/summary
    # (consistent with paste/file upload); fall back to legacy only when down.
    if _ivyea_front_door():
        try:
            return await _ia_ingest_analyzed(body_text, analysis=analysis)
        except Exception:  # noqa: BLE001 — degrade to legacy GBrain ingest
            pass
    return _handle(bc.ingest_pasted_text, body_text, body.import_after_save)


def _migration_marker_path():
    from app.core.config import settings as ops_settings
    return ops_settings.data_dir / "brain_chat_migrated.json"


def _load_migration_map() -> dict[str, str]:
    import json as _json
    p = _migration_marker_path()
    try:
        return _json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:  # noqa: BLE001 — a corrupt marker just re-migrates
        return {}


def _save_migration_map(mapping: dict[str, str]) -> None:
    import json as _json
    p = _migration_marker_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(_json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def _mirror_to_agent(session_id: str, principal: str) -> None:
    """把这条知识库对话的纯文本副本同步进 agent 会话库。

    **是镜像，不是搬家。** agent 的会话只存 {role, content}，而知识库对话的价值
    有一半在引证、告警、对话模式上 —— 真搬过去这些字段就没了。所以系统记录仍然是
    brain 自己的 sqlite，这里只同步一份正文，为的是让左栏能把三个板块的对话列在
    一起、点进来能回到这条（回的是 /brain，引证还在）。

    **不会越镜像越多**：agent 的 import 按 id 覆盖写，而 id 是从 brain 的
    session_id 算出来的，所以同一条对话每轮都写回同一个文件。

    已经被 /chat/migrate-to-agent 搬过的老会话复用它当初拿到的 id —— 否则同一条
    对话会在 agent 库里躺两份（一份随机 id 的旧迁移，一份新镜像）。

    整个过程 best-effort：镜像失败绝不能影响这一轮对话本身。
    """
    try:
        detail = bc.get_session(session_id)
        messages = [
            {"role": m.get("role"), "content": m.get("content") or ""}
            for m in (detail.get("messages") or [])
            if m.get("role") in {"user", "assistant"} and (m.get("content") or "").strip()
        ]
        if not messages:
            return
        mapping = _load_migration_map()
        agent_id = mapping.get(session_id) or f"imp-brain-{session_id}"
        resp = ia.chat_import({"id": agent_id, "messages": messages})
        if not resp.get("ok"):
            return
        agent_id = str(resp.get("id") or agent_id)
        if mapping.get(session_id) != agent_id:
            mapping[session_id] = agent_id
            _save_migration_map(mapping)
        console_sessions.register_session(agent_id, principal, "", "brain")
    except Exception:  # noqa: BLE001 — 镜像是附加价值，坏了也不该毁掉这轮对话
        return


@router.post("/chat/migrate-to-agent")
def chat_migrate_to_agent() -> dict[str, Any]:
    """One-time, idempotent migration of legacy brain_chat transcripts into the
    agent's native session store so the dock and workbench share one history.
    Safe to call repeatedly — already-migrated sessions are skipped via a marker."""
    from datetime import datetime

    if not _ivyea_front_door():
        return {"ok": False, "error": "IvyeaAgent 未连接，稍后重试", "migrated": 0, "skipped": 0, "total": 0}

    mapping = _load_migration_map()
    try:
        sessions = (bc.list_sessions(include_archived=True) or {}).get("sessions") or []
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"读取旧对话失败：{e}", "migrated": 0, "skipped": 0, "total": 0}

    migrated = 0
    skipped = 0
    for sess in sessions:
        sid = str(sess.get("id") or "")
        if not sid:
            continue
        if sid in mapping:
            skipped += 1
            continue
        try:
            detail = bc.get_session(sid)
        except Exception:  # noqa: BLE001 — skip unreadable session, retry next run
            skipped += 1
            continue
        messages = [
            {"role": m.get("role"), "content": m.get("content") or ""}
            for m in (detail.get("messages") or [])
            if m.get("role") in {"user", "assistant"} and (m.get("content") or "").strip()
        ]
        if not messages:
            mapping[sid] = ""  # remember empty sessions so we don't re-scan them
            skipped += 1
            continue
        created = None
        raw_created = sess.get("created_at")
        if isinstance(raw_created, str) and raw_created:
            try:
                created = datetime.fromisoformat(raw_created).timestamp()
            except ValueError:
                created = None
        try:
            resp = ia.chat_import({"messages": messages, "created": created})
        except Exception:  # noqa: BLE001 — agent hiccup, retry next run
            skipped += 1
            continue
        if resp.get("ok") and resp.get("id"):
            mapping[sid] = str(resp["id"])
            migrated += 1
        else:
            skipped += 1

    _save_migration_map(mapping)
    return {"ok": True, "migrated": migrated, "skipped": skipped, "total": len(sessions)}


@router.get("/chat/status")
def chat_status() -> dict[str, Any]:
    return _handle(bc.chat_model_status)


@router.get("/chat/sessions")
def chat_sessions(include_archived: bool = False) -> dict[str, Any]:
    return _handle(bc.list_sessions, include_archived)


@router.post("/chat/sessions")
def chat_session_create(body: ChatSessionCreateBody) -> dict[str, Any]:
    return _handle(bc.create_session, body.title, body.mode)


@router.get("/chat/sessions/{session_id}")
def chat_session_get(session_id: str) -> dict[str, Any]:
    return _handle(bc.get_session, session_id)


@router.patch("/chat/sessions/{session_id}")
def chat_session_update(session_id: str, body: ChatSessionUpdateBody) -> dict[str, Any]:
    return _handle(bc.update_session, session_id, body.title, body.archived)


@router.post("/chat/sessions/{session_id}/messages")
def chat_message_send(session_id: str, body: ChatMessageBody,
                      info: dict[str, Any] = Depends(require_user_info)) -> dict[str, Any]:
    out = _handle(bc.send_message, session_id, body.content)
    _mirror_to_agent(session_id, str(info.get("id") or ""))
    return out


def _sse(evt: dict[str, Any]) -> str:
    return f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"


async def _bridge_ivyea_stream(question: str, session_id: str):
    """Drive IvyeaAgent /v1/chat/stream (blocking, stdlib urllib) from a worker
    thread and yield its (event, data) frames into the async SSE handler."""
    q: asyncio.Queue = asyncio.Queue()
    sentinel = object()
    loop = asyncio.get_running_loop()

    def _produce() -> None:
        try:
            payload = {
                "message": question,
                "session_id": session_id or "",
                "plan_mode": True,   # read-only knowledge turn, no side effects
                "persist": False,    # IvyeaOps owns session persistence
            }
            for event, data in ia.chat_stream_events(payload):
                loop.call_soon_threadsafe(q.put_nowait, (event, data))
        except Exception as exc:  # noqa: BLE001 — surface as an error frame
            loop.call_soon_threadsafe(q.put_nowait, ("error", {"detail": str(exc)}))
        finally:
            loop.call_soon_threadsafe(q.put_nowait, sentinel)

    task = loop.run_in_executor(None, _produce)
    try:
        while True:
            item = await q.get()
            if item is sentinel:
                break
            yield item
    finally:
        await task


_STREAM_DEADLINE_S = int(__import__("os").environ.get("BRAIN_CHAT_HERMES_TIMEOUT", "180"))


@router.post("/chat/sessions/{session_id}/messages/stream")
async def chat_message_stream(session_id: str, body: ChatStreamBody,
                              info: dict[str, Any] = Depends(require_user_info)):
    """Stream a hermes answer token-by-token over SSE. Emits:
    start{user_message,citations} → token{text}* → done{assistant_message} | error{detail}."""

    async def gen():
        # Front door: route the knowledge chat through the governed IvyeaAgent
        # brain (its built-in Amazon knowledge base) when the local service is
        # up; skip the legacy GBrain citation retrieval in that case. Hermes /
        # the global text chain remain only as fallbacks when IvyeaAgent is down.
        use_ivyea = bc.ivyea_chat_available()
        try:
            turn = bc.begin_chat_turn(
                session_id, body.content, regenerate=body.regenerate,
                category=body.category, retrieve=not use_ivyea,
            )
        except (gb.GBrainError, bc.BrainChatError) as e:
            yield _sse({"type": "error", "detail": str(e)})
            return
        except Exception as e:  # noqa: BLE001
            yield _sse({"type": "error", "detail": f"准备对话失败：{e}"})
            return

        yield _sse({
            "type": "start",
            "user_message": turn["user_message"],
            "citations": turn["citations"],
            "regenerated": turn.get("regenerated", False),
        })

        parts: list[str] = []
        timed_out = False
        engine = ""  # which engine produced the answer: ivyea-agent | hermes | global

        # 1) IvyeaAgent brain (token-by-token over the local bridge).
        if use_ivyea:
            try:
                async for event, data in _bridge_ivyea_stream(turn.get("question") or body.content, session_id):
                    if event == "token":
                        text = str(data.get("text") or "")
                        if text:
                            parts.append(text)
                            yield _sse({"type": "token", "text": text})
                    elif event == "event":
                        # tool/thinking narration — keep the SSE connection warm
                        # during a long agent turn without polluting the answer.
                        yield ":hb\n\n"
                    elif event == "final":
                        full = str(data.get("text") or "")
                        if full and not "".join(parts).strip():
                            parts.append(full)
                            yield _sse({"type": "token", "text": full})
                    elif event == "error":
                        # degrade to the global chain below (do not surface yet)
                        break
                if "".join(parts).strip():
                    engine = "ivyea-agent"
            except Exception:  # noqa: BLE001 — degrade to fallbacks below
                pass

        # 2) Hermes (only when IvyeaAgent is unavailable), token-by-token.
        if not "".join(parts).strip() and not use_ivyea and bc.hermes_available():
            spec = bc.stream_spec(turn["prompt"])
            proc = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    *spec["argv"],
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    cwd=spec["cwd"],
                    env=spec["env"],
                    **no_window_kwargs(),  # no black console flash on Windows
                )
            except Exception:  # noqa: BLE001 — degrade to the global chain below
                proc = None

            if proc is not None:
                try:
                    proc.stdin.write(spec["stdin"])
                    await proc.stdin.drain()
                    proc.stdin.close()
                except Exception:
                    pass

                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                deadline = time.monotonic() + _STREAM_DEADLINE_S
                try:
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            timed_out = True
                            break
                        try:
                            data = await asyncio.wait_for(proc.stdout.read(1024), timeout=min(remaining, 15))
                        except asyncio.TimeoutError:
                            yield ":hb\n\n"  # heartbeat keeps proxies from closing the stream
                            continue
                        if not data:
                            break
                        text = decoder.decode(data)
                        if text:
                            parts.append(text)
                            yield _sse({"type": "token", "text": text})
                finally:
                    if proc.returncode is None:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    try:
                        await proc.wait()
                    except Exception:
                        pass

        if not engine and "".join(parts).strip():
            engine = "hermes"

        # Fall back to the unified global text chain (DeepSeek → Apimart → 全局兜底
        # 大模型) when IvyeaAgent/Hermes are absent or produced nothing — so the
        # knowledge-base chat still answers without any local agent.
        if not "".join(parts).strip():
            fallback_prompt = turn.get("prompt") or (turn.get("question") or body.content)
            try:
                async for chunk in bc.stream_global_answer(fallback_prompt):
                    parts.append(chunk)
                    yield _sse({"type": "token", "text": chunk})
                timed_out = False
                if "".join(parts).strip():
                    engine = "global"
            except Exception as e:  # noqa: BLE001
                if not "".join(parts).strip():
                    yield _sse({"type": "error", "detail": f"对话失败（IvyeaAgent 与全局兜底均不可用）：{e}"})
                    return

        answer = "".join(parts)
        if not answer.strip():
            yield _sse({"type": "error", "detail": "未能生成回答：请确认 IvyeaAgent 服务在运行，或在「系统配置 → 全局兜底大模型」配置一个文本模型。"})
            return
        try:
            assistant = bc.commit_chat_answer(session_id, answer, turn["citations"])
        except (gb.GBrainError, bc.BrainChatError) as e:
            yield _sse({"type": "error", "detail": str(e)})
            return
        # 落库成功之后才镜像，镜像里就不会出现"问了但还没答"的半截会话
        _mirror_to_agent(session_id, str(info.get("id") or ""))
        yield _sse({
            "type": "done",
            "assistant_message": assistant,
            "truncated": timed_out,
            "engine": engine,
            "citations_count": len(turn["citations"]),
        })

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.delete("/chat/messages/{message_id}")
def chat_message_delete(message_id: str) -> dict[str, Any]:
    return _handle(bc.delete_message, message_id)
