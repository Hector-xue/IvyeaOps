"""主题注册表和 CSS 必须对得上。

主题的**视觉真相**在 `client/src/styles/workbench.css` 的 `[data-theme=x]` 变量块里，
**元数据真相**（中文名、图标、明暗、选择器上那颗圆点的颜色）在
`client/src/lib/themes.ts` 里。两份分处两种语言，没法靠类型系统对齐。

不钉住的后果已经发生过一次：在 `lib/themes.ts` 出现之前，同一份清单手抄了三份
（workbench.css / MainLayout.tsx / agents/utils/ivyeaOpsTheme.ts），其中
deep-space、smoke-gold、catppuccin、hermes 四套的强调色 CSS 和 TS 是两个值 ——
hermes 一个是橙 `#e8a84a`、一个是绿 `#34d399`。表现为主题选择器上的小圆点
和点进去之后的界面根本不是一个颜色，而且没有任何报错。

所以这里钉三条：
  ① 两边的主题 id 集合完全一致（加了 CSS 块忘了注册、或反过来，都红）；
  ② 每套的 accent 等于 CSS 里的 `--acc`；
  ③ 声明为 light 的主题，其 `--t`（正文色）确实比 `--bg`（页面底）暗。
     —— 明暗标错会让 agents 子树的 `.dark` 和 HSL 变量对不上，界面撕裂。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_CLIENT = Path(__file__).resolve().parents[2] / "client"
_CSS = _CLIENT / "src" / "styles" / "workbench.css"
_TS = _CLIENT / "src" / "lib" / "themes.ts"


_TOKENS = _CLIENT / "src" / "styles" / "mendao-tokens.css"

# 选择器里的主题名：`[data-theme=light]` 和 `[data-theme="mendao-light"]` 都要认
_SEL = re.compile(r"\[data-theme=\"?([\w-]+)\"?\]")


def _collect(text: str, into: dict[str, dict[str, str]]) -> None:
    """把一段 CSS 里的变量声明按主题归堆。

    要处理三种写法，因为三种都真实存在：
      · `:root{...}`                              —— 默认主题(dark)，同时是所有主题的底
      · `[data-theme=light]{...}`                 —— 旧 16 套，不带引号
      · `[data-theme="a"],\\n[data-theme="b"]{...}` —— 门道两套共用的接线块与别名块
    """
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", text, re.S):
        sel, body = m.group(1), m.group(2)
        decls = dict(re.findall(r"(--[\w-]+):\s*([^;]+);", body))
        if not decls:
            continue
        names = _SEL.findall(sel)
        if ":root" in sel and not names:
            names = ["*"]          # `*` = 所有主题共享的底
        for n in names:
            into.setdefault(n, {}).update(decls)


def _resolve(value: str, scope: dict[str, str], depth: int = 0) -> str:
    """把 `var(--x)` 一层层展开。门道那两套是两跳：
    `--acc: var(--md-info)` → `--md-info: rgb(var(--md-c-info))` → `rgb(64 120 242)`。
    """
    if depth > 8:
        raise AssertionError(f"变量解析成环：{value!r}")
    m = re.search(r"var\((--[\w-]+)\)", value)
    if not m:
        return value.strip()
    inner = scope.get(m.group(1))
    assert inner is not None, f"{value!r} 引用了未定义的变量 {m.group(1)}"
    return _resolve(value[: m.start()] + inner + value[m.end():], scope, depth + 1)


def _css_themes() -> dict[str, dict[str, str]]:
    """每套主题**最终生效**的变量值（已展开 var 间接层）。"""
    raw: dict[str, dict[str, str]] = {}
    _collect(_TOKENS.read_text(encoding="utf-8"), raw)
    css = _CSS.read_text(encoding="utf-8")
    # 主题块全在文件开头的调色板区，正文样式里不会再出现 [data-theme=] 选择器
    head = css[: css.index("html, body")] if "html, body" in css else css[:14000]
    _collect(head, raw)

    shared = raw.pop("*", {})
    out: dict[str, dict[str, str]] = {}
    for name, decls in raw.items():
        scope = {**shared, **decls}          # 主题自己的声明压过 :root 的底
        out[name] = {k: _resolve(v, scope) for k, v in scope.items()}
    # `:root` 既是共享底、又是 dark 这一套本身
    out.setdefault("dark", {k: _resolve(v, shared) for k, v in shared.items()})
    return out


def _ts_themes() -> dict[str, dict[str, str]]:
    ts = _TS.read_text(encoding="utf-8")
    block = re.search(r"export const THEMES[^=]*=\s*\[(.*?)\n\];", ts, re.S)
    assert block, "lib/themes.ts 里没找到 THEMES 数组 —— 结构变了，这条守卫要跟着改"
    out: dict[str, dict[str, str]] = {}
    for row in re.finditer(r"\{([^}]*)\}", block.group(1)):
        fields = dict(re.findall(r"(\w+):\s*\"([^\"]*)\"", row.group(1)))
        if "id" in fields:
            out[fields["id"]] = fields
    return out


def _rgb(value: str) -> tuple[int, int, int]:
    v = value.strip()
    m = re.match(r"rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)", v, re.I)
    if m:
        return int(float(m.group(1))), int(float(m.group(2))), int(float(m.group(3)))
    m = re.fullmatch(r"#([0-9a-f]{6})", v, re.I)
    if m:
        h = m.group(1)
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    raise AssertionError(f"认不出的颜色写法：{value!r}")


def _luma(rgb: tuple[int, int, int]) -> float:
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def test_theme_ids_match_between_css_and_registry():
    css, ts = set(_css_themes()), set(_ts_themes())
    assert css == ts, (
        f"CSS 里有而注册表没有：{sorted(css - ts)}\n"
        f"注册表里有而 CSS 没有：{sorted(ts - css)}\n"
        "加主题要同时改 workbench.css 的变量块和 lib/themes.ts 的 THEMES。"
    )


@pytest.mark.parametrize("theme_id", sorted(_ts_themes()))
def test_registry_accent_matches_css(theme_id: str):
    """选择器上那颗圆点必须就是这套主题真正的强调色。"""
    css_acc = _css_themes()[theme_id].get("--acc")
    assert css_acc, f"{theme_id} 在 CSS 里没有 --acc"
    assert _rgb(css_acc) == _rgb(_ts_themes()[theme_id]["accent"]), (
        f"{theme_id}: CSS --acc = {css_acc}，注册表 accent = "
        f"{_ts_themes()[theme_id]['accent']} —— 圆点和真实界面会是两个颜色"
    )


@pytest.mark.parametrize("theme_id", sorted(_ts_themes()))
def test_declared_mode_matches_actual_luminance(theme_id: str):
    """声明 light 的主题，正文色必须真的比页面底暗（深色主题反之）。

    明暗标错不会有任何报错，但 agents 子树会拿着错误的 `.dark` 去渲染 ——
    深色变量配浅色 class，整个子树撕裂。
    """
    v = _css_themes()[theme_id]
    bg_l, fg_l = _luma(_rgb(v["--bg"])), _luma(_rgb(v["--t"]))
    declared = _ts_themes()[theme_id]["mode"]
    actual = "light" if fg_l < bg_l else "dark"
    assert declared == actual, (
        f"{theme_id} 注册表标成 {declared}，但 CSS 里 --bg 亮度 {bg_l:.0f}、"
        f"--t 亮度 {fg_l:.0f} → 实际是 {actual}"
    )
