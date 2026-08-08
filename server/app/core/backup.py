"""备份 / 校验 / 恢复。

**为什么现在必须做**：这个产品是自托管的，用户的数据全在他自己那台机器上，而
仓库里此前**一处备份逻辑都没有**——磁盘坏了或者换电脑，数据就没了。凭据静态加密
上线之后紧迫性又高了一档：``data/.master.key`` 成了单点，用户换机器时只拷
``data/`` 却漏掉这个**隐藏文件**，所有密钥就再也解不开。

四个设计取舍
------------
1. **用 SQLite 官方在线备份 API**（``conn.backup()``）而不是复制文件。WAL 模式下
   直接 cp 出来的 .sqlite3 可能缺最近的事务（数据在 -wal 里），而在线备份产出的
   是一致快照，且不用停服。备份包里也因此不需要 ``-wal`` / ``-shm``。
2. **分层**。data 目录实测 429MB，其中 terminal_history 一个就 45MB，还有大量
   生成的图片。默认只收"丢了就再也拿不回来"的那部分（各数据库 + 配置 + 技能）；
   体量大又可再生的（图片、日志）要显式 ``include_media`` 才带上。
3. **密钥要么带、要么不带，但绝不明文带**。给了备份口令就把主密钥用
   PBKDF2-HMAC-SHA256 派生的密钥加密后放进包里（能完整恢复）；没给口令就不放
   主密钥，manifest 里明确标注，恢复时告诉用户"密钥要重填"。
   另外：老装机 hub_settings 里可能还是**明文**凭据，进包前统一加密一遍，
   这样没有主密钥的包里就不存在任何可用的凭据。
4. **恢复先干跑**。默认 ``dry_run=True`` 只报告"会覆盖什么、版本对不对、包完不
   完整"，真正落地要显式再来一次。恢复是不可逆动作，不该一次点击就发生。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("ivyea.core.backup")

MANIFEST = "manifest.json"
FORMAT_VERSION = 1

# 备份里不收的东西：-wal/-shm 由在线备份消化掉；日志、缓存、临时文件丢了无所谓。
_SKIP_SUFFIXES = (".sqlite3-wal", ".sqlite3-shm", ".db-wal", ".db-shm", ".log", ".tmp")
# 体量大且可再生 —— 只有 include_media 才带。
_MEDIA_DIRS = {"imagegen-jobs", "image_workspace", "listing_images",
               "listing_copy_images", "logs", "backups"}

_PBKDF2_ROUNDS = 240_000


# ── 口令派生 ────────────────────────────────────────────────────────────

def _derive(passphrase: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, _PBKDF2_ROUNDS, 32)


def _seal(passphrase: str, plaintext: bytes) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt, nonce = os.urandom(16), os.urandom(12)
    blob = AESGCM(_derive(passphrase, salt)).encrypt(nonce, plaintext, None)
    return base64.urlsafe_b64encode(salt + nonce + blob).decode("ascii")


def _unseal(passphrase: str, payload: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    raw = base64.urlsafe_b64decode(payload)
    salt, nonce, blob = raw[:16], raw[16:28], raw[28:]
    return AESGCM(_derive(passphrase, salt)).decrypt(nonce, blob, None)


# ── 备份 ────────────────────────────────────────────────────────────────

def _snapshot_db(src: Path, dest: Path) -> None:
    """在线备份：WAL 下直接 cp 会丢掉还在 -wal 里的事务。"""
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=15.0)
    target = sqlite3.connect(str(dest))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def _iter_payload(data_dir: Path, include_media: bool):
    """产出 (归档内路径, 磁盘路径, 类型)。"""
    for path in sorted(data_dir.iterdir()):
        name = path.name
        if name.startswith(".") and name != ".master.key":
            continue
        if name.endswith(_SKIP_SUFFIXES):
            continue
        if path.is_dir():
            if name in _MEDIA_DIRS and not include_media:
                continue
            for sub in sorted(path.rglob("*")):
                if sub.is_file() and not sub.name.endswith(_SKIP_SUFFIXES):
                    yield f"files/{sub.relative_to(data_dir).as_posix()}", sub, "file"
        elif path.suffix in (".sqlite3", ".db"):
            yield f"db/{name}", path, "db"
        elif name == ".master.key":
            continue          # 单独处理，见 create()
        elif path.is_file():
            yield f"files/{name}", path, "file"


def create(
    dest_dir: Optional[Path] = None,
    *,
    passphrase: str = "",
    include_media: bool = False,
    data_dir: Optional[Path] = None,
) -> Path:
    """生成一个备份包，返回它的路径。"""
    from app.core import secrets as _secrets
    from app.core.config import settings
    from app.core.version import app_version

    src = Path(data_dir) if data_dir is not None else Path(settings.data_dir)
    out_dir = Path(dest_dir) if dest_dir is not None else src / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = out_dir / f"ivyea-ops-backup-{stamp}.zip"

    entries: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for arcname, path, kind in _iter_payload(src, include_media):
                if kind == "db":
                    staged = tmpdir / path.name
                    try:
                        _snapshot_db(path, staged)
                    except sqlite3.Error as exc:
                        logger.warning("跳过打不开的库 %s：%s", path.name, exc)
                        continue
                    payload = staged.read_bytes()
                else:
                    payload = path.read_bytes()

                # 配置里的凭据统一加密后入包：老装机盘上可能还是明文，
                # 直接塞进备份就等于把密钥随备份一起发出去了。
                if arcname == "files/hub_settings.json":
                    try:
                        data = json.loads(payload.decode("utf-8"))
                        payload = json.dumps(_secrets.encrypt_mapping(data),
                                             ensure_ascii=False, indent=2).encode("utf-8")
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("hub_settings 加密入包失败，改为跳过：%s", exc)
                        continue

                zf.writestr(arcname, payload)
                entries.append({"path": arcname, "bytes": len(payload),
                                "sha256": hashlib.sha256(payload).hexdigest()})

            key_path = src / ".master.key"
            has_key = False
            if passphrase and key_path.is_file():
                # 有口令才带主密钥，且只带密文。
                zf.writestr("secrets/master.key.enc", _seal(passphrase, key_path.read_bytes()))
                has_key = True

            manifest = {
                "format": FORMAT_VERSION,
                "app_version": app_version(),
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "created_ts": int(time.time()),
                "include_media": include_media,
                "master_key_included": has_key,
                "entries": entries,
                "total_bytes": sum(e["bytes"] for e in entries),
            }
            zf.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))

    logger.info("备份完成 %s（%d 项，%.1f MB%s）", out.name, len(entries),
                manifest["total_bytes"] / 1048576,
                "，含主密钥" if has_key else "，未含主密钥")
    return out


def prune(keep: int = 7, dest_dir: Optional[Path] = None,
          data_dir: Optional[Path] = None) -> int:
    """只留最近 N 份，返回删掉几份。"""
    from app.core.config import settings

    src = Path(data_dir) if data_dir is not None else Path(settings.data_dir)
    out_dir = Path(dest_dir) if dest_dir is not None else src / "backups"
    if not out_dir.is_dir():
        return 0
    files = sorted(out_dir.glob("ivyea-ops-backup-*.zip"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for old in files[max(0, keep):]:
        try:
            old.unlink()
            removed += 1
        except OSError as exc:
            logger.warning("删除旧备份失败 %s：%s", old.name, exc)
    return removed


# ── 校验 / 恢复 ─────────────────────────────────────────────────────────

def inspect(path: Path) -> dict:
    """读 manifest 并逐项校验 sha256。恢复前的"这个包能不能用"。"""
    report: dict[str, Any] = {"ok": False, "path": str(path), "problems": []}
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if MANIFEST not in names:
                report["problems"].append("缺少 manifest.json，这不是 IvyeaOps 备份包")
                return report
            manifest = json.loads(zf.read(MANIFEST).decode("utf-8"))
            report["manifest"] = manifest

            if int(manifest.get("format", 0)) > FORMAT_VERSION:
                report["problems"].append(
                    f"备份格式版本 {manifest.get('format')} 比当前程序（{FORMAT_VERSION}）新，"
                    "请先升级 IvyeaOps 再恢复")

            corrupted = []
            for entry in manifest.get("entries", []):
                name = entry["path"]
                if name not in names:
                    corrupted.append(f"{name}（缺失）")
                    continue
                if hashlib.sha256(zf.read(name)).hexdigest() != entry["sha256"]:
                    corrupted.append(f"{name}（校验和不符）")
            if corrupted:
                report["problems"].append("内容损坏：" + "、".join(corrupted[:10]))

            report["master_key_included"] = bool(manifest.get("master_key_included"))
            if not report["master_key_included"]:
                report["problems"].append(
                    "包里没有主密钥（备份时未设口令）——恢复后各处 API 密钥需要重填")
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, KeyError) as exc:
        report["problems"].append(f"无法读取备份包：{type(exc).__name__}: {exc}")
        return report

    # 只有"缺主密钥"这一条不算致命：它是提醒，不是错误。
    fatal = [p for p in report["problems"] if not p.startswith("包里没有主密钥")]
    report["ok"] = not fatal
    return report


def restore(
    path: Path,
    *,
    passphrase: str = "",
    dry_run: bool = True,
    data_dir: Optional[Path] = None,
) -> dict:
    """恢复备份。**默认只干跑**。

    干跑报告"会覆盖哪些文件、包完不完整、密钥能不能解开"，真正落地要显式
    ``dry_run=False`` 再来一次 —— 恢复是不可逆的，不该一次点击就发生。
    """
    from app.core.config import settings

    target = Path(data_dir) if data_dir is not None else Path(settings.data_dir)
    report = inspect(path)
    report["dry_run"] = dry_run
    report["data_dir"] = str(target)
    if not report["ok"]:
        return report

    with zipfile.ZipFile(path) as zf:
        manifest = report["manifest"]
        will_overwrite = []
        for entry in manifest.get("entries", []):
            dest = _dest_for(target, entry["path"])
            if dest is not None and dest.exists():
                will_overwrite.append(str(dest.relative_to(target)))
        report["will_overwrite"] = will_overwrite

        if manifest.get("master_key_included"):
            if not passphrase:
                report["ok"] = False
                report["problems"].append("这个包里的主密钥是加密的，需要提供备份口令")
                return report
            try:
                key_bytes = _unseal(passphrase, zf.read("secrets/master.key.enc").decode("ascii"))
            except Exception:  # noqa: BLE001 — 口令错和包损坏对用户是同一件事
                report["ok"] = False
                report["problems"].append("备份口令不对，或主密钥部分已损坏")
                return report
        else:
            key_bytes = None

        if dry_run:
            report["restored"] = 0
            return report

        target.mkdir(parents=True, exist_ok=True)
        restored = 0
        for entry in manifest.get("entries", []):
            dest = _dest_for(target, entry["path"])
            if dest is None:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".restoring")
            tmp.write_bytes(zf.read(entry["path"]))
            os.replace(tmp, dest)          # 原子替换：中途断电不会留下半个库
            restored += 1

        if key_bytes is not None:
            key_dest = target / ".master.key"
            fd = os.open(str(key_dest), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, key_bytes)
            finally:
                os.close(fd)

        report["restored"] = restored
    return report


def _dest_for(target: Path, arcname: str) -> Optional[Path]:
    """把归档内路径映射回 data 目录，并挡住 zip 路径穿越。"""
    if arcname.startswith("db/"):
        rel = arcname[len("db/"):]
    elif arcname.startswith("files/"):
        rel = arcname[len("files/"):]
    else:
        return None
    dest = (target / rel).resolve()
    root = target.resolve()
    # zip 里的 ../ 是经典的解压穿越写法，别人给的备份包不能无条件信。
    if dest != root and not str(dest).startswith(str(root) + os.sep):
        logger.warning("备份包里有越界路径，已跳过：%s", arcname)
        return None
    return dest
