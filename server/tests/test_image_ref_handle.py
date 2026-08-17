"""附图引用句柄：任务台的图生图靠它。

为什么要有这一层：任务台里用户贴一张图说"把它改成夜景"，agent 得把这张图当原图
传给 image_generate。data URL 有几百 KB —— 让它穿过模型的工具调用参数是不可能的。
所以图不进模型：ops 先把它落盘换成 `ivyea-ref://<id>`，agent 只经手这串短字符，
服务端再凭句柄把原图取回来。
"""
from __future__ import annotations

import asyncio
import base64

import pytest
from fastapi import HTTPException

from app.routers import assistant

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfake-bytes").decode()
DATA_URL = f"data:image/png;base64,{PNG}"


@pytest.fixture(autouse=True)
def _tmp_refs(tmp_path, monkeypatch):
    """句柄落在临时目录 —— 测试绝不碰用户真实的 ~/.hermes 目录。"""
    monkeypatch.setattr(assistant, "_REFS_DIR", tmp_path / "imagegen-refs")


def test_handle_round_trips_back_to_the_original_bytes():
    ref = assistant.image_ref(assistant.ImageRefReq(data_url=DATA_URL), _user="t")["ref"]
    assert ref.startswith("ivyea-ref://")

    raw, mime = asyncio.run(assistant._source_to_bytes(ref))
    assert raw == base64.b64decode(PNG)
    assert mime == "image/png"


def test_jpeg_keeps_its_mime_through_the_handle():
    url = "data:image/jpeg;base64," + base64.b64encode(b"jpeg-bytes").decode()
    ref = assistant.image_ref(assistant.ImageRefReq(data_url=url), _user="t")["ref"]

    raw, mime = asyncio.run(assistant._source_to_bytes(ref))
    assert raw == b"jpeg-bytes"
    assert mime == "image/jpeg"          # 不能退化成 image/jpg，上游按标准 mime 收


def test_data_and_http_sources_still_work():
    """句柄是新增的第三条路，原来两条不能被它挤掉。"""
    raw, mime = asyncio.run(assistant._source_to_bytes(DATA_URL))
    assert raw == base64.b64decode(PNG) and mime == "image/png"


def test_expired_handle_says_so_instead_of_silently_generating_a_new_image():
    """句柄失效必须报错。**不能退化成文生图** —— 用户要的是"改我这张图"，
    悄悄给一张全新的图比报错更糟。"""
    with pytest.raises(ValueError, match="附图引用"):
        asyncio.run(assistant._source_to_bytes("ivyea-ref://" + "0" * 16))


@pytest.mark.parametrize("bad", [
    "../../etc/passwd",           # 路径穿越
    "abc/../../x",
    "ZZZZ",                       # 非 hex
    "",
])
def test_only_our_own_hex_ids_resolve(bad):
    assert assistant._ref_file(bad) is None


def test_non_image_payload_is_refused():
    with pytest.raises(HTTPException) as e:
        assistant.image_ref(assistant.ImageRefReq(data_url="data:text/html;base64,PGI+"), _user="t")
    assert e.value.status_code == 400


def test_oversized_image_is_refused_before_it_hits_the_disk(monkeypatch):
    monkeypatch.setattr(assistant, "_MAX_REF_BYTES", 8)
    with pytest.raises(HTTPException) as e:
        assistant.image_ref(assistant.ImageRefReq(data_url=DATA_URL), _user="t")
    assert e.value.status_code == 413
    assert not assistant._REFS_DIR.exists() or not list(assistant._REFS_DIR.glob("*"))


def test_old_handles_are_pruned_so_the_dir_cannot_grow_without_bound(monkeypatch):
    monkeypatch.setattr(assistant, "_KEEP_REFS", 3)
    refs = [assistant.image_ref(assistant.ImageRefReq(data_url=DATA_URL), _user="t")["ref"]
            for _ in range(6)]
    assert len(list(assistant._REFS_DIR.glob("*.png"))) == 3
    # 留下的是最近的三张 —— 刚贴的图必须还在
    raw, _ = asyncio.run(assistant._source_to_bytes(refs[-1]))
    assert raw == base64.b64decode(PNG)


def test_image_generate_tool_accepts_a_handle(monkeypatch):
    """agent 侧的入口：句柄要能一路走到 image_submit 的 image_urls。"""
    from app.services import ivyea_ops_tools

    seen: dict = {}

    async def _fake_submit(req):
        seen["urls"] = req.image_urls
        return {"task_id": "edit_x"}

    monkeypatch.setattr(assistant, "image_submit", _fake_submit)
    out = asyncio.run(ivyea_ops_tools._image_generate(
        {"prompt": "改成夜景", "image_urls": ["ivyea-ref://deadbeef"]}))
    assert seen["urls"] == ["ivyea-ref://deadbeef"]
    assert out["task_id"] == "edit_x"
