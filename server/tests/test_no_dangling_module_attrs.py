"""守卫：代码里 `模块.属性` 引用的属性必须真实存在。

起因：ruff 清"未使用导入"时删掉了 ad_audit 里被路由当作 `ad_audit.runner_status()`
引用的再导出，广告审计的执行器下拉从此 500；同期 Agent 工具 `lingxing_optimizer`
调用的 `lingxing.optimizer_run` 在路由改成后台任务后已不存在。两处都只在运行时才炸，
单测没覆盖到。这里静态扫一遍 app/ 下所有对 `app.*` 模块的属性访问和 from-import。
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"


def _scan() -> list[str]:
    bad: list[str] = []
    for f in sorted(APP.rglob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        alias: dict[str, str] = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.level == 0 and (n.module or "").startswith("app"):
                for a in n.names:
                    alias[a.asname or a.name] = f"{n.module}.{a.name}"
                    if a.name == "*":
                        continue
                    mod = importlib.import_module(n.module)
                    if not hasattr(mod, a.name):
                        try:
                            importlib.import_module(f"{n.module}.{a.name}")
                        except ImportError:
                            bad.append(f"{f.relative_to(APP.parent)}:{n.lineno} from {n.module} import {a.name}")
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in alias:
                try:
                    mod = importlib.import_module(alias[n.value.id])
                except ImportError:
                    continue  # 别名指向的是函数/类而非模块
                if not hasattr(mod, n.attr):
                    bad.append(f"{f.relative_to(APP.parent)}:{n.lineno} {n.value.id}.{n.attr}")
    return bad


def test_no_dangling_module_attrs() -> None:
    # 在独立子进程里扫：要导入 app 下全部模块，同进程跑既会被前面测试留下的
    # 状态（已关闭的事件循环等）干扰，也会反过来把导入副作用带给后面的测试。
    import json
    import subprocess
    import sys
    code = (
        "import json, sys; sys.path.insert(0, %r); "
        "from tests.test_no_dangling_module_attrs import _scan; print(json.dumps(_scan()))"
    ) % str(APP.parent)
    out = subprocess.run([sys.executable, "-c", code], cwd=str(APP.parent),
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    assert json.loads(out.stdout.strip().splitlines()[-1]) == []
