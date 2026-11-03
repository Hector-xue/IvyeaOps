"""守卫：前端 CSS 的注释不能被正文里的 `*/` 提前关闭。

起因：workbench.css 里一段注释写着「--t*/--acc」，其中 `*/` 把注释提前关掉了，
后面的中文被浏览器当成选择器，连带把紧跟着的 `.cp-page{…}` 整条规则吞掉 ——
广告看板 / 促销日历的滚动容器和间距从此失效，而构建、类型检查都不会报错
（文字原样留在产物里，只是浏览器解析时丢弃）。

判定：注释闭合之后、同一行剩下的内容里出现中文或全角标点，几乎一定是这种情况
（合法 CSS 的选择器和声明里不会有中文）。
"""
from __future__ import annotations

import re
from pathlib import Path

CLIENT_SRC = Path(__file__).resolve().parents[2] / "client" / "src"
_CJK = re.compile(r"[一-鿿，。、；：（）]")


def _premature_closes(text: str) -> list[int]:
    bad: list[int] = []
    i = 0
    while True:
        a = text.find("/*", i)
        if a < 0:
            return bad
        b = text.find("*/", a + 2)
        if b < 0:
            return bad + [text.count("\n", 0, a) + 1]
        eol = text.find("\n", b + 2)
        tail = text[b + 2: eol if eol >= 0 else len(text)]
        if _CJK.search(tail):
            bad.append(text.count("\n", 0, b) + 1)
        i = b + 2


def test_css_comments_close_where_intended() -> None:
    if not CLIENT_SRC.exists():  # 只装了后端的环境
        return
    problems = []
    for f in sorted(CLIENT_SRC.rglob("*.css")):
        for line in _premature_closes(f.read_text(encoding="utf-8")):
            problems.append(f"{f.relative_to(CLIENT_SRC)}:{line}")
    assert problems == [], "注释里的 `*/` 提前关闭了注释：" + ", ".join(problems)
