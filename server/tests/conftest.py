"""测试的全局隔离。

**为什么要有这个文件**：`console_sessions` 的库路径来自 `settings.data_dir`，也就是
生产数据。之前只有直接测它的两个文件自己 patch 了路径，别的文件是"我又不碰数据库"
——直到给审批加了留痕，`_tee_session_events` 顺手就往生产库里写了两行 r1/r2，
而那个测试文件从头到尾没提过数据库。

这类事只堵在"想得起来的地方"是堵不住的：任何一次给现有代码路径加落盘，都会让一批
本来干净的测试开始写生产数据，而且**悄无声息**。所以在这里一次性钉死 —— 跑测试时
`data_dir` 整个指向临时目录，谁都写不到真的那份。
"""
from __future__ import annotations

import pytest

from app.core.config import settings


@pytest.fixture(autouse=True, scope="session")
def _isolate_data_dir(tmp_path_factory):
    real = settings.data_dir
    sandbox = tmp_path_factory.mktemp("ivyea-ops-data")
    settings.data_dir = sandbox
    # 换了目录就得建表：沙箱里是空的，不初始化的话原本"不碰数据库"的测试会撞
    # OperationalError: no such table，看着像功能坏了，其实只是隔离没做完整。
    from app.services import console_sessions
    console_sessions.init_db()
    try:
        yield sandbox
    finally:
        settings.data_dir = real
