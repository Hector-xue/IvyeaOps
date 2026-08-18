#!/usr/bin/env python3
"""CI 用：在另一个平台上还原 ``ci_migrate_export.py`` 产出的包，逐行核对。

**核对而不是"没报错就算过"**：恢复最危险的失败模式不是抛异常，而是悄悄少了几行
或者中文变成乱码 —— 那种失败当场看不出来，要等用户几个月后翻旧数据才发现。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from ci_migrate_export import NESTED, NESTED_TEXT, PASSPHRASE, ROWS  # noqa: E402

WORK = ROOT / "server" / "ci-migrate"
FRESH = WORK / "restored"


def main() -> int:
    archives = sorted(WORK.glob("*.zip"))
    assert archives, f"{WORK} 下没有备份包"
    archive = archives[-1]

    from app.core import backup

    FRESH.mkdir(parents=True, exist_ok=True)

    # 先干跑：这是产品承诺的默认行为，顺带确认包在这个平台上读得动
    dry = backup.restore(archive, passphrase=PASSPHRASE, data_dir=FRESH)
    assert dry["ok"], dry["problems"]
    assert dry["dry_run"] is True and dry["restored"] == 0, "干跑不该写任何文件"

    # 口令不对必须失败，且不能写进去一半
    wrong = backup.restore(archive, passphrase="wrong", dry_run=False, data_dir=FRESH)
    assert not wrong["ok"], "口令不对居然还原成功了"
    assert not any(FRESH.iterdir()), "口令不对时写了半截数据"

    report = backup.restore(archive, passphrase=PASSPHRASE, dry_run=False, data_dir=FRESH)
    assert report["ok"], report["problems"]

    # ① 数据库逐行核对（含中文、emoji、制表符，以及故意留在 -wal 里的事务）
    conn = sqlite3.connect(FRESH / "biz.sqlite3")
    got = conn.execute("SELECT id, asin, title FROM items ORDER BY id").fetchall()
    conn.close()
    assert got == [tuple(r) for r in ROWS], f"数据对不上：\n期望 {ROWS}\n实际 {got}"

    # ② 多级嵌套路径（Linux 的 a/b/c ↔ Windows 的 a\b\c）
    nested = FRESH / Path(NESTED)
    assert nested.is_file(), f"嵌套路径没还原出来：{NESTED}"
    assert nested.read_text(encoding="utf-8") == NESTED_TEXT, "嵌套文件内容变了（编码？）"

    # ③ 主密钥跟着口令过来了，因此密钥能解开
    assert (FRESH / ".master.key").is_file(), "主密钥没还原 —— 换机器后所有密钥都会失效"

    from app.core import hub_settings
    from app.core.config import settings
    settings.data_dir = FRESH
    assert hub_settings.get("deepseek_api_key") == "sk-ci-secret-value", \
        "密钥解不开：主密钥或密文在跨平台传输中损坏了"

    # ④ 包里不能有明文凭据（备份会被拷来拷去，甚至发给别人排障）
    assert b"sk-ci-secret-value" not in archive.read_bytes(), "备份包里有明文密钥"

    print(f"✓ {archive.name} 在 {sys.platform} 上完整还原："
          f"{report['restored']} 个文件，{len(got)} 行数据逐行一致，密钥可解")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
