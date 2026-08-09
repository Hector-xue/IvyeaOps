"""从进程环境里摘走 IvyeaOps 自己的凭据。

**为什么在源头摘而不是逐个调用点加 env=**：全仓约 60 处 spawn，29 处继承全量环境。
逐个补有两个问题——28 处散改本身容易漏；更要命的是**挡不住以后新增的调用点**，
明年谁加一行 subprocess.run 洞就又开了。在源头摘走则一次覆盖现有和未来所有 spawn。

core/proc.child_env() 仍然保留作为第二层（挡第三方工具自己设的凭据变量）。
两层互不替代：这里管"我们自己的秘密"，那里管"跑在高危路径上的子进程"。
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from app.core import secret_env

_DUMP = (
    "import os;"
    "print('|'.join(sorted(n for n in os.environ if n.startswith('IVYEA_OPS_')"
    " or n in ('SORFTIME_KEY','DEEPSEEK_API_KEY','APIMART_KEY'))))"
)


@pytest.fixture()
def restore_store():
    saved = dict(secret_env._STORE)
    yield
    secret_env._STORE.clear()
    secret_env._STORE.update(saved)


def test_harvest_removes_them_from_the_environment(monkeypatch, restore_store):
    monkeypatch.setenv("IVYEA_OPS_SECRET", "session-signing-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")

    assert secret_env.harvest() >= 2
    assert "IVYEA_OPS_SECRET" not in os.environ
    assert "DEEPSEEK_API_KEY" not in os.environ


def test_harvested_values_are_still_readable(monkeypatch, restore_store):
    monkeypatch.setenv("IVYEA_OPS_SECRET", "session-signing-key")
    secret_env.harvest()
    assert secret_env.get("IVYEA_OPS_SECRET") == "session-signing-key"


def test_get_falls_back_to_the_environment(monkeypatch, restore_store):
    """回落让**导入顺序无关紧要**：harvest 之前读环境、之后读字典，都对。"""
    monkeypatch.setenv("SOME_OTHER_VAR", "v")
    assert secret_env.get("SOME_OTHER_VAR") == "v"
    assert secret_env.get("NEVER_SET", "fallback") == "fallback"


def test_harvest_is_idempotent(monkeypatch, restore_store):
    monkeypatch.setenv("IVYEA_OPS_SECRET", "x")
    secret_env.harvest()
    assert secret_env.harvest() == 0          # 第二次没得可摘
    assert secret_env.get("IVYEA_OPS_SECRET") == "x"


def test_industry_standard_keys_are_deliberately_left_alone(monkeypatch, restore_store):
    """OPENAI_API_KEY / ANTHROPIC_API_KEY 不摘：gbrain、hermes 这些子进程正是靠
    它们工作的，摘掉会直接把功能弄坏。它们由 proc.child_env() 在高危路径上单独
    剥离 —— 分层处理，而不是一刀切。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")
    secret_env.harvest()
    assert os.environ.get("OPENAI_API_KEY") == "sk-openai"
    assert os.environ.get("ANTHROPIC_API_KEY") == "sk-anthropic"


def test_names_never_leak_values(monkeypatch, restore_store):
    monkeypatch.setenv("IVYEA_OPS_SECRET", "super-secret-value")
    secret_env.harvest()
    assert "super-secret-value" not in str(secret_env.names())
    assert "IVYEA_OPS_SECRET" in secret_env.names()


# ── 这才是最终判据：子进程实际读到什么 ──────────────────────────────────

def test_a_plain_subprocess_can_no_longer_read_them(monkeypatch, restore_store):
    """**不传 env 的裸 subprocess** —— 也就是那 29 处没收口的调用点 —— 也读不到了。
    这正是"在源头摘"相对"逐个加 env="的价值所在。"""
    monkeypatch.setenv("IVYEA_OPS_SECRET", "session-signing-key")
    monkeypatch.setenv("SORFTIME_KEY", "sk-market")

    before = subprocess.run([sys.executable, "-c", _DUMP],
                            capture_output=True, text=True, timeout=30).stdout.strip()
    assert "IVYEA_OPS_SECRET" in before, "反向验证：没摘之前确实看得见"

    secret_env.harvest()
    after = subprocess.run([sys.executable, "-c", _DUMP],
                           capture_output=True, text=True, timeout=30).stdout.strip()
    assert "IVYEA_OPS_SECRET" not in after
    assert "SORFTIME_KEY" not in after


def test_hub_settings_env_fallback_still_works(monkeypatch, tmp_path, restore_store):
    """摘走之后 hub_settings 的"配置留空就回落环境变量"这条契约不能断。"""
    from app.core import hub_settings
    from app.core.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setenv("SORFTIME_KEY", "sk-from-env")
    secret_env.harvest()

    assert hub_settings.get("sorftime_key") == "sk-from-env"
