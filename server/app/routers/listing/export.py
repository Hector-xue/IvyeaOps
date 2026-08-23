"""成图导出：把一张已生成的套图/A+ 图打成 PSD 下载。

为什么要有它：成品图交给设计或翻译时，对方要的是能进 Photoshop 的分层文件，而不是
一张 PNG。画面里的英文是图片模型直接写进像素的（见 visuals 的编译逻辑），我们没有
"文字层"可以还原 —— 所以这里给的是**诚实的两层**：

  1. 成图本身（可见）
  2. 产品真值白底图（隐藏、居中放置）—— 换产品、抠图重排时直接可用

再多的层就得靠 AI 猜了，那不是分层，是编造。
"""
from __future__ import annotations

import asyncio
import logging
import re
from io import BytesIO
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from app.core.security import require_user

from .common import _fetch_image_bytes, project_row
from .images import _load_plan

logger = logging.getLogger("ivyea.routers.listing.export")

router = APIRouter()

_PSD_MIME = "image/vnd.adobe.photoshop"


def _safe_stem(value: str, fallback: str) -> str:
    """文件名用：去掉路径分隔符和 Windows 非法字符，保留中文。"""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "-", str(value or "")).strip(" .-")
    return cleaned[:60] or fallback


def _content_disposition(filename: str) -> str:
    """中文文件名走 RFC 5987；同时给一个 ASCII 兜底名，老浏览器不至于拿到乱码。"""
    ascii_name = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9._-]+", "_", filename)).strip("_") or "listing.psd"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def _pick(images: list[dict], slot: str, index: int) -> tuple[int, dict]:
    if slot:
        for i, item in enumerate(images):
            if str(item.get("slot") or "") == slot:
                return i, item
        raise HTTPException(404, f"没找到分镜 {slot}")
    if 0 <= index < len(images):
        return index, images[index]
    raise HTTPException(400, "请指定要导出的分镜（slot 或 index）")


@router.get("/projects/{project_id}/psd")
async def download_psd(
    project_id: str,
    deliverable: str = Query("gallery"),
    slot: str = Query(""),
    index: int = Query(-1),
    include_source: bool = Query(True, description="是否附带隐藏的产品真值图层"),
    _user: str = Depends(require_user),
):
    """把一张成图打成 PSD 返回（成图层 + 可选的隐藏产品真值层）。"""
    from PIL import Image
    from app.services.psd_writer import PsdLayer, build_psd

    row = project_row(project_id, "asin")
    if not row:
        raise HTTPException(404, "project not found")
    deliverable = "aplus" if deliverable == "aplus" else "gallery"
    images = _load_plan(project_id, deliverable).get("images") or []
    idx, image = _pick(images, slot.strip(), index)

    final_url = str(image.get("final_url") or "")
    if not final_url:
        raise HTTPException(400, "这张还没有成图，先渲染再导出 PSD")

    async with httpx.AsyncClient(timeout=60) as client:
        raw = await _fetch_image_bytes(client, final_url)
        source_raw = b""
        if include_source:
            src_url = str(image.get("product_source_url") or "")
            if src_url:
                try:
                    source_raw = await _fetch_image_bytes(client, src_url)
                except Exception:  # noqa: BLE001 —— 附赠图层拿不到不该拖垮下载
                    logger.debug("产品真值图取不到（旁路，已忽略）", exc_info=True)

    try:
        base = Image.open(BytesIO(raw)).convert("RGBA")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"成图无法解码：{exc}") from exc

    role = str(image.get("role") or f"第{idx + 1}张")
    layers = [PsdLayer(f"成图 · {role}", base, ascii=f"final-{idx + 1}")]

    if source_raw:
        try:
            src = Image.open(BytesIO(source_raw)).convert("RGBA")
            # 等比缩到画布内（白底原图常常比成图大），再居中放置
            src.thumbnail(base.size, Image.LANCZOS)
            offset = ((base.width - src.width) // 2, (base.height - src.height) // 2)
            layers.append(PsdLayer("产品真值（白底，默认隐藏）", src, offset=offset,
                                   visible=False, ascii="product-source"))
        except Exception:  # noqa: BLE001
            logger.debug("产品真值图无法解码（旁路，已忽略）", exc_info=True)

    # 纯 Python 的 RLE 压一张 1600×1600 要两三秒 —— 丢进线程，别卡住事件循环。
    psd = await asyncio.to_thread(build_psd, base.size, layers)

    name = (f"{_safe_stem(row['asin'], 'listing')}_"
            f"{'aplus' if deliverable == 'aplus' else 'gallery'}_"
            f"{idx + 1:02d}_{_safe_stem(role, 'shot')}.psd")
    return Response(content=psd, media_type=_PSD_MIME, headers={
        "Content-Disposition": _content_disposition(name),
        "Content-Length": str(len(psd)),
        "Cache-Control": "no-store",
    })
