#!/usr/bin/env python3
"""CI 用：造一份带"跨平台易碎点"的数据，导出成备份包。

配对的还原端是 ``ci_migrate_verify.py``。两边合起来验证的是同一句承诺：
**换台机器，数据能完整搬过去。**

刻意塞进去的三样东西，都是跨平台时最容易悄悄坏掉的：
* **中文内容**（编码：中文 Windows 默认 GBK，任何不带 encoding 的读写都会炸）；
* **多级嵌套的相对路径**（路径分隔符：Linux 的 ``a/b/c`` 到 Windows 要变 ``a\\b\\c``）；
* **主密钥这个隐藏文件**（手工拷贝最容易漏的东西）。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

WORK = ROOT / "server" / "ci-migrate"
DATA = WORK / "data"
PASSPHRASE = "ci-migration-passphrase"

# 还原端要逐行核对这些，所以两边必须用同一份定义。
ROWS = [
    (1, "B0TEST0001", "中文商品标题 · 带空格与标点"),
    (2, "B0TEST0002", "emoji 🌿 与制表符\t也要活着过去"),
    (3, "B0TEST0003", "path/like/value"),
]
NESTED = "sub/deep/nested.txt"
NESTED_TEXT = "多级嵌套路径下的中文内容\n"


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DATA / "biz.sqlite3")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, asin TEXT, title TEXT)")
    conn.executemany("INSERT OR REPLACE INTO items VALUES (?,?,?)", ROWS)
    conn.commit()
    # **故意不 checkpoint**：让部分事务留在 -wal 里。直接 cp 的话这些会丢，
    # 而在线备份不会 —— 这正是要验的东西。
    conn.close()

    (DATA / "hub_settings.json").write_text(
        json.dumps({"deepseek_api_key": "sk-ci-secret-value", "hermes_base_url": "http://x"},
                   ensure_ascii=False),
        encoding="utf-8")

    nested = DATA / NESTED
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text(NESTED_TEXT, encoding="utf-8")

    from app.core import backup, secrets
    from app.core.config import settings

    settings.data_dir = DATA
    secrets.master_key()                       # 生成隐藏的 .master.key
    path = backup.create(dest_dir=WORK, passphrase=PASSPHRASE, data_dir=DATA)

    report = backup.inspect(path)
    assert report["ok"], report["problems"]
    assert report["master_key_included"], "带口令的包必须含主密钥，否则换机器还原不出密钥"
    print(f"导出 {path.name}：{path.stat().st_size} 字节，"
          f"{len(report['manifest']['entries'])} 项，含主密钥")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
