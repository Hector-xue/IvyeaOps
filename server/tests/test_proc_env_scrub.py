"""子进程环境脱敏。

**这批是实测出来的洞，不是假想**：``core/config`` 启动时 ``load_dotenv()`` 会把
``server/.env`` 灌进 ``os.environ``，而 README 正建议把密钥放那儿。于是任何子进程
都能直接读到 ``IVYEA_OPS_SECRET``（会话签名密钥，拿到就能伪造管理员 cookie）、
``IVYEA_OPS_PASSWORD_HASH`` 等等。全仓有约 69 处 spawn，其中不少跑的是不完全
可控的代码：终端里敲的命令、AI 决定的工具调用、skills 下 63 个可执行脚本、
外部 MCP server —— 装一个社区 skill 就等于把签名密钥交出去。

注意判据：**必须真起一个子进程看它读到什么**。``/proc/<pid>/environ`` 是 exec 时的
快照，反映不出 ``load_dotenv`` 之后 putenv 的改动，用它来验会得出"没问题"的错误
结论（我第一次就是这么误判的）。
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from app.core import proc

_DUMP = (
    "import os,re;"
    "h=re.compile(r'SECRET|PASSWORD|HASH|TOKEN|KEY',re.I);"
    "print('|'.join(sorted(n for n in os.environ if h.search(n))))"
)


def _child_sees(env: dict) -> set:
    out = subprocess.run([sys.executable, "-c", _DUMP], env=env,
                         capture_output=True, text=True, timeout=30).stdout.strip()
    return {n for n in out.split("|") if n}


@pytest.fixture()
def loaded_secrets(monkeypatch):
    monkeypatch.setenv("IVYEA_OPS_SECRET", "session-signing-key")
    monkeypatch.setenv("IVYEA_OPS_PASSWORD_HASH", "$2b$12$fakehash")
    monkeypatch.setenv("SORFTIME_KEY", "sk-market-data")
    monkeypatch.setenv("PATH", os.environ.get("PATH", "/usr/bin:/bin"))


# ── 核心保证 ────────────────────────────────────────────────────────────

def test_child_cannot_read_the_session_signing_key(loaded_secrets):
    """最要紧的一条：拿到 IVYEA_OPS_SECRET 就能伪造管理员会话。"""
    assert _child_sees(proc.child_env()) == set()


def test_the_hole_is_real_without_scrubbing(loaded_secrets):
    """反向验证：不脱敏的话确实全都看得见 —— 这条在于说明这个模块不是摆设。"""
    leaked = _child_sees(dict(os.environ))
    assert "IVYEA_OPS_SECRET" in leaked
    assert "IVYEA_OPS_PASSWORD_HASH" in leaked


def test_non_secret_variables_are_preserved(loaded_secrets, monkeypatch):
    """脱敏不能把正常环境也端掉，否则子进程根本跑不起来。"""
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    env = proc.child_env()
    assert env.get("PATH") and env.get("LANG") == "en_US.UTF-8"


def test_explicit_allow_list(loaded_secrets):
    """需要哪个就显式写出来 —— 读代码的人一眼能看见这个子进程能碰到哪些秘密。"""
    env = proc.child_env(allow=["SORFTIME_KEY"])
    assert env["SORFTIME_KEY"] == "sk-market-data"
    assert "IVYEA_OPS_SECRET" not in env


def test_extra_injection_survives_scrubbing(loaded_secrets):
    """显式塞进去的（比如给 agent 的一次性 token）是调用方有意为之，不该被剥掉。"""
    env = proc.child_env(extra={"IVYEA_API_TOKEN": "one-shot"})
    assert env["IVYEA_API_TOKEN"] == "one-shot"


def test_escape_hatch_env_var(loaded_secrets, monkeypatch):
    """给"我的工具就是靠环境变量拿 key"的用户兜底，不至于升级后突然坏且无法自救。"""
    monkeypatch.setenv("IVYEA_OPS_CHILD_ENV_ALLOW", "SORFTIME_KEY, IVYEA_OPS_SECRET")
    env = proc.child_env()
    assert env["SORFTIME_KEY"] == "sk-market-data"
    assert env["IVYEA_OPS_SECRET"] == "session-signing-key"


@pytest.mark.parametrize("name", [
    "IVYEA_OPS_SECRET", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN",
    "DB_PASSWORD", "SOME_API_KEY", "MY_PASSWD",
])
def test_secret_name_detection(name):
    assert proc.is_secret_env(name)


@pytest.mark.parametrize("name", ["PATH", "HOME", "LANG", "TERM", "SSH_AUTH_SOCK"])
def test_non_secret_names_are_kept(name):
    """SSH_AUTH_SOCK 名字里有 AUTH 但转发的是 socket 不是密钥，剥掉会让 git 推送坏掉。"""
    assert not proc.is_secret_env(name)


# ── 收口后的调用路径 ────────────────────────────────────────────────────

def test_runner_child_env_is_scrubbed(loaded_secrets):
    """agents 跑的是 AI 自己决定的工具调用，是最不该看到签名密钥的地方。"""
    from app.services.runners import build_child_env

    env = build_child_env(sys.executable)
    assert "IVYEA_OPS_SECRET" not in env
    assert env.get("PATH"), "PATH 还得在，否则 runner 拉不起 node/MCP"
    assert env.get("HOME")


def test_proc_run_scrubs_by_default(loaded_secrets):
    out = proc.run([sys.executable, "-c", _DUMP], capture_output=True, text=True)
    assert out.stdout.strip() == ""


def test_proc_run_has_a_default_timeout(loaded_secrets):
    """外部工具挂住会把请求线程一起挂住 —— 这个仓库为此吃过亏（CI 转 6 小时）。"""
    with pytest.raises(subprocess.TimeoutExpired):
        proc.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1)
