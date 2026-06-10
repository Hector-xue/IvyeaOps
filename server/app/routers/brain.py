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

from app.core.security import require_user
from app.services import brain_chat_service as bc
from app.services import gbrain_service as gb


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


@router.get("/overview")
def overview() -> dict[str, Any]:
    return _handle(gb.overview)


@router.get("/stats")
def stats() -> dict[str, Any]:
    return _handle(gb.stats)


@router.get("/doctor")
def doctor() -> dict[str, Any]:
    return _handle(gb.doctor)


@router.post("/search")
def search(body: SearchBody) -> dict[str, Any]:
    return _handle(gb.search, body.query, body.mode)


@router.get("/page/{slug:path}")
def get_page(slug: str) -> dict[str, str]:
    return _handle(gb.get_page, slug)


@router.post("/page")
def get_page_post(body: PageBody) -> dict[str, str]:
    return _handle(gb.get_page, body.slug)


@router.get("/files")
def list_files() -> dict[str, Any]:
    return _handle(gb.list_files)


@router.get("/file")
def read_file(path: str = Query(..., min_length=1, max_length=240)) -> dict[str, Any]:
    return _handle(gb.read_file, path)


@router.put("/file")
def write_file(body: FileWriteBody, user: str = Depends(require_user)) -> dict[str, Any]:
    _ = user
    return _handle(gb.write_file, body.path, body.content)


@router.delete("/file")
def delete_file(path: str = Query(..., min_length=1, max_length=240), user: str = Depends(require_user)) -> dict[str, Any]:
    _ = user
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
    return _handle(bc.upload_knowledge, file.filename or "upload", data, category, title, import_after_save)


@router.get("/uploads")
def uploads(limit: int = Query(50, ge=1, le=100)) -> dict[str, Any]:
    return _handle(bc.list_uploads, limit)


@router.post("/ingest/text")
def ingest_text(body: IngestTextBody) -> dict[str, Any]:
    return _handle(bc.ingest_pasted_text, body.text, body.import_after_save)


@router.post("/ingest/url")
async def ingest_url(body: IngestUrlBody) -> dict[str, Any]:
    """Fetch URL, extract content via AI into clean Markdown, then ingest."""
    import httpx
    import re
    import subprocess

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(body.url, headers={"User-Agent": "Mozilla/5.0 (compatible; IvyeaOps/1.0)"})
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        raise HTTPException(400, f"抓取失败: {e}")

    # Basic HTML to text extraction
    html = re.sub(r"<(script|style|noscript|header|footer|nav)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "\n", html)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text).strip()

    if len(text) < 30:
        raise HTTPException(400, "页面内容为空或无法解析")

    # Truncate for AI processing
    raw_text = text[:12000]

    # Call AI to reformat into clean Markdown
    prompt = f"""你是一个内容整理专家。请将以下从网页抓取的原始文本整理成一篇干净、排版良好的 Markdown 文章。

要求：
1. 只保留文章正文内容，去除所有导航、广告、页脚、cookie提示等无关信息
2. 用合适的 Markdown 标题层级（#, ##, ###）组织内容结构
3. 保留关键信息，去除重复和冗余
4. 如有列表内容用 Markdown 列表格式
5. 直接输出 Markdown 内容，不要加任何解释或前言
6. 保持原文语言（中文内容用中文，英文内容用英文）

来源URL: {body.url}

原始文本：
{raw_text}"""

    markdown = ""
    errors = []

    # Route through Hermes CLI so we no longer depend on the retired
    # localhost:8000 kiro gateway.
    try:
        cmd = [
            bc._hermes_bin(),
            "chat",
            "-q",
            prompt,
            "-Q",
            "--source",
            "IvyeaOps-web-brain-url-ingest",
            "--max-turns",
            "1",
            "--toolsets",
            "",
            "--provider",
            "openai-codex",
            "-m",
            "gpt-5.4",
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(gb.BRAIN_ROOT),
            env=bc._hermes_env(),
            text=True,
            capture_output=True,
            timeout=180,
        )
        if proc.returncode == 0:
            markdown = bc._strip_hermes_output(proc.stdout)
        else:
            detail = (proc.stderr or proc.stdout or "").strip()[-1200:]
            errors.append(detail or "Hermes CLI 未知错误")
    except Exception as e:
        errors.append(str(e))

    if not markdown:
        raise HTTPException(502, f"AI整理失败: {'; '.join(errors)}")

    return _handle(bc.ingest_pasted_text, markdown, body.import_after_save)


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
def chat_message_send(session_id: str, body: ChatMessageBody) -> dict[str, Any]:
    return _handle(bc.send_message, session_id, body.content)


def _sse(evt: dict[str, Any]) -> str:
    return f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"


_STREAM_DEADLINE_S = int(__import__("os").environ.get("BRAIN_CHAT_HERMES_TIMEOUT", "180"))


@router.post("/chat/sessions/{session_id}/messages/stream")
async def chat_message_stream(session_id: str, body: ChatStreamBody):
    """Stream a hermes answer token-by-token over SSE. Emits:
    start{user_message,citations} → token{text}* → done{assistant_message} | error{detail}."""

    async def gen():
        try:
            turn = bc.begin_chat_turn(session_id, body.content, regenerate=body.regenerate, category=body.category)
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

        # Prefer Hermes (token-by-token) when its agent venv is present.
        if bc.hermes_available():
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

        # Fall back to the unified global text chain (DeepSeek → Apimart → 全局兜底
        # 大模型) when Hermes is absent or produced nothing — so the knowledge-base
        # chat works without any local agent CLI, and an empty Hermes reply still
        # yields an answer.
        if not "".join(parts).strip():
            try:
                async for chunk in bc.stream_global_answer(turn["prompt"]):
                    parts.append(chunk)
                    yield _sse({"type": "token", "text": chunk})
                timed_out = False
            except Exception as e:  # noqa: BLE001
                if not "".join(parts).strip():
                    yield _sse({"type": "error", "detail": f"对话失败（Hermes 不可用，全局兜底也失败）：{e}"})
                    return

        answer = "".join(parts)
        if not answer.strip():
            yield _sse({"type": "error", "detail": "未能生成回答：请安装 Hermes，或在「系统配置 → 全局兜底大模型」配置一个文本模型。"})
            return
        try:
            assistant = bc.commit_chat_answer(session_id, answer, turn["citations"])
        except (gb.GBrainError, bc.BrainChatError) as e:
            yield _sse({"type": "error", "detail": str(e)})
            return
        yield _sse({"type": "done", "assistant_message": assistant, "truncated": timed_out})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.delete("/chat/messages/{message_id}")
def chat_message_delete(message_id: str) -> dict[str, Any]:
    return _handle(bc.delete_message, message_id)
