"""User-facing HTTP-only AI sandbox: free-form chat/writing + image generation.

Uses ONLY deepseek / apimart over HTTP — no local CLI agents, no shell, no MCP,
no filesystem. Safe to expose to registered (non-admin) users.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from typing import AsyncGenerator, List

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core import hub_settings as _hs
from app.core.security import require_user
from app.services.ai_synthesis_service import (
    ASSISTANT_PROVIDER_BASE,
    _apimart_base,
    _apimart_key,
    _deepseek_key,
    assistant_text_cfg,
)

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
    async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as c:
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
    async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as c:
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
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as c:
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
    async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as c:
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


def _prune_edit_jobs() -> None:
    if len(_EDIT_JOBS) <= 60:
        return
    for k in sorted(_EDIT_JOBS, key=lambda j: _EDIT_JOBS[j].get("ts", 0.0))[: len(_EDIT_JOBS) - 60]:
        _EDIT_JOBS.pop(k, None)


async def _source_to_bytes(url: str) -> tuple[bytes, str]:
    """Return (image_bytes, mime) from a base64 data URL or an http(s) URL."""
    if url.startswith("data:"):
        head, _, b64 = url.partition(",")
        mime = head[5:].split(";")[0] or "image/png"
        return base64.b64decode(b64), mime
    async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10)) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.content, (r.headers.get("content-type") or "image/png").split(";")[0]


async def _run_edit_job(job_id: str, model: str, prompt: str, size: str, key: str, base: str, image_url: str) -> None:
    ts = _EDIT_JOBS.get(job_id, {}).get("ts", time.time())
    try:
        img, mime = await _source_to_bytes(image_url)
        ext = "jpg" if ("jpe" in mime or "jpg" in mime) else ("webp" if "webp" in mime else "png")
        files = {"image": (f"source.{ext}", img, mime or "image/png")}
        data = {"model": model, "prompt": prompt, "size": size, "n": "1"}
        async with httpx.AsyncClient(timeout=httpx.Timeout(280, connect=10)) as c:
            r = await c.post(f"{base}/images/edits", data=data, files=files,
                             headers={"Authorization": f"Bearer {key}"})
        if r.status_code >= 400:
            _EDIT_JOBS[job_id] = {"status": "failed", "images": [], "error": f"编辑失败 HTTP {r.status_code}：{r.text[:200]}", "ts": ts}
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
        asyncio.create_task(_run_edit_job(job_id, ic["model"], req.prompt, req.size, key, ic["base_url"], refs[0]))
        return {"task_id": job_id}

    # Text-to-image
    payload = {"model": ic["model"], "prompt": req.prompt, "n": min(max(req.n, 1), 4), "size": req.size}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10)) as c:
            r = await c.post(f"{ic['base_url']}/images/generations", json=payload,
                             headers={"Authorization": f"Bearer {key}"})
    except Exception as e:
        raise HTTPException(502, f"生图请求失败：{e}")
    if r.status_code >= 400:
        raise HTTPException(502, f"Apimart 生图失败 HTTP {r.status_code}：{r.text[:200]}")
    item = (r.json().get("data") or [{}])[0]
    tid = item.get("task_id")
    if not tid:
        raise HTTPException(502, "Apimart 未返回任务 ID")
    return {"task_id": tid}


@router.get("/image/status")
async def image_status(task_id: str, _user: str = Depends(require_user)) -> dict:
    # Local image-edit jobs (image-to-image) are tracked in-process.
    if task_id.startswith("edit_") or task_id in _EDIT_JOBS:
        j = _EDIT_JOBS.get(task_id)
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
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10)) as c:
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
