"""PSD 写入器的真实回读校验：写出去的字节能不能被 PSD 解析器读回来。

用 Pillow 的 PsdImagePlugin 当第三方读者（它只读不写，和被测代码没有共享实现），
校验合并图、图层框、PackBits 解码和 alpha 混合。
"""
from io import BytesIO

import pytest
from PIL import Image

from app.services.psd_writer import PsdLayer, build_psd


def _open(psd: bytes) -> Image.Image:
    return Image.open(BytesIO(psd))


def test_header_and_merged_image_round_trip():
    bg = Image.new("RGBA", (64, 48), (10, 20, 30, 255))
    im = _open(build_psd((64, 48), [PsdLayer("成图", bg, ascii="final")]))
    assert im.format == "PSD"
    assert im.size == (64, 48)
    assert im.mode == "RGB"
    assert im.convert("RGB").getpixel((5, 5)) == (10, 20, 30)


def test_layers_keep_name_and_bbox():
    bg = Image.new("RGBA", (100, 60), (255, 255, 255, 255))
    fg = Image.new("RGBA", (30, 20), (200, 0, 0, 255))
    psd = build_psd((100, 60), [
        PsdLayer("成图", bg, ascii="final"),
        PsdLayer("产品真值", fg, offset=(10, 8), ascii="product-source"),
    ])
    layers = _open(psd).layers
    assert [layer[0] for layer in layers] == ["final", "product-source"]
    assert layers[0][2] == (0, 0, 100, 60)
    assert layers[1][2] == (10, 8, 40, 28)


def test_hidden_layer_is_excluded_from_merged_image():
    bg = Image.new("RGBA", (40, 40), (0, 0, 255, 255))
    fg = Image.new("RGBA", (20, 20), (255, 255, 0, 255))
    psd = build_psd((40, 40), [
        PsdLayer("成图", bg, ascii="final"),
        PsdLayer("隐藏层", fg, offset=(10, 10), visible=False, ascii="hidden"),
    ])
    im = _open(psd)
    assert len(im.layers) == 2                                   # 图层还在文件里
    assert im.convert("RGB").getpixel((15, 15)) == (0, 0, 255)   # 但不进合并图


def test_alpha_is_composited_into_merged_image():
    bg = Image.new("RGBA", (40, 40), (12, 90, 200, 255))
    fg = Image.new("RGBA", (20, 20), (255, 210, 0, 128))
    im = _open(build_psd((40, 40), [PsdLayer("底", bg, ascii="bg"),
                                    PsdLayer("半透明", fg, offset=(10, 10), ascii="fg")]))
    assert im.convert("RGB").getpixel((15, 15)) == (134, 150, 100)
    assert im.convert("RGB").getpixel((1, 1)) == (12, 90, 200)


def test_photo_like_content_survives_rle():
    """噪声图最容易踩 PackBits 的边界（无重复串 + 128 字节切段）。"""
    import random
    random.seed(7)
    src = Image.new("RGB", (77, 53))
    src.putdata([(random.randrange(256), random.randrange(256), random.randrange(256))
                 for _ in range(77 * 53)])
    im = _open(build_psd((77, 53), [PsdLayer("noise", src.convert("RGBA"), ascii="noise")]))
    assert list(im.convert("RGB").getdata()) == list(src.getdata())


def test_rejects_empty_and_oversized_canvas():
    one = PsdLayer("x", Image.new("RGBA", (2, 2)), ascii="x")
    with pytest.raises(ValueError):
        build_psd((0, 10), [one])
    with pytest.raises(ValueError):
        build_psd((10, 10), [])
    with pytest.raises(ValueError):
        build_psd((40_000, 10), [one])
