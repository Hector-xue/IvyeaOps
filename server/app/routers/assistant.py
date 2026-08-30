"""User-facing HTTP-only AI sandbox: free-form chat/writing + image generation.

Uses ONLY deepseek / apimart over HTTP — no local CLI agents, no shell, no MCP,
no filesystem. Safe to expose to registered (non-admin) users.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import base64
import json
import time
import uuid
from pathlib import Path
from typing import AsyncGenerator, List

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.core import hub_settings as _hs
from app.core.security import require_user
from app.core.skill_paths import STUDIO_ROOT
from app.services.ai_synthesis_service import (
    ASSISTANT_PROVIDER_BASE,
    _apimart_base,
    _apimart_key,
    _deepseek_key,
    assistant_text_cfg,
)

logger = logging.getLogger("ivyea.routers.assistant")

router = APIRouter()


# The global fallback model slot is the same one this AI 问答 panel drives, so
# its config reader and provider→base map live canonically in
# ai_synthesis_service (imported above) — no duplicate maps to drift.
def _assistant_cfg() -> dict:
    """Return the user-configured AI-chat model, or {} to use the default chain."""
    return assistant_text_cfg()


class Msg(BaseModel):
    role: str
    content: str


class ChatReq(BaseModel):
    messages: List[Msg]


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


async def _deepseek_chat(messages: List[Msg]) -> AsyncGenerator[str, None]:
    key = _deepseek_key()
    if not key:
        raise RuntimeError("DeepSeek key 未配置")
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        "stream": True,
        "max_tokens": 4096,
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=30)) as c:
        async with c.stream("POST", "https://api.deepseek.com/chat/completions",
                            json=payload, headers={"Authorization": f"Bearer {key}"}) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                try:
                    ev = json.loads(raw)
                except Exception:
                    continue
                choices = ev.get("choices", [])
                if choices:
                    t = choices[0].get("delta", {}).get("content", "")
                    if t:
                        yield t


async def _apimart_chat(messages: List[Msg]) -> AsyncGenerator[str, None]:
    key = _apimart_key()
    if not key:
        raise RuntimeError("Apimart key 未配置")
    system = " ".join(m.content for m in messages if m.role == "system")
    msgs = [{"role": m.role, "content": m.content} for m in messages if m.role in ("user", "assistant")]
    payload = {"model": "claude-sonnet-4-6", "max_tokens": 4096, "messages": msgs, "stream": True}
    if system:
        payload["system"] = system
    async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=30)) as c:
        async with c.stream("POST", f"{_apimart_base()}/messages", json=payload,
                            headers={"Authorization": f"Bearer {key}", "anthropic-version": "2023-06-01"}) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                try:
                    ev = json.loads(raw)
                except Exception:
                    continue
                if ev.get("type") == "content_block_delta":
                    t = ev.get("delta", {}).get("text", "")
                    if t:
                        yield t


async def _configured_chat(cfg: dict, messages: List[Msg]) -> AsyncGenerator[str, None]:
    """Stream from a user-configured OpenAI-compatible chat endpoint."""
    provider = cfg["provider"]
    key = cfg["api_key"]
    if not key:
        raise RuntimeError(f"{provider} key 未配置")
    if provider == "anthropic":
        # Anthropic-native API (messages endpoint)
        base = cfg["base_url"] or "https://api.anthropic.com/v1"
        system = " ".join(m.content for m in messages if m.role == "system")
        msgs = [{"role": m.role, "content": m.content} for m in messages if m.role in ("user", "assistant")]
        payload = {"model": cfg["model"] or "claude-sonnet-4-6", "max_tokens": 4096, "messages": msgs, "stream": True}
        if system:
            payload["system"] = system
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=30)) as c:
            async with c.stream("POST", f"{base}/messages", json=payload,
                                headers={"Authorization": f"Bearer {key}", "anthropic-version": "2023-06-01"}) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(line[5:].strip())
                    except Exception:
                        continue
                    if ev.get("type") == "content_block_delta":
                        t = ev.get("delta", {}).get("text", "")
                        if t:
                            yield t
        return
    # OpenAI-compatible (deepseek/openai/openrouter/groq/together/xiaomi/kimi/custom)
    base = cfg["base_url"] or ASSISTANT_PROVIDER_BASE.get(provider, "")
    if not base:
        raise RuntimeError(f"{provider} 需要填写 Base URL")
    payload = {
        "model": cfg["model"] or "",
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        "stream": True, "max_tokens": 4096,
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=30)) as c:
        async with c.stream("POST", f"{base.rstrip('/')}/chat/completions",
                            json=payload, headers={"Authorization": f"Bearer {key}"}) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                try:
                    ev = json.loads(raw)
                except Exception:
                    continue
                choices = ev.get("choices", [])
                if choices:
                    t = choices[0].get("delta", {}).get("content", "")
                    if t:
                        yield t


@router.post("/chat")
async def chat(req: ChatReq, _user: str = Depends(require_user)) -> StreamingResponse:
    if not req.messages:
        raise HTTPException(400, "messages cannot be empty")

    cfg = _assistant_cfg()

    async def gen() -> AsyncGenerator[str, None]:
        # User-configured model takes priority; no silent fallback so the user
        # sees real errors from their chosen provider.
        if cfg:
            provider = cfg["provider"]
            try:
                got = False
                async for t in _configured_chat(cfg, req.messages):
                    got = True
                    yield _sse({"type": "token", "text": t, "provider": provider})
                if got:
                    yield _sse({"type": "done", "provider": provider})
                    return
            except Exception as e:
                yield _sse({"type": "error", "detail": f"{provider}: {e}"})
                return

        # No explicit config → default deepseek → apimart chain.
        last_err = None
        for provider, fn in (("deepseek", _deepseek_chat), ("apimart", _apimart_chat)):
            got = False
            try:
                async for t in fn(req.messages):
                    got = True
                    yield _sse({"type": "token", "text": t, "provider": provider})
                if got:
                    yield _sse({"type": "done", "provider": provider})
                    return
            except Exception as e:
                last_err = f"{provider}: {e}"
                continue
        yield _sse({"type": "error", "detail": last_err or "无可用 AI（请在系统配置中填 DeepSeek 或 Apimart key）"})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class ImageReq(BaseModel):
    prompt: str
    size: str = "1024x1024"
    n: int = 1
    # Optional source image(s) for image-to-image editing. Accepts http(s) URLs or
    # base64 data URLs (data:image/...;base64,...). When present, the model edits
    # the given image instead of generating from scratch.
    image_urls: List[str] | None = None


def _image_cfg() -> dict:
    """Image-gen model/key/base, falling back to apimart defaults."""
    cfg = _hs.load()
    return {
        "model":    (cfg.get("image_model") or "").strip() or "gpt-image-2",
        "api_key":  (cfg.get("image_api_key") or "").strip() or _apimart_key(),
        "base_url": (cfg.get("image_base_url") or "").strip() or _apimart_base(),
    }


# ── Image-to-image editing via /images/edits ────────────────────────────────
# /images/edits truly EDITS the given image (preserves its content), unlike
# /images/generations + image_urls which only generates a *new* image inspired by
# the reference. The edits endpoint is synchronous (~70-90s), so we run it as a
# small in-memory background job and expose the same task_id/poll interface the
# text-to-image path already uses.
_EDIT_JOBS: dict[str, dict] = {}

# Edit jobs are also written to disk so a completed image survives a backend
# restart (the poll can still fetch it) and an interrupted job reports a clear
# message instead of "任务不存在". ~/.hermes/imagegen-jobs/ (respects HERMES_HOME).
_JOBS_DIR: Path = STUDIO_ROOT.parent / "imagegen-jobs"


def _prune_edit_jobs() -> None:
    if len(_EDIT_JOBS) <= 60:
        return
    for k in sorted(_EDIT_JOBS, key=lambda j: _EDIT_JOBS[j].get("ts", 0.0))[: len(_EDIT_JOBS) - 60]:
        _EDIT_JOBS.pop(k, None)


def _job_file(job_id: str) -> Path:
    # job_id is our own uuid-hex ("edit_<hex>") — no path traversal risk.
    return _JOBS_DIR / f"{job_id}.json"


def _persist_job(job_id: str) -> None:
    """Write-through the in-memory job record to disk (best-effort)."""
    j = _EDIT_JOBS.get(job_id)
    if j is None:
        return
    try:
        _JOBS_DIR.mkdir(parents=True, exist_ok=True)
        _job_file(job_id).write_text(
            json.dumps({**j, "id": job_id}, ensure_ascii=False), encoding="utf-8"
        )
        _prune_job_files()
    except Exception:
        logger.debug("_JOBS_DIR.mkdir 失败（旁路，已忽略）", exc_info=True)


def _load_job(job_id: str) -> dict | None:
    try:
        return json.loads(_job_file(job_id).read_text(encoding="utf-8"))
    except Exception:
        return None


def _prune_job_files() -> None:
    try:
        files = sorted(_JOBS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for p in files[:-120]:  # keep the 120 most recent on disk
            p.unlink(missing_ok=True)
    except Exception:
        logger.debug("sorted 失败（旁路，已忽略）", exc_info=True)


def _sweep_orphaned_jobs() -> None:
    """On startup, in-memory jobs are empty, so any persisted "running" job is
    orphaned by a previous process (the background asyncio task cannot resume
    across a restart). Mark those failed so their poll returns a clear message
    instead of hanging on "running" forever."""
    try:
        for p in _JOBS_DIR.glob("*.json"):
            try:
                j = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if j.get("status") == "running":
                j["status"] = "failed"
                j["error"] = "服务重启导致任务中断，请重试"
                p.write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
    except Exception:
        logger.debug("try 失败（旁路，已忽略）", exc_info=True)


_sweep_orphaned_jobs()


# ── 附图引用句柄（任务台的图生图靠它）────────────────────────────────────────
#
# 任务台里用户贴一张图说"把它改成夜景"，agent 要把这张图当原图传给 image_generate。
# 但 data URL 有几百 KB —— 让它穿过模型的工具调用参数是不可能的（光 base64 就能
# 撑爆上下文，而且模型会逐字重抄，抄错一位图就废了）。
#
# 所以图**不进模型**：ops 这边先把它落盘换一个短句柄 `ivyea-ref://<id>`，只把句柄
# 告诉 agent；agent 原样把句柄传回 image_generate，服务端再从盘上取回原图。
# 模型全程没碰过图片本体。
_REFS_DIR: Path = STUDIO_ROOT.parent / "imagegen-refs"
_REF_SCHEME = "ivyea-ref://"
_MAX_REF_BYTES = 12 * 1024 * 1024
_KEEP_REFS = 200


def _ref_file(ref_id: str) -> Path | None:
    """句柄 → 盘上的文件。**只认自己发的 uuid-hex**，杜绝路径穿越。"""
    ref_id = (ref_id or "").strip()
    if not ref_id or len(ref_id) > 40 or not all(c in "0123456789abcdef" for c in ref_id):
        return None
    hit = sorted(_REFS_DIR.glob(f"{ref_id}.*"))
    return hit[0] if hit else None


#: 进程内自增序号。见 _new_ref_id —— 毫秒级时间戳不足以给同一批图定先后。
_REF_SEQ = itertools.count()


def _new_ref_id() -> str:
    """毫秒时间戳（定宽 hex）+ 进程内自增序号 + 随机尾巴。

    **前缀是为了让文件名按时间排序**：清理旧图时不能拿 mtime 排 —— 同一毫秒内落
    的几张图 mtime 会并列，谁被删就看文件系统心情，用户刚贴的那张也可能被清掉，
    然后 agent 一调 image_generate 就是"附图引用已过期"。

    **但毫秒本身也不够细。** 一次贴 6 张图在快机器上会全落进同一毫秒，那时前缀
    完全相同，排序退回到那条随机尾巴 —— 刚贴的那张照样可能排在最前面被当成"最旧的"
    删掉，正是上面这段注释想避免的事，只是从 mtime 挪到了文件名。CI 上偶发复现
    （同一个 PR 里 ubuntu 两个 py 版本齐挂、macOS/Windows 全过，就是这个时序差）。
    加一个进程内自增序号，同一毫秒内也有严格先后。
    """
    return f"{int(time.time() * 1000):011x}{next(_REF_SEQ) & 0xFFFF:04x}{uuid.uuid4().hex[:5]}"


def _prune_refs() -> None:
    try:
        files = sorted(_REFS_DIR.glob("*.*"), key=lambda p: p.name)
        for p in files[:-_KEEP_REFS]:
            p.unlink(missing_ok=True)
    except Exception:
        logger.debug("_prune_refs 失败（旁路，已忽略）", exc_info=True)


async def _source_to_bytes(url: str) -> tuple[bytes, str]:
    """Return (image_bytes, mime) from an ivyea-ref:// handle, a base64 data URL,
    or an http(s) URL."""
    if url.startswith(_REF_SCHEME):
        path = _ref_file(url[len(_REF_SCHEME):])
        if path is None or not path.exists():
            raise ValueError("附图引用已过期或不存在，请重新上传原图")
        ext = path.suffix.lstrip(".").lower()
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext or 'png'}"
        return path.read_bytes(), mime
    if url.startswith("data:"):
        head, _, b64 = url.partition(",")
        mime = head[5:].split(";")[0] or "image/png"
        return base64.b64decode(b64), mime
    async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=30)) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.content, (r.headers.get("content-type") or "image/png").split(";")[0]


_BACKPRESSURE = ("try again", "please wait", "rate limit", "too many", "overload", "busy")


def _upstream_message(body: str) -> str:
    """Pull the human-readable message out of an upstream error body; '' for HTML pages."""
    body = (body or "").strip()
    if body[:1] == "<" or "<html" in body[:200].lower():
        return ""  # Cloudflare/HTML gateway page — useless to the user.
    try:
        j = json.loads(body)
        if isinstance(j, dict):
            err = j.get("error")
            if isinstance(err, dict) and err.get("message"):
                return str(err["message"])
            if isinstance(err, str) and err:
                return err
            if j.get("message"):
                return str(j["message"])
    except Exception:
        logger.debug("json.loads 失败（旁路，已忽略）", exc_info=True)
    return body


def _edit_error_text(status: int, body: str) -> str:
    msg = _upstream_message(body)
    tail = ("：" + msg[:180]) if msg else ""
    # 502/503/504 = gateway timeout; 429 or an explicit "please wait" = provider overload.
    if status in (429, 502, 503, 504) or any(s in msg.lower() for s in _BACKPRESSURE):
        return f"编辑失败：生图上游（Apimart）繁忙或超时，请稍后再试{tail}"
    return f"编辑失败 HTTP {status}{tail}"


async def _run_edit_job(job_id: str, model: str, prompt: str, size: str, key: str, base: str, image_url: str) -> None:
    ts = _EDIT_JOBS.get(job_id, {}).get("ts", time.time())
    try:
        img, mime = await _source_to_bytes(image_url)
        ext = "jpg" if ("jpe" in mime or "jpg" in mime) else ("webp" if "webp" in mime else "png")
        files = {"image": (f"source.{ext}", img, mime or "image/png")}
        data = {"model": model, "prompt": prompt, "size": size, "n": "1"}
        # /images/edits is slow and its gateway intermittently returns 5xx/504; retry transient failures.
        r = None
        last = ""
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(280, connect=30)) as c:
                    r = await c.post(f"{base}/images/edits", data=data, files=files,
                                     headers={"Authorization": f"Bearer {key}"})
                if r.status_code < 500:
                    break  # success or 4xx (won't get better by retrying)
                # Explicit provider backpressure ("please wait / try again later"): an
                # immediate retry won't help and just hammers an overloaded upstream — stop.
                if any(s in (r.text or "").lower() for s in _BACKPRESSURE):
                    break
                last = f"HTTP {r.status_code}"
            except (httpx.TimeoutException, httpx.TransportError) as e:
                r = None
                last = str(e) or e.__class__.__name__
            if attempt < 2:
                await asyncio.sleep(2 * (attempt + 1))
        if r is None:
            _EDIT_JOBS[job_id] = {"status": "failed", "images": [], "error": f"编辑失败：连接生图上游超时（{last}），请稍后重试", "ts": ts}
            return
        if r.status_code >= 400:
            _EDIT_JOBS[job_id] = {"status": "failed", "images": [], "error": _edit_error_text(r.status_code, r.text), "ts": ts}
            return
        images: list[str] = []
        for item in (r.json().get("data") or []):
            u = item.get("url")
            if isinstance(u, list):
                images.extend(x for x in u if isinstance(x, str))
            elif isinstance(u, str):
                images.append(u)
            elif item.get("b64_json"):
                images.append("data:image/png;base64," + item["b64_json"])
        _EDIT_JOBS[job_id] = {"status": "completed" if images else "failed", "images": images,
                              "error": None if images else "编辑未返回图片", "ts": ts}
    except Exception as e:  # noqa: BLE001
        _EDIT_JOBS[job_id] = {"status": "failed", "images": [], "error": f"编辑失败：{e}", "ts": ts}
    finally:
        # Write-through the terminal state (completed/failed) once, whichever path we took.
        _persist_job(job_id)


@router.post("/image")
async def image_submit(req: ImageReq, _user: str = Depends(require_user)) -> dict:
    """Submit an image job (async). Returns a task_id the client polls via
    /image/status. With a source image -> true editing (/images/edits, run as a
    background job); otherwise text-to-image (/images/generations)."""
    ic = _image_cfg()
    key = ic["api_key"]
    if not key:
        raise HTTPException(400, "生图 key 未配置（系统配置 → 应用模型 → AI 生图）")
    if not req.prompt.strip():
        raise HTTPException(400, "提示词不能为空")

    refs = [u for u in (req.image_urls or []) if isinstance(u, str) and u.strip()]
    if refs:
        # Image-to-image: edit the (first) source image so its content is kept.
        job_id = "edit_" + uuid.uuid4().hex[:16]
        _EDIT_JOBS[job_id] = {"status": "running", "images": [], "error": None, "ts": time.time()}
        _prune_edit_jobs()
        _persist_job(job_id)  # if the backend restarts mid-edit, the startup sweep flags it
        asyncio.create_task(_run_edit_job(job_id, ic["model"], req.prompt, req.size, key, ic["base_url"], refs[0]))
        return {"task_id": job_id}

    # Text-to-image. Apimart returns an async task_id (poll /tasks/{id}); a
    # standard OpenAI-compatible platform returns the image synchronously in
    # data[*].b64_json/url. Support BOTH so users can point image_base_url at any
    # platform when Apimart is down.
    payload = {"model": ic["model"], "prompt": req.prompt, "n": min(max(req.n, 1), 4), "size": req.size}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=30)) as c:
            r = await c.post(f"{ic['base_url']}/images/generations", json=payload,
                             headers={"Authorization": f"Bearer {key}"})
    except Exception as e:
        raise HTTPException(502, f"生图请求失败：{e}")
    if r.status_code >= 400:
        raise HTTPException(502, f"生图失败 HTTP {r.status_code}：{_upstream_message(r.text) or r.text[:200]}")
    data = r.json().get("data") or []
    first = data[0] if data else {}
    tid = first.get("task_id")
    if tid:
        return {"task_id": tid}   # async (Apimart) — client polls /image/status

    # Synchronous (OpenAI-compatible): the image(s) are already here — stash them
    # in the same in-memory job map so /image/status serves them on the first poll.
    images: List[str] = []
    for it in data:
        if it.get("b64_json"):
            images.append("data:image/png;base64," + it["b64_json"])
        elif isinstance(it.get("url"), str):
            images.append(it["url"])
    if not images:
        raise HTTPException(502, "生图未返回 task_id 也没有图片，请检查自定义生图接口是否兼容 /images/generations")
    job_id = "sync_" + uuid.uuid4().hex[:16]
    _EDIT_JOBS[job_id] = {"status": "completed", "images": images, "error": None, "ts": time.time()}
    _prune_edit_jobs()
    _persist_job(job_id)
    return {"task_id": job_id}


class ImageRefReq(BaseModel):
    """任务台把一张附图换成短句柄。data_url 只接受 data:image/...;base64,..."""
    data_url: str


@router.post("/image/ref")
def image_ref(req: ImageRefReq, _user: str = Depends(require_user)) -> dict:
    """把一张 data URL 附图落盘，返回 `ivyea-ref://<id>` 句柄。

    返回的句柄可以原样传给 image_generate 的 image_urls 做图生图 —— 图片本体
    留在服务器上，模型只经手这一小串字符。
    """
    url = (req.data_url or "").strip()
    if not url.startswith("data:image/"):
        raise HTTPException(400, "data_url 必须是 data:image/... 开头的 data URI")
    head, _, b64 = url.partition(",")
    if not b64:
        raise HTTPException(400, "data_url 里没有 base64 内容")
    try:
        raw = base64.b64decode(b64)
    except Exception:
        raise HTTPException(400, "data_url 的 base64 解不开")
    if not raw:
        raise HTTPException(400, "图片是空的")
    if len(raw) > _MAX_REF_BYTES:
        raise HTTPException(413, "图片过大（>12MB），请压缩后再试")
    mime = head[5:].split(";")[0] or "image/png"
    sub = mime.split("/")[-1].lower()
    ext = {"jpeg": "jpg", "svg+xml": "svg"}.get(sub, sub if sub.isalnum() else "png")
    ref_id = _new_ref_id()
    try:
        _REFS_DIR.mkdir(parents=True, exist_ok=True)
        (_REFS_DIR / f"{ref_id}.{ext}").write_bytes(raw)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"附图落盘失败：{e}")
    _prune_refs()
    return {"ref": f"{_REF_SCHEME}{ref_id}", "bytes": len(raw)}


@router.get("/image/ref/{ref_id}")
def image_ref_file(ref_id: str, _user: str = Depends(require_user)) -> FileResponse:
    """按句柄取回原图 —— 会话记录里那张缩略图就是从这里来的。

    历史会话是从 agent 的存档里恢复的，而存档里只有文字（图片本体从来不进模型）。
    没有这个出口的话，刷新之后"我发过一张图"这件事在界面上就彻底消失了 —— 用户的
    原话是"会话记录里面也没有展示我发送的图片"。

    句柄只认自己发的 uuid-hex（`_ref_file` 里堵的路径穿越），落盘的图只保留最近
    _KEEP_REFS 张，过期的返回 404，由前端显示成"附图已过期"。
    """
    path = _ref_file(ref_id)
    if path is None or not path.exists():
        raise HTTPException(404, "附图引用已过期或不存在")
    ext = path.suffix.lstrip(".").lower()
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext or 'png'}"
    return FileResponse(path, media_type=mime)


# ── Agent 回答里夹的图（show_image 工具）─────────────────────────────────────
#
# 和上面那个 imagegen-refs **故意分开存**。那个库是"用户刚贴的原图"的中转站，
# 只留最近 _KEEP_REFS(200) 张、转得飞快 —— 而这里存的是**已经写进会话存档的图**：
# 模型把 `![](…)` 写进了回答正文，那段文字会一直躺在会话记录里。混进那个库的话，
# 200 张一冲，几天前的会话打开就是一排碎图，而正文还言之凿凿地在描述它们。
_SHOTS_DIR: Path = STUDIO_ROOT.parent / "session-images"
_MAX_SHOT_BYTES = 12 * 1024 * 1024
_KEEP_SHOTS = 4000

#: 认得出的图片魔数。**按文件头判，不按扩展名** —— show_image 收的是模型给的
#: 路径，扩展名是它说了算的，只看后缀等于让模型决定"这个文件算不算图片"，
#: 那这个出口就成了任意文件外泄通道。
_IMAGE_MAGIC: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
    (b"BM", "bmp", "image/bmp"),
)
# SVG **故意不收**：它是文本，能带 <script>，而这个出口是同源的 —— 收下它等于
# 让模型往用户的会话里塞一段同源可执行脚本。要展示矢量图就先转成 png。


def _sniff_image(raw: bytes) -> tuple[str, str] | None:
    """(扩展名, mime)，认不出来就是 None。webp/avif 是 RIFF/ftyp 容器，单独判。"""
    for magic, ext, mime in _IMAGE_MAGIC:
        if raw.startswith(magic):
            return ext, mime
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp", "image/webp"
    if raw[4:8] == b"ftyp" and raw[8:12] in (b"avif", b"avis"):
        return "avif", "image/avif"
    return None


def _shot_file(name: str) -> Path | None:
    """`<hex-id>.<ext>` → 盘上的文件。**只认自己发的名字**，杜绝路径穿越。

    URL 里带扩展名是有意的：前端判"这条链接是不是图"要靠后缀（reportFormat 的
    looksLikeImage），不带后缀的裸链接会被渲染成一条普通链接。显式 `![]()` 语法
    不受影响，但不能指望模型每次都规规矩矩写成图片语法。
    """
    stem, _, ext = (name or "").strip().partition(".")
    if not stem or len(stem) > 40 or not all(c in "0123456789abcdef" for c in stem):
        return None
    if not ext.isalnum() or len(ext) > 5:
        return None
    p = _SHOTS_DIR / f"{stem}.{ext.lower()}"
    return p if p.exists() else None


def _prune_shots() -> None:
    try:
        files = sorted(_SHOTS_DIR.glob("*.*"), key=lambda p: p.name)
        for p in files[:-_KEEP_SHOTS]:
            p.unlink(missing_ok=True)
    except Exception:
        logger.debug("_prune_shots 失败（旁路，已忽略）", exc_info=True)


def store_session_image(raw: bytes) -> tuple[str, int]:
    """把一张图存进会话图库，返回 (可直接写进 markdown 的站内地址, 字节数)。

    校验放在这里而不是调用方：这是**唯一**的入口，堵在入口才堵得住。
    """
    if not raw:
        raise ValueError("文件是空的")
    if len(raw) > _MAX_SHOT_BYTES:
        raise ValueError(f"图片过大（{len(raw) // 1024 // 1024}MB > 12MB）")
    sniffed = _sniff_image(raw)
    if sniffed is None:
        raise ValueError("这个文件不是图片（按文件头判定；支持 png/jpg/gif/webp/bmp/avif，不收 svg）")
    ext, _mime = sniffed
    name = f"{_new_ref_id()}.{ext}"
    _SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    (_SHOTS_DIR / name).write_bytes(raw)
    _prune_shots()
    return f"/api/assistant/session-image/{name}", len(raw)


# ── 会话附件的原件 ──────────────────────────────────────────────────────────
#
# 抽出来的正文进了会话存档（模型看的是那个），但**原件也得留一份**：用户回头翻
# 记录时要能把当初传的那份 PDF 下回来。存档里只有文字的话，"我上传过一份报价单"
# 就只剩一个文件名，点不开。
_FILES_DIR: Path = STUDIO_ROOT.parent / "session-files"
_MAX_SESSION_FILE_BYTES = 10 * 1024 * 1024
_KEEP_SESSION_FILES = 4000


def _session_file(name: str) -> Path | None:
    """`<hex-id>.<ext>` → 盘上的文件。**只认自己发的名字**，杜绝路径穿越。"""
    stem, _, ext = (name or "").strip().partition(".")
    if not stem or len(stem) > 40 or not all(c in "0123456789abcdef" for c in stem):
        return None
    if ext and (not ext.isalnum() or len(ext) > 8):
        return None
    p = _FILES_DIR / (f"{stem}.{ext.lower()}" if ext else stem)
    return p if p.exists() else None


def store_session_file(raw: bytes, filename: str) -> str:
    """存一份会话附件原件，返回站内下载地址。"""
    if len(raw) > _MAX_SESSION_FILE_BYTES:
        raise ValueError("文件过大")
    ext = "".join(ch for ch in Path(filename or "").suffix.lstrip(".").lower() if ch.isalnum())[:8]
    name = f"{_new_ref_id()}{('.' + ext) if ext else ''}"
    _FILES_DIR.mkdir(parents=True, exist_ok=True)
    (_FILES_DIR / name).write_bytes(raw)
    try:
        files = sorted(_FILES_DIR.glob("*"), key=lambda p: p.name)
        for old in files[:-_KEEP_SESSION_FILES]:
            old.unlink(missing_ok=True)
    except Exception:
        logger.debug("会话附件清理失败（旁路，已忽略）", exc_info=True)
    return f"/api/assistant/session-file/{name}"


@router.get("/session-file/{name}")
def session_file_download(name: str, filename: str = "",
                          _user: str = Depends(require_user)) -> FileResponse:
    """把当初传的那份原件下回来。

    **一律强制下载，绝不 inline 渲染。** 这里存的是用户上传的任意文件，其中可能有
    .html/.svg 这类同源就能执行脚本的东西；让浏览器直接打开它等于在自己的域上执行
    别人的内容。`application/octet-stream` + Content-Disposition: attachment 两道
    一起上，浏览器就只会存盘。
    """
    path = _session_file(name)
    if path is None:
        raise HTTPException(404, "附件不存在或已过期")
    # 下载时用回原来的文件名（前端传过来），但**只取基名**：带路径的名字会被
    # 某些客户端当成目录写下去。
    shown = Path(filename or path.name).name[:120] or path.name
    return FileResponse(path, media_type="application/octet-stream", filename=shown)


@router.get("/session-image/{name}")
def session_image_file(name: str, _user: str = Depends(require_user)) -> FileResponse:
    """取回 agent 夹在回答里的那张图。

    鉴权是 cookie（见 core/security.require_user），所以正文里的 `<img src>`
    同源请求会自动带上 —— 不需要前端做任何事。
    """
    path = _shot_file(name)
    if path is None:
        raise HTTPException(404, "图片不存在或已过期")
    raw = path.read_bytes()[:16]
    sniffed = _sniff_image(raw)
    # 落盘时已经验过一次，这里再验一次是防"盘上的文件被换掉了"：这个出口是同源的，
    # 端出一个不是图片的东西代价太大，多读 16 字节换这个确定性很划算。
    mime = sniffed[1] if sniffed else "application/octet-stream"
    return FileResponse(path, media_type=mime)


@router.get("/image/status")
async def image_status(task_id: str, _user: str = Depends(require_user)) -> dict:
    # Local image jobs (image-to-image edit + synchronous text-to-image) are
    # tracked in-process, with a disk fallback so a completed result survives a
    # backend restart and an interrupted job reports a clear message.
    if task_id.startswith(("edit_", "sync_")) or task_id in _EDIT_JOBS:
        j = _EDIT_JOBS.get(task_id) or _load_job(task_id)
        if not j:
            return {"status": "failed", "progress": 0, "images": [], "error": "任务不存在或已过期，请重试"}
        running = j["status"] == "running"
        return {"status": "processing" if running else j["status"],
                "progress": 50 if running else 100, "images": j["images"], "error": j["error"]}
    ic = _image_cfg()
    key = ic["api_key"]
    if not key:
        raise HTTPException(400, "生图 key 未配置")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=30)) as c:
            r = await c.get(f"{ic['base_url']}/tasks/{task_id}", headers={"Authorization": f"Bearer {key}"})
    except Exception as e:
        raise HTTPException(502, f"查询失败：{e}")
    if r.status_code >= 400:
        raise HTTPException(502, f"查询失败 HTTP {r.status_code}")
    d = r.json().get("data", {}) or {}
    st = d.get("status", "")
    out = {"status": st, "progress": d.get("progress", 0), "images": [], "error": None}
    if st == "completed":
        for im in (d.get("result", {}) or {}).get("images", []) or []:
            u = im.get("url") if isinstance(im, dict) else None
            if isinstance(u, list):
                out["images"].extend(u)
            elif isinstance(u, str):
                out["images"].append(u)
    elif st in ("failed", "error"):
        out["error"] = str(d.get("error") or "生图失败")
    return out


@router.get("/status")
def status(_user: str = Depends(require_user)) -> dict:
    cfg = _assistant_cfg()
    ic = _image_cfg()
    return {
        "deepseek": bool(_deepseek_key()),
        "apimart": bool(_apimart_key()),
        "chat_configured": bool(cfg),
        "chat_provider": cfg.get("provider", "") if cfg else "",
        "image_ready": bool(ic["api_key"]),
    }
