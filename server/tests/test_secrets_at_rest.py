"""凭据静态加密。

开源产品把密钥明文落盘，等于连"去哪儿捞"都一并公开了 —— 这批断言守的就是
"落到盘上的那份不能是明文"，以及几条不能因此被破坏的东西：老装机的明文配置
要照常能用、主密钥丢了要能报出来而不是让服务起不来。
"""
from __future__ import annotations

import json
import os
import stat

import pytest

from app.core import hub_settings, secrets

SECRET = "sk-livekey1234567890ABCDEFghijklmnop"


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    yield tmp_path


# ── 加解密本身 ──────────────────────────────────────────────────────────

def test_roundtrip():
    assert secrets.decrypt(secrets.encrypt(SECRET)) == SECRET


def test_ciphertext_does_not_contain_the_plaintext():
    blob = secrets.encrypt(SECRET)
    assert SECRET not in blob
    assert blob.startswith("enc:v1:")


def test_same_plaintext_encrypts_differently_each_time():
    """nonce 必须每次都换：否则相同的 key 会产生相同的密文，
    看一眼配置文件就能知道两个字段填的是不是同一个 key。"""
    assert secrets.encrypt(SECRET) != secrets.encrypt(SECRET)


def test_empty_stays_empty():
    """"没填"和"填了个空的"在 UI 上是同一种状态，加密成一段密文反而看不出来。"""
    assert secrets.encrypt("") == ""


def test_plaintext_passthrough_keeps_old_installs_working():
    assert secrets.decrypt("明文老配置") == "明文老配置"


def test_tampered_ciphertext_fails_closed():
    """GCM 自带完整性校验：密文被改过要解不开，而不是悄悄返回垃圾。"""
    blob = secrets.encrypt(SECRET)
    tampered = blob[:-4] + ("AAAA" if not blob.endswith("AAAA") else "BBBB")
    assert secrets.decrypt(tampered) == ""


def test_missing_master_key_degrades_instead_of_crashing(tmp_path):
    """换机器只拷了 data、漏了 .master.key —— 该让用户重填，不该让服务起不来。"""
    blob = secrets.encrypt(SECRET)
    (tmp_path / ".master.key").unlink()
    assert secrets.decrypt(blob) == ""      # 新密钥解不开旧密文
    assert secrets.encrypt("x")             # 但功能本身还能继续用


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX 权限位")
def test_master_key_file_is_owner_only(tmp_path):
    secrets.master_key()
    mode = stat.S_IMODE((tmp_path / ".master.key").stat().st_mode)
    assert mode == 0o600, f"主密钥权限是 {oct(mode)}，应为 0600"


# ── 字段判定 ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "deepseek_api_key", "sorftime_key", "lingxing_openapi_secret",
    "ivyea_agent_token", "alert_app_secret",
])
def test_credential_fields_are_encrypted(name):
    assert secrets.is_secret_key(name)


@pytest.mark.parametrize("name", [
    "hermes_base_url", "ivyea_agent_url", "imgflow_url",
    "password_hash",          # 已经是 bcrypt 哈希，再包一层没有意义
])
def test_non_credential_fields_are_left_alone(name):
    assert not secrets.is_secret_key(name)


# ── 落盘 ────────────────────────────────────────────────────────────────

def test_saved_file_has_no_plaintext_secret(_isolated_data_dir):
    hub_settings.save({"deepseek_api_key": SECRET, "hermes_base_url": "http://x"})

    raw = (_isolated_data_dir / "hub_settings.json").read_text(encoding="utf-8")
    assert SECRET not in raw, "密钥明文落盘了"
    on_disk = json.loads(raw)
    assert on_disk["deepseek_api_key"].startswith("enc:v1:")
    # 非凭据字段保持明文，否则排障时配置文件完全没法读
    assert on_disk["hermes_base_url"] == "http://x"


def test_reading_back_returns_plaintext(_isolated_data_dir):
    hub_settings.save({"deepseek_api_key": SECRET})
    assert hub_settings.get("deepseek_api_key") == SECRET
    assert hub_settings.load()["deepseek_api_key"] == SECRET


def test_save_returns_plaintext_to_the_caller(_isolated_data_dir):
    """保存后前端要回显、runner 配置同步要用真值 —— 返回值必须是明文。"""
    out = hub_settings.save({"deepseek_api_key": SECRET})
    assert out["deepseek_api_key"] == SECRET


def test_legacy_plaintext_file_is_upgraded_on_next_save(_isolated_data_dir):
    """老装机升级上来：盘上是明文，读得出来，存一次之后变密文。"""
    (_isolated_data_dir / "hub_settings.json").write_text(
        json.dumps({"deepseek_api_key": SECRET}), encoding="utf-8")

    assert hub_settings.get("deepseek_api_key") == SECRET      # 照常能用

    hub_settings.save({"hermes_base_url": "http://x"})          # 存别的字段
    raw = (_isolated_data_dir / "hub_settings.json").read_text(encoding="utf-8")
    assert SECRET not in raw, "保存一次之后就该是密文了"


def test_decrypt_all_escape_hatch_restores_plaintext(_isolated_data_dir):
    """降级逃生舱：回退到不认识 enc:v1: 的旧版本前，要能把盘上还原成明文。

    没有这个口子，回退后旧代码会把整串密文当 key 去调 API —— 表现为"所有集成
    突然全部鉴权失败"，而且几乎不会有人联想到是版本回退造成的。
    """
    hub_settings.save({"deepseek_api_key": SECRET, "hermes_base_url": "http://x"})
    assert secrets.decrypt_settings_file_in_place() == 1

    raw = json.loads((_isolated_data_dir / "hub_settings.json").read_text(encoding="utf-8"))
    assert raw["deepseek_api_key"] == SECRET      # 旧版本能直接读
    assert raw["hermes_base_url"] == "http://x"
    assert secrets.decrypt_settings_file_in_place() == 0   # 幂等


def test_diagnostic_bundle_still_redacts_after_encryption(_isolated_data_dir):
    """诊断包读的是解密后的明文，脱敏那层不能因为加密而失效。"""
    from app.services import diagnostics

    hub_settings.save({"deepseek_api_key": SECRET})
    payload = diagnostics.build_bundle(data_dir=_isolated_data_dir)
    assert SECRET.encode() not in payload


def test_public_keys_are_not_treated_as_secrets():
    """名字里带 key 不等于是秘密。公钥本来就是公开的 —— 加密它没有意义，
    还让排障时配置文件读不出来。"""
    assert not secrets.is_secret_key("skill_market_pubkey")
