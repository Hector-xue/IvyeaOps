"""最小可用的 PSD（Photoshop 文档）写入器 —— 纯 Python，不引入新依赖。

为什么自己写：Listing 成图要给用户一个能直接拖进 Photoshop 的分层文件，而 Pillow
只能读 PSD 不能写；能写 PSD 的第三方库（pytoshop 之类）为一个下载按钮拉一整个
依赖不划算，且要跟着 IvyeaOps 一起打进 Windows 的 PyInstaller 包。PSD 的
"8BPS" 结构本身很直白，够用的一版就在这里。

产出规格：8 bit / 通道、RGB 色彩模式、每图层带 alpha，图层与合并图都用 PackBits
(RLE) 压缩 —— 与 Photoshop 自己写的文件同一种压缩，体积约为裸 RGB 的一半。

图层名写两处：
  * 图层记录里的 Pascal 短名（只能放单字节字符，非 ASCII 会退化）
  * 'luni' 附加块里的 UTF-16 名字 —— Photoshop 实际显示的是这个，所以中文图层名
    要靠它。只写 Pascal 名的话，PS 里会看到一串乱码。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from io import BytesIO
from typing import Sequence

from PIL import Image

__all__ = ["PsdLayer", "build_psd"]

_MAX_SIDE = 30_000  # PSD 上限；再大要用 PSB，那是另一种格式


@dataclass
class PsdLayer:
    """一个 PSD 图层。image 会被转成 RGBA；offset 是它在画布上的左上角。"""
    name: str
    image: Image.Image
    offset: tuple[int, int] = (0, 0)
    visible: bool = True
    opacity: int = 255
    ascii: str = ""      # Pascal 短名；留空则从 name 里挑 ASCII

    @property
    def ascii_name(self) -> str:
        """Pascal 名只能放单字节字符。中文名在这里会被剔空，所以调用方最好显式给一个
        英文短名 —— Photoshop 显示 luni 的中文名，但只读 Pascal 名的工具（Pillow、
        部分预览器）看到的是这个。"""
        source = self.ascii or self.name
        s = "".join(c for c in source if 32 <= ord(c) < 127).strip()
        return s or "layer"


def _packbits(data: bytes) -> bytes:
    """PackBits (RLE) 编码 —— PSD 的 compression=1 用的就是它。"""
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        run_end = i + 1
        while run_end < n and data[run_end] == data[i] and run_end - i < 128:
            run_end += 1
        run = run_end - i
        if run >= 2:                       # 重复串：-(run-1) + 那个字节
            out.append(257 - run)
            out.append(data[i])
            i = run_end
            continue
        start = i                          # 直出串：一直到出现 3 个连续相同字节
        i += 1
        while i < n and i - start < 128:
            if i + 2 < n and data[i] == data[i + 1] == data[i + 2]:
                break
            i += 1
        lit = data[start:i]
        out.append(len(lit) - 1)
        out += lit
    return bytes(out)


def _rle_plane(plane: bytes, width: int, height: int) -> tuple[bytes, bytes]:
    """把一个通道逐行 PackBits，返回 (每行字节数表, 压缩数据)。"""
    counts = bytearray()
    body = bytearray()
    for y in range(height):
        row = _packbits(plane[y * width:(y + 1) * width])
        counts += struct.pack(">H", len(row))
        body += row
    return bytes(counts), bytes(body)


def _pascal4(name: str) -> bytes:
    """Pascal 短名，总长补齐到 4 的倍数（图层记录里的规矩）。"""
    raw = name.encode("ascii", "replace")[:255]
    blob = bytes([len(raw)]) + raw
    return blob + b"\x00" * (-len(blob) % 4)


def _luni(name: str) -> bytes:
    """'luni' 附加块：Photoshop 真正显示的 Unicode 图层名。"""
    text = name.encode("utf-16-be")
    data = struct.pack(">I", len(name)) + text
    data += b"\x00" * (len(data) % 2)
    return b"8BIM" + b"luni" + struct.pack(">I", len(data)) + data


def _flatten(canvas: tuple[int, int], layers: Sequence[PsdLayer]) -> Image.Image:
    """合并图（PS 之外的软件、缩略图、预览都只看它）。"""
    merged = Image.new("RGB", canvas, (255, 255, 255))
    for layer in layers:
        if not layer.visible:
            continue
        rgba = layer.image.convert("RGBA")
        merged.paste(rgba, layer.offset, rgba)
    return merged


def build_psd(canvas: tuple[int, int], layers: Sequence[PsdLayer]) -> bytes:
    """按画布尺寸与图层列表（自下而上）生成 PSD 字节流。"""
    width, height = canvas
    if width <= 0 or height <= 0:
        raise ValueError("PSD 画布尺寸非法")
    if width > _MAX_SIDE or height > _MAX_SIDE:
        raise ValueError(f"PSD 单边最大 {_MAX_SIDE}px，当前 {width}x{height}")
    if not layers:
        raise ValueError("PSD 至少要有一个图层")

    out = BytesIO()
    # ── File header ──
    out.write(b"8BPS" + struct.pack(">H", 1) + b"\x00" * 6
              + struct.pack(">H", 3)          # 通道数（合并图 RGB，无透明）
              + struct.pack(">I", height) + struct.pack(">I", width)
              + struct.pack(">H", 8)          # 每通道 8 bit
              + struct.pack(">H", 3))         # 色彩模式 = RGB
    out.write(struct.pack(">I", 0))           # Color mode data：RGB 不需要
    out.write(struct.pack(">I", 0))           # Image resources：留空

    # ── Layer and mask information ──
    records = BytesIO()
    channel_blobs = BytesIO()
    records.write(struct.pack(">h", len(layers)))
    for layer in layers:
        rgba = layer.image.convert("RGBA")
        lw, lh = rgba.size
        left, top = layer.offset
        bands = rgba.split()                  # R,G,B,A
        planes = [(-1, bands[3]), (0, bands[0]), (1, bands[1]), (2, bands[2])]

        chan_meta: list[tuple[int, int]] = []
        for cid, band in planes:
            counts, body = _rle_plane(band.tobytes(), lw, lh)
            blob = struct.pack(">H", 1) + counts + body   # compression=RLE
            channel_blobs.write(blob)
            chan_meta.append((cid, len(blob)))

        records.write(struct.pack(">iiii", top, left, top + lh, left + lw))
        records.write(struct.pack(">H", len(chan_meta)))
        for cid, size in chan_meta:
            records.write(struct.pack(">hI", cid, size))
        records.write(b"8BIM" + b"norm")
        # flags 的 bit1 置位表示**隐藏**（PSD 里这一位是反的），bit3 恒置位
        records.write(bytes([max(0, min(255, layer.opacity)), 0,
                             0x08 | (0x00 if layer.visible else 0x02), 0]))
        extra = struct.pack(">I", 0) + struct.pack(">I", 0)   # 蒙版 / 混合范围：无
        extra += _pascal4(layer.ascii_name) + _luni(layer.name)
        records.write(struct.pack(">I", len(extra)) + extra)

    layer_info = records.getvalue() + channel_blobs.getvalue()
    layer_info += b"\x00" * (len(layer_info) % 2)             # 补齐到偶数
    layer_and_mask = struct.pack(">I", len(layer_info)) + layer_info \
        + struct.pack(">I", 0)                                # 全局蒙版：无
    out.write(struct.pack(">I", len(layer_and_mask)) + layer_and_mask)

    # ── Image data（合并图，planar：R 全图 → G 全图 → B 全图）──
    merged = _flatten(canvas, layers)
    counts_all, body_all = bytearray(), bytearray()
    for band in merged.split():
        counts, body = _rle_plane(band.tobytes(), width, height)
        counts_all += counts
        body_all += body
    out.write(struct.pack(">H", 1) + bytes(counts_all) + bytes(body_all))
    return out.getvalue()
