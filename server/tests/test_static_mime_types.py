"""前端静态资源的 Content-Type 必须是我们钉死的那个，不能听宿主机的。

Python 的 `mimetypes` 表是**跟着操作系统走**的：Linux 读 /etc/mime.types，
Windows 读注册表。而 `/assets/*` 是交给 StaticFiles 服务的 —— 它内部直接调
`mimetypes.guess_type`，没有任何地方能让我们传 media_type 进去。宿主机答错，
用户就拿到错的 Content-Type。

已经出过的两次：

  ① `.js` 在 Windows 上被答成 `text/plain`。注册表里 `HKCR\\.js` 被各种安装包
     写成 text/plain 很常见，而浏览器对 ES module 做严格 MIME 检查，收到
     text/plain 直接拒绝执行 —— 整个页面一片空白，控制台只有一句 MIME 报错，
     排查起来完全不像"服务端配置问题"。由 @jacks10086 在 PR #64 里报告并定位。
  ② `.woff2` 在部分 Linux 发行版上被答成 `text/plain`（本项目的服务器就是）。
     字体照样能加载，但任何按 content-type 做判断的代理或安全策略看到的是假话。

所以 `app.main` 在导入时用 `mimetypes.add_type` 把这几个钉死。这份测试守的就是
那几行别被删掉 —— **在 Windows 的 CI 上，删掉就会红**（测试矩阵里有 windows-latest）。
"""
from __future__ import annotations

import mimetypes

import pytest

# 导入即注册（add_type 写在模块顶层作用域）。这行本身就是被测行为的一部分：
# 如果哪天有人把 add_type 挪进某个函数里，这里的断言会立刻发现。
import app.main  # noqa: F401


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        # ES module 和普通脚本都要对：Vite 产出的 /assets/*.js 是 module。
        ("index-abc123.js", ("application/javascript", "text/javascript")),
        ("worker.mjs", ("application/javascript", "text/javascript")),
        ("inter-latin-var.woff2", ("font/woff2",)),
        ("legacy.woff", ("font/woff",)),
    ],
)
def test_guessed_type_is_pinned(filename: str, expected: tuple[str, ...]) -> None:
    guessed, _ = mimetypes.guess_type(filename)
    # 允许 text/javascript：较新的 Python 自带表把 .js 定成了它，而它和
    # application/javascript 一样能通过浏览器的模块 MIME 检查。**唯独不能是
    # text/plain 或 None** —— 那两个才是故障。
    assert guessed in expected, (
        f"{filename} 被猜成 {guessed!r}，期望 {expected}。"
        "检查 app/main.py 顶部那组 mimetypes.add_type 是否还在。"
    )
