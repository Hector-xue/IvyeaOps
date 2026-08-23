"""成图 PSD 导出接口：真的吐出一个能被 PSD 解析器读回来的分层文件。"""
import asyncio
import json
import time
from io import BytesIO

import pytest
from fastapi import HTTPException
from PIL import Image

from app.routers.listing import export as E
from app.routers.listing.common import _db

PID = "psdtest01"


def _png(size, colour) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, colour).save(buf, "PNG")
    return buf.getvalue()


def _seed(images: list[dict]) -> None:
    now = time.time()
    conn = _db()
    conn.execute("DELETE FROM listing_projects WHERE id = ?", (PID,))
    conn.execute(
        "INSERT INTO listing_projects (id,asin,marketplace,status,creative_sets,created_at,updated_at) "
        "VALUES (?,?,?,'planned',?,?,?)",
        (PID, "B0TEST1234", "US", json.dumps({"gallery": {"images": images}}), now, now),
    )
    conn.commit()
    conn.close()


def _cleanup() -> None:
    conn = _db()
    conn.execute("DELETE FROM listing_projects WHERE id = ?", (PID,))
    conn.commit()
    conn.close()


@pytest.fixture()
def fake_images(monkeypatch):
    async def _fetch(_client, url: str) -> bytes:
        if "source" in url:
            return _png((200, 200), (255, 255, 255))
        return _png((320, 240), (20, 60, 120))
    monkeypatch.setattr(E, "_fetch_image_bytes", _fetch)
    yield
    _cleanup()


def test_psd_download_has_both_layers(fake_images):
    _seed([{"slot": "main", "role": "主图", "final_url": "/api/image-translate/images/final.png",
            "product_source_url": "/api/image-translate/images/source.png"}])
    resp = asyncio.run(E.download_psd(PID, deliverable="gallery", slot="main", index=-1,
                                      include_source=True, _user="t"))
    assert resp.media_type == "image/vnd.adobe.photoshop"
    assert ".psd" in resp.headers["content-disposition"]
    assert "B0TEST1234" in resp.headers["content-disposition"]

    im = Image.open(BytesIO(resp.body))
    assert im.format == "PSD" and im.size == (320, 240)
    names = [layer[0] for layer in im.layers]
    assert names == ["final-1", "product-source"]
    # 产品真值层默认隐藏 → 合并图仍是成图本身
    assert im.convert("RGB").getpixel((160, 120)) == (20, 60, 120)
    # 白底图等比缩放后居中
    assert im.layers[1][2] == (60, 20, 260, 220)


def test_psd_without_source_layer(fake_images):
    _seed([{"slot": "main", "role": "主图", "final_url": "/api/image-translate/images/final.png",
            "product_source_url": "/api/image-translate/images/source.png"}])
    resp = asyncio.run(E.download_psd(PID, deliverable="gallery", slot="main", index=-1,
                                      include_source=False, _user="t"))
    assert len(Image.open(BytesIO(resp.body)).layers) == 1


def test_psd_rejects_unrendered_shot(fake_images):
    _seed([{"slot": "main", "role": "主图", "final_url": ""}])
    with pytest.raises(HTTPException) as err:
        asyncio.run(E.download_psd(PID, deliverable="gallery", slot="main", index=-1,
                                   include_source=True, _user="t"))
    assert err.value.status_code == 400


def test_psd_unknown_slot_is_404(fake_images):
    _seed([{"slot": "main", "role": "主图", "final_url": "/api/image-translate/images/final.png"}])
    with pytest.raises(HTTPException) as err:
        asyncio.run(E.download_psd(PID, deliverable="gallery", slot="nope", index=-1,
                                   include_source=True, _user="t"))
    assert err.value.status_code == 404
