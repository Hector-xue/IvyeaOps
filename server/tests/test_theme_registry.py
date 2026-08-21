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


# 调色板文件。**新增一套自带调色板的主题时要往这里加一行** —— 少加的表现是
# "`var(--acc)` 引用了未定义的变量"，看着像 CSS 写错了，其实是这条清单没跟上。
_TOKEN_FILES = [
    _CLIENT / "src" / "styles" / "mendao-tokens.css",   # 门道 + 静谧
    _CLIENT / "src" / "styles" / "lucent-tokens.css",   # 琉璃
]

# 选择器里的主题名：`[data-theme=light]` 和 `[data-theme="mendao-light"]` 都要认
_SEL = re.compile(r"\[data-theme=\"?([\w-]+)\"?\]")


def _collect(text: str, into: dict[str, dict[str, str]]) -> None:
    """把一段 CSS 里的变量声明按主题归堆。

    要处理三种写法，因为三种都真实存在：
      · `:root{...}`                              —— 默认主题(dark)，同时是所有主题的底
      · `[data-theme=light]{...}`                 —— 旧 16 套，不带引号
      · `[data-theme="a"],\\n[data-theme="b"]{...}` —— 门道两套共用的接线块与别名块
    """
    # **先剥注释。** 这个解析器是按花括号切块的，而 CSS 注释里完全可以出现
    # 花括号（比如写一句 `#root{font-weight:…}` 当例子说明）—— 不剥的话那对
    # 括号会被当成真的规则块，从它往后**所有**主题的变量归属全部错位，
    # 表现是某套主题的 --acc 莫名其妙变成了另一套的值。踩过一次，见 git 历史。
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
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
    for tokens in _TOKEN_FILES:
        _collect(tokens.read_text(encoding="utf-8"), raw)
    css = _CSS.read_text(encoding="utf-8")
    # 主题块全在文件开头的调色板区，正文样式里不会再出现 [data-theme=] 选择器。
    #
    # 切点用调色区结束处那条注释横幅，**不要用固定字节数**。这里原本写的是
    # `css[:14000]`，而调色区当时已经涨到 12100 字符 —— 再加一套主题的接线块就
    # 会把它自己截在外面，报出来是"注册表里有而 CSS 没有 quiet-*"，看起来像
    # 谁写漏了一个变量块，实际是这条切片的锅。找不到横幅时才退回字节数兜底。
    # 切点用横幅那一整行，**不要只用 "GLOBAL BASE" 两个词** —— 谁在上面的
    # 注释里提一句"全局基座那一节"并把这两个词写进去，index() 就会先命中那句
    # 注释，把它后面的主题块（包括它自己所在的那一块）拦腰截断。表现是
    # "某套主题的 --acc 变成了 :root 的默认值"，看着像 CSS 写错了。踩过一次。
    marker = "GLOBAL BASE — painting"
    head = css[: css.index(marker)] if marker in css else css[:14000]
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


# ── 默认主题与一次性迁移 ────────────────────────────────────────────────

def _ts_source() -> str:
    return _TS.read_text(encoding="utf-8")


def test_default_theme_is_registered():
    """DEFAULT_THEME 必须是注册表里真实存在的一项。

    它是**所有启动路径的兜底**（main.tsx、MainLayout、getTheme），拼错的话
    每个新用户和每个 localStorage 被清过的浏览器都会落到一个不存在的主题上，
    而 getTheme 又会把它兜回 DEFAULT_THEME —— 死循环般地一直是错的，且不报错。
    """
    m = re.search(r'export const DEFAULT_THEME\s*=\s*"([^"]+)"', _ts_source())
    assert m, "没找到 DEFAULT_THEME"
    assert m.group(1) in _ts_themes(), f"DEFAULT_THEME = {m.group(1)}，但注册表里没有这一项"


def test_default_theme_exists_in_css():
    """默认主题必须在 CSS 里有对应的变量块，否则全站会回落到 :root（暗夜）。"""
    m = re.search(r'export const DEFAULT_THEME\s*=\s*"([^"]+)"', _ts_source())
    assert m.group(1) in _css_themes()


def test_migration_only_runs_once():
    """迁移必须先写版本号、且只在版本号缺席时改用户已存的主题。

    顺序写反（先改主题后写版本号）在写 localStorage 抛异常时会变成每次刷新
    都强改一遍 —— 用户手动选的主题永远存不住，而且没有任何报错。
    """
    src = _ts_source()
    body = src[src.index("export function migrateTheme"):]
    body = body[: body.index("\n}")]
    set_v = body.index("MIGRATION_KEY, MIGRATION")
    set_theme = body.index("THEME_KEY, DEFAULT_THEME")
    assert set_v < set_theme, "必须先写迁移版本号，再改主题"
    assert "startsWith(\"quiet-\")" in body, "已经在静谧主题上的用户不该被再迁一次"
