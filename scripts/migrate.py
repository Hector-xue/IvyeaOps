#!/usr/bin/env python3
"""换机器搬家：在老机器上打包，在新机器上还原。

    # 老机器（建议设口令：不设的话主密钥不会随包走，还原后要重填所有 API 密钥）
    python scripts/migrate.py export --passphrase '你的口令' --out ~/ivyea-backup.zip

    # 新机器 —— 先看会发生什么，确认无误再执行
    python scripts/migrate.py import ~/ivyea-backup.zip --passphrase '你的口令'
    python scripts/migrate.py import ~/ivyea-backup.zip --passphrase '你的口令' --yes

**为什么不直接 cp 整个 data 目录**：三个原因，每一个都足以让手工拷贝出问题。

1. ``data/`` 实测 429MB，其中大头是可再生的图片和日志；
2. WAL 模式下直接复制 ``.sqlite3`` 会丢掉还在 ``-wal`` 里的事务，拷过去的库可能
   缺最近的数据 —— 而且这种缺失不报错，你要过很久才发现；
3. ``data/.master.key`` 是**隐藏文件**，是解开所有 API 密钥的钥匙，手工拷极容易漏。
   漏了之后所有集成都得重填，而且现象是"密钥莫名其妙失效"，很难联想到原因。

这个脚本走的是和设置页同一套备份/恢复逻辑（``app/core/backup.py``），
Windows / macOS / Linux 三向互通。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))


def _human(n: int) -> str:
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{n / 1024:.0f} KB"


def cmd_export(args: argparse.Namespace) -> int:
    from app.core import backup

    if not args.passphrase:
        print("⚠ 没设口令：主密钥不会进包，还原后需要重新填写所有 API 密钥。")
        print("  想要完整还原，加上 --passphrase '你的口令'。\n")

    path = backup.create(dest_dir=Path(args.out).parent if args.out else None,
                         passphrase=args.passphrase,
                         include_media=args.include_media)
    if args.out:
        target = Path(args.out)
        if target != path:
            path.replace(target)
            path = target

    report = backup.inspect(path)
    print(f"✓ 已导出 {path}（{_human(path.stat().st_size)}，"
          f"{len(report.get('manifest', {}).get('entries', []))} 项）")
    print(f"  含主密钥：{'是' if report.get('master_key_included') else '否'}")
    print("\n把这个文件拷到新机器，然后在那边执行：")
    print(f"  python scripts/migrate.py import {path.name}"
          + (" --passphrase '你的口令'" if args.passphrase else ""))
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    from app.core import backup

    path = Path(args.archive)
    if not path.is_file():
        print(f"✗ 找不到备份包：{path}")
        return 1

    report = backup.restore(path, passphrase=args.passphrase, dry_run=not args.yes)
    for problem in report.get("problems", []):
        print(f"  · {problem}")

    if not report.get("ok"):
        print("✗ 这个包现在不能用（见上面的说明）")
        return 1

    if not args.yes:
        overwrite = report.get("will_overwrite") or []
        print(f"\n干跑结果：恢复到 {report['data_dir']}")
        print(f"  将写入 {len(report.get('manifest', {}).get('entries', []))} 个文件")
        if overwrite:
            print(f"  其中 {len(overwrite)} 个会**覆盖**现有文件：")
            for name in overwrite[:12]:
                print(f"    - {name}")
            if len(overwrite) > 12:
                print(f"    …… 另外 {len(overwrite) - 12} 个")
        print("\n确认无误后加 --yes 真正执行。")
        return 0

    print(f"✓ 已恢复 {report['restored']} 个文件到 {report['data_dir']}")
    print("  重启 IvyeaOps 后生效。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="IvyeaOps 换机器搬家",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    export = sub.add_parser("export", help="在老机器上打包")
    export.add_argument("--passphrase", default="",
                        help="备份口令。设了才会把主密钥（加密后）打进包里")
    export.add_argument("--out", default="", help="输出文件路径")
    export.add_argument("--include-media", action="store_true",
                        help="连图片等可再生内容一起打包（包会大很多）")
    export.set_defaults(func=cmd_export)

    imp = sub.add_parser("import", help="在新机器上还原")
    imp.add_argument("archive", help="备份包路径")
    imp.add_argument("--passphrase", default="")
    imp.add_argument("--yes", action="store_true",
                     help="真正执行。不加则只做干跑，告诉你会覆盖什么")
    imp.set_defaults(func=cmd_import)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
