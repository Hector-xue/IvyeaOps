"""配置里的凭据静态加密。

**为什么开源产品更需要这个**：闭源软件把密钥明文存盘只是"不够好"；IvyeaOps 的
代码是公开的，等于把**存放位置、字段名、文件格式**也一并公开了 —— 拿到用户
``data/`` 目录的人不需要逆向就知道去 ``hub_settings.json`` 里捞
``deepseek_api_key``。所以这不是加分项，是因为开源才必须做的一条。

设计取舍
--------
* **AES-256-GCM**（``cryptography`` 已经因为领星 OpenAPI 签名进了依赖，不新增）。
  GCM 自带完整性校验：密文被改过会解密失败而不是悄悄返回垃圾。
* **主密钥落在 ``data/.master.key``**，权限 0600，首次用到时生成。它跟密文放在
  同一台机器上 —— 这挡的是"拷走了 data 目录"和"看了备份文件"，挡不住已经拿到
  机器 root 的人。对自托管产品来说这是合适的威胁模型；再往上就得要求用户每次
  启动输密码，那会毁掉开机自启。
* **向后兼容**：解密遇到没有 ``enc:v1:`` 前缀的值就原样返回。老装机的明文配置
  照常能用，下次保存时自动转成密文。
* **解不开不炸服务**：主密钥丢了（换机器只拷了 data 没拷 .master.key）时返回空
  串并打一条明确的警告，让用户去重填 —— 而不是整个服务起不来。
"""
from __future__ import annotations

import base64
import logging
import os
import re
import secrets as _stdlib_secrets
from pathlib import Path
from typing import Any

logger = logging.getLogger("ivyea.core.secrets")

_PREFIX = "enc:v1:"
_KEY_BYTES = 32          # AES-256
_NONCE_BYTES = 12        # GCM 推荐长度

# 字段名命中这些词就当凭据加密。跟诊断包的脱敏用的是同一套判据 —— 两处若不一致，
# 就会出现"日志/诊断包里脱了、落盘却是明文"这种最难发现的漏法。
_SECRET_HINT = re.compile(
    r"(key|secret|token|password|passwd|credential|cookie)",
    re.IGNORECASE,
)

# 例外：这些名字里带敏感词，但值本身不是凭据，加密了反而没法排障。
_NOT_SECRET = {
    "password_hash",      # 已经是 bcrypt 哈希，再加一层没有意义
    "hermes_api_key_env",  # 存的是环境变量**名**，不是值
}


def is_secret_key(name: str) -> bool:
    if name in _NOT_SECRET:
        return False
    return bool(_SECRET_HINT.search(name or ""))


def _key_path() -> Path:
    from app.core.config import settings
    return Path(settings.data_dir) / ".master.key"


def master_key() -> bytes:
    """读取主密钥，没有就生成一把。"""
    path = _key_path()
    if path.is_file():
        raw = path.read_bytes().strip()
        try:
            key = base64.urlsafe_b64decode(raw)
        except Exception as exc:
            raise RuntimeError(f"主密钥文件损坏：{path}（{exc}）") from exc
        if len(key) != _KEY_BYTES:
            raise RuntimeError(f"主密钥长度不对：{path}")
        return key

    key = _stdlib_secrets.token_bytes(_KEY_BYTES)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 先以 0600 建文件再写：先写后 chmod 会有一个瞬间是 0644。
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, base64.urlsafe_b64encode(key))
    finally:
        os.close(fd)
    logger.info("已生成主密钥 %s —— **备份 data 目录时务必带上它**，否则密文解不开", path)
    return key


def is_encrypted(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def encrypt(plaintext: str) -> str:
    """空串保持空串 —— "没填" 和 "填了个空的" 在 UI 上是同一种状态，
    加密后会变成一段密文，反而看不出来是空的。"""
    if not plaintext:
        return plaintext
    if is_encrypted(plaintext):
        return plaintext
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = _stdlib_secrets.token_bytes(_NONCE_BYTES)
    blob = AESGCM(master_key()).encrypt(nonce, plaintext.encode("utf-8"), None)
    return _PREFIX + base64.urlsafe_b64encode(nonce + blob).decode("ascii")


def decrypt(value: Any) -> Any:
    """没有前缀的值原样返回（老装机的明文配置照常能用）。"""
    if not is_encrypted(value):
        return value
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        raw = base64.urlsafe_b64decode(value[len(_PREFIX):])
        nonce, blob = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
        return AESGCM(master_key()).decrypt(nonce, blob, None).decode("utf-8")
    except Exception as exc:
        # 最常见的原因：换机器时只拷了 data 却漏了 .master.key。
        # 这时候该让用户重填，而不是让整个服务起不来。
        logger.warning("配置项解密失败（主密钥是否丢了？）：%s", type(exc).__name__)
        return ""


def encrypt_mapping(data: dict) -> dict:
    """按字段名决定加不加密，返回可落盘的字典。"""
    return {k: (encrypt(v) if is_secret_key(k) and isinstance(v, str) else v)
            for k, v in data.items()}


def decrypt_mapping(data: dict) -> dict:
    return {k: (decrypt(v) if isinstance(v, str) else v) for k, v in data.items()}


def decrypt_settings_file_in_place() -> int:
    """把 ``hub_settings.json`` 里的密文全部还原成明文，返回改了几条。

    **降级逃生舱**：密文一旦落盘，回退到不认识 ``enc:v1:`` 的旧版本时，旧代码会
    把整串密文当成 key 去调 API —— 表现为"所有集成突然全部鉴权失败"，而且极难
    联想到是版本回退造成的。留这个口子，回退前跑一次即可：

        python -m app.core.secrets --decrypt-all

    这是有意保留的单向风险出口，不是调试工具；跑完盘上就是明文，别在日常用。
    """
    import json

    from app.core.config import settings

    path = Path(settings.data_dir) / "hub_settings.json"
    if not path.is_file():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    changed = 0
    for key, value in list(data.items()):
        if is_encrypted(value):
            data[key] = decrypt(value)
            changed += 1
    if changed:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    return changed


if __name__ == "__main__":  # pragma: no cover - 运维口子
    import sys

    if "--decrypt-all" in sys.argv:
        n = decrypt_settings_file_in_place()
        print(f"已还原 {n} 项为明文：降级到旧版本前用的口子，跑完请尽快升回来。")
    else:
        print("用法: python -m app.core.secrets --decrypt-all")
