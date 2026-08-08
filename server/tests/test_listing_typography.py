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


def test_overlay_renders_text_bottom():
    out = T.overlay_callout(_blank(), "30天超长续航 30-Day Battery", "bottom-center")
    im = Image.open(io.BytesIO(out))
    assert im.size == (1024, 1024) and im.mode == "RGB"
    assert _dark_pixels(im, (100, 850, 924, 1000)) > 500     # plate + glyphs present


def test_overlay_top_right_region():
    out = T.overlay_callout(_blank(), "Waterproof", "top-right")
    im = Image.open(io.BytesIO(out))
    assert _dark_pixels(im, (520, 30, 1010, 210)) > 300


def test_overlay_headline_and_callout():
    out = T.overlay_callout(_blank(), "30-Day Battery", "bottom-center", headline="Power That Lasts")
    im = Image.open(io.BytesIO(out))
    assert _dark_pixels(im, (100, 30, 924, 190)) > 500      # headline at top
    assert _dark_pixels(im, (100, 840, 924, 1000)) > 500    # callout at bottom


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
    assert _dark_pixels(im, (40, 40, 850, 390)) > 300


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
