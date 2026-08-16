"""知识库本地 markdown 目录的安全文件操作（`~/brain`，即 BRAIN_ROOT）。

**这个模块不依赖任何外部程序。** 它是 `gbrain_service.py` 的继承者 —— 那个模块
是围绕外部 `gbrain` CLI 的白名单包装器，随着知识库前门整体迁到 IvyeaAgent
（搜索、页面、统计、体检、索引重建、对话引用全部走内置治理知识库），那些启动
外部二进制的函数已经没有调用方，连同 `gbrain`/`bun` 一起摘掉了。

留下来的只有一件事：BRAIN_ROOT 下的 .md 文件读 / 写 / 删 / 列。它们从来就是纯
文件系统操作，只是当初恰好住在 GBrain 的包装器里。

安全边界（照搬原实现，一个字都没松）：
  - 路径必须落在 BRAIN_ROOT 之内（resolve 之后再 relative_to 校验，挡目录穿越）
  - 不允许隐藏路径（任何一段以 . 开头）
  - 只允许 .md
  - 写入有大小上限
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("ivyea.services.brain_files")

MAX_QUERY_CHARS = 500
MAX_FILE_BYTES = 512 * 1024
MAX_WRITE_BYTES = 512 * 1024


class BrainFilesError(RuntimeError):
    """面向用户的知识库文件操作失败。"""


# 旧名保留为别名：`routers/brain.py` 的 `_handle()` 按异常类型转 HTTP 400，
# 改名时若漏掉一处 except，用户看到的会从"400 + 原因"变成"500 未处理异常"。
# 别名让新旧写法都能被同一个 except 捕获。
GBrainError = BrainFilesError


def _brain_root() -> Path:
    from app.core import hub_settings
    val = hub_settings.get("brain_root")
    if val:
        return Path(str(val)).resolve()
    fallback = os.environ.get("IVYEA_OPS_BRAIN_ROOT") or str(Path.home() / "brain")
    return Path(fallback).resolve()


def __getattr__(name: str):
    """模块级惰性属性（PEP 562）。

    调用方把 ``BRAIN_ROOT`` 当常量用，但它的值必须在**调用时**解析：它来自
    hub_settings，运行时可改，且 import 阶段还没就绪。这里保持惰性，既让
    `bf.BRAIN_ROOT` 这种写法照常可用，又能跟上配置变更。

    注意：这是合成属性，不是真实模块变量 —— 测试里 `monkeypatch.setattr` 它会
    写入一个真实属性并**遮蔽**本函数（这是期望行为，但别据此以为它本来就存在）。
    """
    if name == "BRAIN_ROOT":
        return _brain_root()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def installed() -> bool:
    """历史遗留探针：外部 GBrain 是否可用。

    知识库已完全不依赖它，这里恒为 False。保留这个函数是因为调用方用它来判断
    "要不要走旧路径"，直接删掉会让那些分支变成 AttributeError；恒 False 则让
    它们干净地走"没有旧后端"的那一支。
    """
    return False


def _safe_rel_path(rel_path: str) -> Path:
    rel = (rel_path or "").strip().lstrip("/")
    if not rel:
        raise BrainFilesError("path is required")
    if "\x00" in rel:
        raise BrainFilesError("invalid path")
    target = (_brain_root() / rel).resolve()
    try:
        target.relative_to(_brain_root())
    except ValueError as e:
        raise BrainFilesError("path escapes brain root") from e
    if any(part.startswith(".") for part in target.relative_to(_brain_root()).parts):
        raise BrainFilesError("hidden paths are not editable")
    if target.suffix.lower() != ".md":
        raise BrainFilesError("only .md files are allowed")
    return target


def list_files() -> dict[str, Any]:
    if not _brain_root().exists():
        raise BrainFilesError(f"brain root not found: {_brain_root()}")
    files: list[dict[str, Any]] = []
    for path in sorted(_brain_root().rglob("*.md")):
        rel_parts = path.relative_to(_brain_root()).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        st = path.stat()
        # Extract one-line summary: first meaningful content line (prefer Chinese)
        summary = ""
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("---") or line.startswith("```"):
                        continue
                    # Skip YAML frontmatter key-value lines
                    if ":" in line and line.split(":")[0].replace("_", "").replace("-", "").isalpha() and len(line) < 60:
                        continue
                    summary = line[:100]
                    break
        except Exception:
            logger.debug("读取摘要失败（旁路，已忽略）", exc_info=True)
        # Category = top-level directory
        category = rel_parts[0] if len(rel_parts) > 1 else "root"
        files.append({
            "path": str(path.relative_to(_brain_root())),
            "name": path.stem,
            "size": st.st_size,
            "mtime": st.st_mtime,
            "category": category,
            "summary": summary,
        })
    return {"root": str(_brain_root()), "files": files, "total": len(files)}


def read_file(rel_path: str) -> dict[str, Any]:
    target = _safe_rel_path(rel_path)
    if not target.is_file():
        raise BrainFilesError("file not found")
    size = target.stat().st_size
    if size > MAX_FILE_BYTES:
        raise BrainFilesError(f"file too large to edit ({size} bytes)")
    return {
        "path": str(target.relative_to(_brain_root())),
        "content": target.read_text(encoding="utf-8", errors="replace"),
        "size": size,
    }


def write_file(rel_path: str, content: str) -> dict[str, Any]:
    target = _safe_rel_path(rel_path)
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_WRITE_BYTES:
        raise BrainFilesError(f"file too large to save (>{MAX_WRITE_BYTES} bytes)")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    rel = str(target.relative_to(_brain_root()))
    return {"ok": True, "path": rel, "size": len(encoded)}


def delete_file(rel_path: str) -> dict[str, Any]:
    target = _safe_rel_path(rel_path)
    if not target.is_file():
        raise BrainFilesError("file not found")
    target.unlink()
    return {"ok": True, "path": rel_path}
