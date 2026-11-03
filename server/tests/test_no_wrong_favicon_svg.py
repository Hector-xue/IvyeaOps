"""守卫：client/public 里不许再出现 favicon.svg。

那是初版仓库里一个手绘的绿色小人图标，并不是 Ivyea 的嫩芽 logo；IvyeaOps 自己从没引用过它，
却被复制进 ivyea.com 门户并声明成 SVG 图标 —— 浏览器优先用 SVG，标签页就显示成了这个错图标。
正确的图标只有 favicon.png / apple-touch-icon.png / ivyea-logo.png（嫩芽）。
"""
from __future__ import annotations

from pathlib import Path

CLIENT = Path(__file__).resolve().parents[2] / "client"


def test_no_hand_drawn_favicon_svg() -> None:
    if not CLIENT.exists():  # 只装了后端的环境
        return
    assert not (CLIENT / "public" / "favicon.svg").exists(), "favicon.svg 是错图标，用 favicon.png"
    html = (CLIENT / "index.html").read_text(encoding="utf-8")
    assert "image/svg+xml" not in html, "index.html 不要声明 SVG 图标，浏览器会优先用它"
