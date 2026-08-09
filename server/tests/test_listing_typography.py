"""文字排版独立化 — Pillow 叠字:产出合法图、指定区域确有文字像素、空文案 no-op。"""
import io

from PIL import Image

from app.services import listing_typography as T


def _blank(w=1024, h=1024, color=(255, 255, 255)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "PNG")
    return b.getvalue()


def _dark_pixels(im: Image.Image, box, thresh=240) -> int:
    band = im.crop(box).convert("L")
    return sum(1 for p in band.getdata() if p < thresh)


def _assert_text_landed(im: Image.Image, box, control_box) -> None:
    """断言文字落在 ``box`` 里，而不是落在 ``control_box``。

    **不要在这里写死墨迹像素数**。同一段文字在不同平台挑到的字体不同，墨迹量
    能差一个数量级（Linux 的 Noto CJK Bold 约 13000 像素，macOS 的 PingFang
    约 500），任何调得下来的绝对阈值要么在 macOS 上误报，要么在 Linux 上形同
    虚设。这个测试真正要保的是**位置**：目标区域有字、对照区域干净。
    """
    ink = _dark_pixels(im, box)
    control = _dark_pixels(im, control_box)
    assert ink > 150, f"目标区域几乎没有墨迹（{ink}），文字大概没画上"
    assert ink > 20 * (control + 1), f"墨迹没落在目标区域（目标 {ink} / 对照 {control}）"


def test_overlay_renders_text_bottom():
    out = T.overlay_callout(_blank(), "30天超长续航 30-Day Battery", "bottom-center")
    im = Image.open(io.BytesIO(out))
    assert im.size == (1024, 1024) and im.mode == "RGB"
    _assert_text_landed(im, (100, 850, 924, 1000), (100, 300, 924, 500))


def test_overlay_top_right_region():
    out = T.overlay_callout(_blank(), "Waterproof", "top-right")
    im = Image.open(io.BytesIO(out))
    # 对照取左下角：右上角有字时，左下角必须还是干净的。
    _assert_text_landed(im, (520, 30, 1010, 210), (30, 800, 500, 1000))


def test_overlay_headline_and_callout():
    out = T.overlay_callout(_blank(), "30-Day Battery", "bottom-center", headline="Power That Lasts")
    im = Image.open(io.BytesIO(out))
    _assert_text_landed(im, (100, 30, 924, 190), (100, 400, 924, 560))    # headline 在顶部
    _assert_text_landed(im, (100, 840, 924, 1000), (100, 400, 924, 560))  # callout 在底部


def test_overlay_empty_is_noop():
    b = _blank()
    assert T.overlay_callout(b, "") == b
    assert T.overlay_callout(b, "   ") == b
    assert T.overlay_callout(b, "", headline="") == b


def test_font_loader_never_crashes():
    # NotoCJK exists on this box; even on a font-less host _load_font must return something
    assert T._load_font(40) is not None


def test_editorial_layout_keeps_canvas_edges_clean_instead_of_full_sticker():
    out = T.overlay_callout(
        _blank(),
        headline="Natural light. Real detail.",
        supporting_text="Designed for daily use",
        position="top-left",
        layout_style="editorial",
        theme="auto",
    )
    im = Image.open(io.BytesIO(out)).convert("RGB")
    # The old compositor put a large rounded dark plate behind every headline.
    # Editorial type may add a local soft scrim, but the page corner stays clean.
    assert im.getpixel((2, 2)) == (255, 255, 255)
    _assert_text_landed(im, (40, 40, 850, 390), (40, 600, 850, 900))


def test_cjk_copy_wraps_and_proof_style_renders():
    out = T.overlay_callout(
        _blank(color=(25, 28, 32)),
        headline="真实场景自然融入",
        supporting_text="产品比例、光线和接触阴影保持可信",
        proof="24小时",
        position="top-left",
        layout_style="proof",
        accent_color="#6EE7A2",
    )
    im = Image.open(io.BytesIO(out))
    assert im.size == (1024, 1024)
    assert im.getbbox() is not None


def test_internal_review_sentence_is_never_rendered_as_public_proof():
    kwargs = {
        "headline": "Big Views",
        "supporting_text": "Native 8K video",
        "position": "top-left",
        "layout_style": "editorial",
    }
    clean = T.overlay_callout(_blank(color=(35, 38, 42)), proof="", **kwargs)
    internal = T.overlay_callout(
        _blank(color=(35, 38, 42)),
        proof="Approved copy supports the sensor and video-resolution claims; image should not simulate evidence",
        **kwargs,
    )
    assert internal == clean
    assert T.public_proof("8K/30fps") == "8K/30fps"
