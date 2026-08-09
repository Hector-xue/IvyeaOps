"""Amazon listing typography compositor.

The image model renders only the product, scene, light and composition.  This
module adds accurate, editable copy using an adaptive layout rather than the old
one-size-fits-all dark rounded sticker.
"""
from __future__ import annotations

import io
import logging
import os
import re
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

logger = logging.getLogger("ivyea.services.listing_typography")

_POS = {
    "top-left": (0.07, 0.08, "left", "top"),
    "top-center": (0.50, 0.08, "center", "top"),
    "top-right": (0.93, 0.08, "right", "top"),
    "center-left": (0.07, 0.50, "left", "center"),
    "center": (0.50, 0.50, "center", "center"),
    "center-right": (0.93, 0.50, "right", "center"),
    "bottom-left": (0.07, 0.92, "left", "bottom"),
    "bottom-center": (0.50, 0.92, "center", "bottom"),
    "bottom-right": (0.93, 0.92, "right", "bottom"),
}

_BOLD_FONTS = [
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
_REGULAR_FONTS = [
    "/usr/share/fonts/google-noto-cjk-fonts/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _font_path(bold: bool = True) -> Optional[str]:
    env = (os.getenv("IVYEA_OPS_CALLOUT_FONT") or "").strip()
    if env and Path(env).exists():
        return env
    for candidate in (_BOLD_FONTS if bold else _REGULAR_FONTS + _BOLD_FONTS):
        if Path(candidate).exists():
            return candidate
    return None


def _load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    path = _font_path(bold)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            logger.debug("ImageFont.truetype 失败（旁路，已忽略）", exc_info=True)
    return ImageFont.load_default()


def _hex(value: str) -> tuple[int, int, int]:
    value = (value or "#FFFFFF").lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except Exception:
        return (255, 255, 255)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont,
               max_width: int, max_lines: int = 3) -> list[str]:
    """Wrap Latin by words and CJK by glyphs; truncate instead of shrinking to noise."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    tokens = text.split(" ") if " " in text else list(text)
    joiner = " " if " " in text else ""
    lines: list[str] = []
    current = ""
    for token in tokens:
        trial = token if not current else current + joiner + token
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
            continue
        if current:
            lines.append(current)
        current = token
        if len(lines) == max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    consumed = joiner.join(lines)
    if len(consumed.replace(" ", "")) < len(text.replace(" ", "")) and lines:
        last = lines[-1]
        while last and draw.textlength(last + "…", font=font) > max_width:
            last = last[:-1].rstrip()
        lines[-1] = last + "…"
    return lines


_INTERNAL_COPY_RE = re.compile(
    r"\b(approved copy|approved title|product facts?|claim(?:s)?|image should|"
    r"do not fabricate|source material|evidence|supported by|supports? (?:the|this))\b",
    re.I,
)


def public_proof(value: str) -> str:
    """Accept only short shopper-facing facts, never internal review notes."""
    text = re.sub(r"\s+", " ", (value or "").strip())
    if not text or len(text) > 24 or _INTERNAL_COPY_RE.search(text):
        return ""
    # Proof is a compact factual token such as "8K/30fps", "120MP" or
    # "2 Batteries".  Long prose belongs in supporting_text.
    if not re.search(r"\d", text):
        return ""
    return text


def _region_box(width: int, height: int, position: str, max_width: float = .46) -> tuple[int, int, int, int]:
    xf, yf, align, vertical = _POS.get(position, _POS["top-left"])
    region_w = int(width * max_width)
    if align == "left":
        x0, x1 = int(xf * width), min(width, int(xf * width) + region_w)
    elif align == "right":
        x0, x1 = max(0, int(xf * width) - region_w), int(xf * width)
    else:
        x0, x1 = int((width - region_w) / 2), int((width + region_w) / 2)
    region_h = int(height * .31)
    if vertical == "top":
        y0, y1 = int(yf * height), int(yf * height) + region_h
    elif vertical == "bottom":
        y0, y1 = int(yf * height) - region_h, int(yf * height)
    else:
        y0, y1 = int(yf * height) - region_h // 2, int(yf * height) + region_h // 2
    return max(0, x0), max(0, y0), min(width, x1), min(height, y1)


def _foreground(im: Image.Image, box: tuple[int, int, int, int], theme: str,
                requested: str) -> tuple[tuple[int, int, int], bool]:
    if theme == "light":
        return (248, 249, 251), True
    if theme == "dark":
        return (22, 25, 29), False
    mean = ImageStat.Stat(im.crop(box).convert("L")).mean[0]
    if theme == "auto":
        return ((248, 249, 251), True) if mean < 132 else ((22, 25, 29), False)
    rgb = _hex(requested)
    return rgb, sum(rgb) > 420


def _add_scrim(im: Image.Image, box: tuple[int, int, int, int], light_text: bool,
               position: str, strength: int = 72) -> Image.Image:
    """A soft edge-to-transparent tonal correction, not a sticker behind text."""
    overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    x0, y0, x1, y1 = box
    base = (0, 0, 0) if light_text else (255, 255, 255)
    horizontal = position.endswith("left") or position.endswith("right")
    steps = max(24, (x1 - x0 if horizontal else y1 - y0) // 5)
    for i in range(steps):
        alpha = int(strength * (1 - i / steps) ** 1.7)
        if position.endswith("right"):
            xa = x1 - int((x1 - x0) * (i + 1) / steps)
            xb = x1 - int((x1 - x0) * i / steps)
            draw.rectangle((xa, y0, xb, y1), fill=(*base, alpha))
        elif horizontal:
            xa = x0 + int((x1 - x0) * i / steps)
            xb = x0 + int((x1 - x0) * (i + 1) / steps)
            draw.rectangle((xa, y0, xb, y1), fill=(*base, alpha))
        elif position.startswith("bottom"):
            ya = y1 - int((y1 - y0) * (i + 1) / steps)
            yb = y1 - int((y1 - y0) * i / steps)
            draw.rectangle((x0, ya, x1, yb), fill=(*base, alpha))
        else:
            ya = y0 + int((y1 - y0) * i / steps)
            yb = y0 + int((y1 - y0) * (i + 1) / steps)
            draw.rectangle((x0, ya, x1, yb), fill=(*base, alpha))
    # Feather all four edges.  Without this blur even a translucent gradient can
    # read as a rectangular text sticker on smooth backgrounds.
    overlay = overlay.filter(ImageFilter.GaussianBlur(radius=max(16, int(min(im.size) * .024))))
    return Image.alpha_composite(im, overlay)


def _measure_lines(draw: ImageDraw.ImageDraw, lines: list[str], font: ImageFont.ImageFont,
                   spacing: int) -> tuple[int, int]:
    widths = [int(draw.textlength(line, font=font)) for line in lines]
    bbox = draw.textbbox((0, 0), "Ag", font=font)
    line_h = max(1, bbox[3] - bbox[1]) + spacing
    return max(widths, default=0), line_h * len(lines)


def _draw_lines(draw: ImageDraw.ImageDraw, lines: list[str], font: ImageFont.ImageFont,
                x: float, y: float, width: int, align: str,
                fill: tuple[int, int, int], spacing: int) -> float:
    bbox = draw.textbbox((0, 0), "Ag", font=font)
    line_h = max(1, bbox[3] - bbox[1]) + spacing
    shadow = (0, 0, 0, 46) if sum(fill) > 420 else (255, 255, 255, 38)
    for line in lines:
        line_w = draw.textlength(line, font=font)
        lx = x if align == "left" else (x + width - line_w if align == "right" else x + (width - line_w) / 2)
        draw.text((lx + 1, y + 1), line, font=font, fill=shadow)
        draw.text((lx, y), line, font=font, fill=(*fill, 255))
        y += line_h
    return y


def _render_editorial_block(im: Image.Image, *, headline: str, supporting_text: str,
                            eyebrow: str, proof: str, position: str, color: str,
                            accent_color: str, theme: str, layout_style: str) -> Image.Image:
    width, height = im.size
    side_zone = position.endswith("left") or position.endswith("right")
    region_width = .46 if side_zone else .62
    box = _region_box(width, height, position, region_width)
    fill, light_text = _foreground(im, box, theme, color)
    if layout_style != "minimal":
        strength = 72 if layout_style == "split" else (50 if layout_style == "editorial" else 62)
        im = _add_scrim(im, box, light_text, position, strength)

    draw = ImageDraw.Draw(im)
    x0, y0, x1, y1 = box
    region_w = x1 - x0
    align = _POS.get(position, _POS["top-left"])[2]
    headline_font = _load_font(max(28, int(height * .052)), True)
    body_font = _load_font(max(18, int(height * .022)), False)
    eyebrow_font = _load_font(max(14, int(height * .016)), True)
    proof_font = _load_font(max(24, int(height * .034)), True)
    headline_lines = _wrap_text(draw, headline, headline_font, region_w, 2)
    body_lines = _wrap_text(draw, supporting_text, body_font, region_w, 2)
    eyebrow_lines = _wrap_text(draw, eyebrow.upper(), eyebrow_font, region_w, 1)
    proof_lines = _wrap_text(draw, public_proof(proof), proof_font, region_w, 1)
    blocks = [
        (eyebrow_lines, eyebrow_font, int(height * .005)),
        (headline_lines, headline_font, int(height * .009)),
        (proof_lines, proof_font, int(height * .006)),
        (body_lines, body_font, int(height * .004)),
    ]
    heights = [_measure_lines(draw, lines, font, spacing)[1] for lines, font, spacing in blocks]
    gaps = int(height * .010) * max(0, sum(bool(lines) for lines, _, _ in blocks) - 1)
    total_h = sum(heights) + gaps
    vertical = _POS.get(position, _POS["top-left"])[3]
    if vertical == "bottom":
        y = y1 - total_h
    elif vertical == "center":
        y = y0 + max(0, (y1 - y0 - total_h) / 2)
    else:
        y = y0
    accent = _hex(accent_color)
    for idx, ((lines, font, spacing), block_h) in enumerate(zip(blocks, heights)):
        if not lines:
            continue
        block_fill = accent if idx in {0, 2} else fill
        y = _draw_lines(draw, lines, font, x0, y, region_w, align, block_fill, spacing)
        y += int(height * .010)
    if layout_style in {"split", "proof"}:
        line_w = min(region_w, int(width * .12))
        ly = max(y0, int(y0 - height * .018))
        lx = x0 if align != "right" else x1 - line_w
        draw.rounded_rectangle((lx, ly, lx + line_w, ly + max(3, int(height * .004))),
                               radius=3, fill=(*accent, 255))
    return im


def overlay_callout(img_bytes: bytes, callout: str = "", position: str = "bottom-center", *,
                    headline: str = "", color: str = "#FFFFFF", plate: str = "#101418",
                    supporting_text: str = "", eyebrow: str = "", proof: str = "",
                    layout_style: str = "editorial", accent_color: str = "#4F8CFF",
                    theme: str = "auto") -> bytes:
    """Compose accurate marketing copy using a real layout system.

    ``plate`` remains in the signature for compatibility but is intentionally not
    used: the previous rounded plate was the visual defect this compositor replaces.
    """
    del plate
    headline = re.sub(r"\s+", " ", (headline or "").strip())[:60]
    supporting_text = re.sub(r"\s+", " ", (supporting_text or "").strip())[:100]
    eyebrow = re.sub(r"\s+", " ", (eyebrow or "").strip())[:28]
    proof = public_proof(proof)
    callout = re.sub(r"\s+", " ", (callout or "").strip())[:100]
    values = [callout, headline, supporting_text, eyebrow, proof]
    if not any((value or "").strip() for value in values):
        return img_bytes
    im = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    position = position if position in _POS else "top-left"
    layout_style = layout_style if layout_style in {"editorial", "minimal", "split", "proof", "grid"} else "editorial"

    # New studio copy stays in one deliberate text zone.  The old headline+
    # callout API keeps its two-zone behavior so saved projects remain readable.
    legacy_two_zone = bool(headline and callout and not supporting_text and not eyebrow and not proof)
    if legacy_two_zone:
        im = _render_editorial_block(
            im, headline=headline, supporting_text="", eyebrow="", proof="",
            position="top-center", color=color, accent_color=accent_color,
            theme=theme, layout_style=layout_style,
        )
        im = _render_editorial_block(
            im, headline=callout, supporting_text="", eyebrow="", proof="",
            position=position if position.startswith("bottom") else "bottom-center",
            color=color, accent_color=accent_color, theme=theme, layout_style="minimal",
        )
    else:
        body = supporting_text or (callout if headline else "")
        title = headline or (callout if not headline else "")
        im = _render_editorial_block(
            im, headline=title, supporting_text=body, eyebrow=eyebrow, proof=proof,
            position=position, color=color, accent_color=accent_color,
            theme=theme, layout_style=layout_style,
        )
    output = io.BytesIO()
    im.convert("RGB").save(output, format="PNG", compress_level=6)
    return output.getvalue()
