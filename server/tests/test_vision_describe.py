"""贴图那条链的服务端契约。

这个端点是**替 agent 挡刀**的：agent serve 在主脑不支持视觉时直接抛错，而本机主脑
恰好没有视觉能力，所以图片走 payload.images 必然失败。ops 这边把图读成文字再带进
那一轮 —— 换句话说，这个端点坏了，任务台的贴图就是坏的，而且是静默的。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.routers import ivyea_agent as mod


PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="


def _stream(monkeypatch, frames):
    async def fake(prompt, images):
        for f in frames:
            yield f

    monkeypatch.setattr(mod, "_vision_stream", fake, raising=False)
    from app.services import ai_synthesis_service
    monkeypatch.setattr(ai_synthesis_service, "stream_vision", fake)


@pytest.mark.anyio
async def test_reads_the_image_into_text(monkeypatch):
    _stream(monkeypatch, [("siliconflow", "ACOS "), ("siliconflow", "42.7%")])
    out = await mod.vision_describe(mod.VisionDescribeBody(images=[PNG]), _user="admin")
    assert out["text"] == "ACOS 42.7%" and out["provider"] == "siliconflow"


def test_empty_list_is_rejected_before_the_handler():
    """空列表在 pydantic 那一层就该被挡住 —— 这是更早的一道防线。"""
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        mod.VisionDescribeBody(images=[])


@pytest.mark.anyio
async def test_rejects_things_that_are_not_images(monkeypatch):
    """前端只该送 data:image/...。放行别的等于让人把任意 URL 喂给视觉模型。"""
    for bad in (["https://example.com/a.png"], ["data:text/html,<b>x"], ["not a uri"]):
        with pytest.raises(HTTPException) as e:
            await mod.vision_describe(mod.VisionDescribeBody(images=bad), _user="admin")
        assert e.value.status_code == 400


@pytest.mark.anyio
async def test_rejects_oversized_images(monkeypatch):
    huge = "data:image/png;base64," + "A" * (8 * 1024 * 1024)
    with pytest.raises(HTTPException) as e:
        await mod.vision_describe(mod.VisionDescribeBody(images=[huge]), _user="admin")
    assert e.value.status_code == 413


@pytest.mark.anyio
async def test_no_vision_model_says_so_instead_of_returning_empty(monkeypatch):
    """**不能静默成功**。返回空字符串的话，那一轮会带着"图片内容：（空）"跑下去，
    用户看到的是 Agent 答非所问，而不是"你没配视觉模型"。"""
    _stream(monkeypatch, [("error", "未配置视觉模型")])
    with pytest.raises(HTTPException) as e:
        await mod.vision_describe(mod.VisionDescribeBody(images=[PNG]), _user="admin")
    assert e.value.status_code == 503 and "视觉模型" in str(e.value.detail)


@pytest.mark.anyio
async def test_empty_output_is_also_an_error(monkeypatch):
    _stream(monkeypatch, [("siliconflow", "   ")])
    with pytest.raises(HTTPException) as e:
        await mod.vision_describe(mod.VisionDescribeBody(images=[PNG]), _user="admin")
    assert e.value.status_code == 503
