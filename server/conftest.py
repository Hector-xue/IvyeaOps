"""测试的全局隔离。

**为什么在 server/ 而不是 server/tests/**：conftest 的 fixture 只对**它所在目录的
子树**生效。这个文件原本放在 server/tests/ 下，于是 server/app/tests/ 那 224 个
测试完全没被隔离 —— 它们跑的时候 `settings.data_dir` 指的是**真实的 data 目录**。
（CI 里之所以一直没出事，是因为 CI 的 Test 步骤只跑 `tests`，压根没跑 app/tests。）
放到 server/ 根上，两棵测试树才都盖得住。


**为什么要有这个文件**：`console_sessions` 的库路径来自 `settings.data_dir`，也就是
生产数据。之前只有直接测它的两个文件自己 patch 了路径，别的文件是"我又不碰数据库"
——直到给审批加了留痕，`_tee_session_events` 顺手就往生产库里写了两行 r1/r2，
而那个测试文件从头到尾没提过数据库。

这类事只堵在"想得起来的地方"是堵不住的：任何一次给现有代码路径加落盘，都会让一批
本来干净的测试开始写生产数据，而且**悄无声息**。所以在这里一次性钉死 —— 跑测试时
`data_dir` 整个指向临时目录，谁都写不到真的那份。
"""
from __future__ import annotations

import os
import tempfile

import pytest

# 同样的道理，对日志落盘也要堵一次：`app.core.obs.configure_logging()` 是在
# `app.main` **被 import 的那一刻**执行的，比下面那个 fixture 换 data_dir 要早，
# 于是测试会在**真实** data/logs 下建出日志文件来。这里在 conftest 模块体里就
# 关掉落盘（pytest 先导入 conftest，再导入测试模块），需要验证落盘本身的测试
# 自己显式 configure 到 tmp_path。
os.environ.setdefault("IVYEA_OPS_LOG_FILE", "0")

# 第三件同类的事：`ensure_studio_dirs()` 会把**仓库自带的技能**播种进 SKILLS_ROOT。
# 于是每个把 SKILLS_ROOT 指向 tmp 目录、再自己造几个技能的测试，实际看到的是
# "自己造的 + 自带的"，断言"应该有 3 个"就永远拿到 7 个。指向一个空目录让播种
# 变成 no-op；真正验证播种行为的 tests/test_skill_paths_migration.py 自己
# monkeypatch seed_bundled_skills，不受影响。
os.environ.setdefault(
    "IVYEA_OPS_BUNDLED_SKILLS",
    tempfile.mkdtemp(prefix="ivyea-tests-no-bundled-skills-"),
)

# 第四件：下面那个 fixture 是**改 settings 对象的属性**，而好几个测试 fixture 会
# `importlib.reload(app.core.config)` —— reload 重新构造 settings，隔离当场失效，
# 测试又回去读写真实的 data 目录（test_brain 就是这样，日志里赫然是
# "data dir: /root/ivyea-ops/data"）。环境变量是 config 在类定义时读的，reload
# 之后照样生效，所以这里两条一起上：环境变量兜底，fixture 负责每次会话换新目录。
_ENV_DATA_DIR = tempfile.mkdtemp(prefix="ivyea-tests-data-")
os.environ.setdefault("IVYEA_OPS_DATA_DIR", _ENV_DATA_DIR)

# 第五件：`ensure_studio_dirs()` 还会做一次"从老位置 ~/.hermes 搬迁"。跑测试时
# 它读的是**开发者真实的 ~/.hermes**，日志里能看到 "migrated 759 skills entrie(s)"
# ——每个建 app 的测试都把 759 个真实条目 copy2 进自己的 tmp 目录。数据没丢
# （是拷贝不是移动，已核实），但它既让测试变慢，也让结果取决于这台机器上恰好
# 攒了什么。指向一个空目录，搬迁自然就是空操作。
# tests/test_skill_paths_migration.py 自己 monkeypatch.setenv 了 HERMES_HOME，
# 会盖掉这里的默认值，不受影响。
os.environ.setdefault("HERMES_HOME", tempfile.mkdtemp(prefix="ivyea-tests-legacy-home-"))

from app.core.config import settings  # noqa: E402


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
