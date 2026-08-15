"""Safe GBrain operations for the IvyeaOps web UI.

This module intentionally exposes a small whitelist around the ``gbrain`` CLI
and the local markdown source directory. It must never become a generic shell
wrapper.
"""
from __future__ import annotations

import os
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.core.proc import no_window_kwargs

logger = logging.getLogger("ivyea.services.gbrain_service")


def _brain_root() -> Path:
    from app.core import hub_settings
    val = hub_settings.get("brain_root")
    if val:
        return Path(str(val)).resolve()
    fallback = os.environ.get("IVYEA_OPS_BRAIN_ROOT") or str(Path.home() / "brain")
    return Path(fallback).resolve()


def __getattr__(name: str):
    """Module-level lazy attribute (PEP 562).

    ``brain_chat_service`` references ``gb.BRAIN_ROOT`` as if it were a
    constant, but the value must be resolved at call time (it reads
    hub_settings, which can change at runtime and isn't ready at import).
    Expose it lazily so existing callers work without each becoming a
    function call, while still honoring live config changes.
    """
    if name == "BRAIN_ROOT":
        return _brain_root()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _gbrain_bin() -> str:
    from app.core import hub_settings
    val = hub_settings.get("gbrain_bin")
    if val:
        return str(val)
    env = os.environ.get("IVYEA_OPS_GBRAIN_BIN")
    if env:
        return env
    # Prefer PATH lookup (handles Windows .exe/.cmd via PATHEXT), then fall back
    # to the Bun global install location, platform-aware.
    found = shutil.which("gbrain")
    if found:
        return found
    name = "gbrain.exe" if os.name == "nt" else "gbrain"
    return str(Path.home() / ".bun" / "bin" / name)


def _bun_bin() -> Optional[str]:
    found = shutil.which("bun")
    if found:
        return found
    cand = Path.home() / ".bun" / "bin" / ("bun.exe" if os.name == "nt" else "bun")
    return str(cand) if cand.exists() else None


def _gbrain_cli_ts() -> Path:
    return Path.home() / ".bun" / "install" / "global" / "node_modules" / "gbrain" / "src" / "cli.ts"


def _gbrain_cmd() -> Optional[list[str]]:
    """ARGV prefix to invoke gbrain. On Windows the bun-generated .exe shim is
    broken ("The system cannot find the path specified"), so run the TypeScript
    entry directly via `bun run <cli.ts>` — which works cross-platform (verified).
    POSIX uses the gbrain binary/symlink. Returns None if neither is available."""
    if os.name == "nt":
        bun = _bun_bin()
        cli = _gbrain_cli_ts()
        if bun and cli.exists():
            return [bun, "run", str(cli)]
        # Fall through to the shim if the package layout is unexpected.
    gb = _gbrain_bin()
    if Path(gb).exists() or shutil.which("gbrain"):
        return [gb]
    if os.name == "nt":
        bun = _bun_bin()
        cli = _gbrain_cli_ts()
        if bun and cli.exists():
            return [bun, "run", str(cli)]
    return None


MAX_QUERY_CHARS = 500
MAX_FILE_BYTES = 512 * 1024
MAX_WRITE_BYTES = 512 * 1024


class GBrainError(RuntimeError):
    """Raised for user-facing GBrain service failures."""


@dataclass
class CommandResult:
    stdout: str
    stderr: str
    returncode: int


def _env() -> dict[str, str]:
    from app.core import integrations
    env = os.environ.copy()
    # systemd has a narrow PATH; make Bun-linked gbrain discoverable.
    extras = [*integrations.extra_path_dirs(), str(Path.home() / ".bun" / "bin")]
    if os.name != "nt":
        extras += ["/usr/local/bin", "/usr/bin"]
    env["PATH"] = os.pathsep.join([e for e in extras if e] + [env.get("PATH", "")])
    return env


def _run_gbrain(args: list[str], timeout: int = 30) -> CommandResult:
    prefix = _gbrain_cmd()
    if not prefix:
        raise GBrainError(f"gbrain not found (looked for the binary and {_gbrain_cli_ts()})")
    try:
        proc = subprocess.run(
            [*prefix, *args],
            cwd=str(_brain_root()) if _brain_root().exists() else str(Path.home()),
            env=_env(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as e:
        raise GBrainError(f"gbrain command timed out after {timeout}s") from e
    except OSError as e:
        raise GBrainError(f"failed to run gbrain: {e}") from e
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or f"gbrain exited {proc.returncode}").strip()
        raise GBrainError(msg[:2000])
    return CommandResult(proc.stdout, proc.stderr, proc.returncode)


def _run_git(args: list[str], timeout: int = 15) -> CommandResult:
    if not _brain_root().exists():
        return CommandResult("", "brain root missing", 1)
    proc = subprocess.run(
        ["git", "-C", str(_brain_root()), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        **no_window_kwargs(),
    )
    return CommandResult(proc.stdout, proc.stderr, proc.returncode)


def _clean_cli_noise(text: str) -> str:
    # The current environment prints an ai.gateway warning on many invocations;
    # keep it out of user-facing cards and parsers.
    lines = []
    for line in text.splitlines():
        if line.startswith("[ai.gateway]"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _parse_stats(text: str) -> dict[str, Any]:
    clean = _clean_cli_noise(text)
    out: dict[str, Any] = {"raw": clean}
    for key in ["Pages", "Chunks", "Embedded", "Links", "Tags", "Timeline"]:
        m = re.search(rf"^{key}:\s+(\d+)", clean, flags=re.MULTILINE)
        out[key.lower()] = int(m.group(1)) if m else 0
    by_type: dict[str, int] = {}
    in_types = False
    for line in clean.splitlines():
        if line.strip() == "By type:":
            in_types = True
            continue
        if in_types:
            m = re.match(r"\s+([^:]+):\s+(\d+)", line)
            if m:
                by_type[m.group(1).strip()] = int(m.group(2))
    out["by_type"] = by_type
    return out


def _parse_search(text: str) -> list[dict[str, Any]]:
    clean = _clean_cli_noise(text)
    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in clean.splitlines():
        m = re.match(r"^\[([0-9.]+)\]\s+([^\s]+)\s+--\s*(.*)$", line)
        if m:
            if current:
                current["snippet"] = current["snippet"].strip()
                items.append(current)
            current = {"score": float(m.group(1)), "slug": m.group(2), "snippet": m.group(3).strip()}
        elif current:
            current["snippet"] += "\n" + line
    if current:
        current["snippet"] = current["snippet"].strip()
        items.append(current)
    return items


def _validate_query(query: str) -> str:
    q = (query or "").strip()
    if not q:
        raise GBrainError("query is required")
    if len(q) > MAX_QUERY_CHARS:
        raise GBrainError(f"query too long (>{MAX_QUERY_CHARS} chars)")
    return q


def _safe_rel_path(rel_path: str) -> Path:
    rel = (rel_path or "").strip().lstrip("/")
    if not rel:
        raise GBrainError("path is required")
    if "\x00" in rel:
        raise GBrainError("invalid path")
    target = (_brain_root() / rel).resolve()
    try:
        target.relative_to(_brain_root())
    except ValueError as e:
        raise GBrainError("path escapes brain root") from e
    if any(part.startswith(".") for part in target.relative_to(_brain_root()).parts):
        raise GBrainError("hidden paths are not editable")
    if target.suffix.lower() != ".md":
        raise GBrainError("only .md files are allowed")
    return target


def _gbrain_config_path() -> Path:
    return Path.home() / ".gbrain" / "config.json"


def _db_configured() -> bool:
    """True when gbrain's config points at a usable database (pglite path or url)."""
    p = _gbrain_config_path()
    if not p.exists():
        return False
    try:
        import json
        cfg = json.loads(p.read_text(encoding="utf-8"))
        return bool(cfg.get("database_path") or cfg.get("database_url"))
    except Exception:
        return False


def _ollama_models(base: str = "http://127.0.0.1:11434") -> list[str]:
    """Names of models a locally-running ollama has, or [] if it's not reachable."""
    try:
        import httpx
        r = httpx.get(f"{base.rstrip('/')}/api/tags", timeout=1.5)
        if r.status_code < 400:
            return [str(m.get("name", "")) for m in (r.json().get("models") or [])]
    except Exception:
        logger.debug("httpx.get 失败（旁路，已忽略）", exc_info=True)
    return []


def ensure_db_ready() -> dict[str, Any]:
    """Make sure GBrain's local DB is initialised. Returns
    ``{db_ready, version_compatible, hint}``. Idempotent; cheap once configured.
    Auto-runs ``gbrain init --pglite`` when the DB is missing, and detects the
    incompatible (database_url-only) version so callers can offer a reinstall
    instead of surfacing the raw "No database URL" error."""
    res: dict[str, Any] = {"db_ready": False, "version_compatible": True, "hint": ""}
    if _db_configured():
        res["db_ready"] = True
        return res
    out = ""
    prefix = _gbrain_cmd()
    if not prefix:
        res["hint"] = "未找到 gbrain，请先在「系统配置 → 系统状态」安装/修复 GBrain。"
        return res
    try:
        Path(_brain_root()).mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [*prefix, "init", "--pglite"], cwd=str(_brain_root()), env=_env(),
            text=True, capture_output=True, timeout=120, **no_window_kwargs())
        out = (proc.stdout + proc.stderr).lower()
    except Exception as e:  # noqa: BLE001
        out = str(e).lower()
    if _db_configured():
        res["db_ready"] = True
    elif any(k in out for k in ("database_url", "--supabase", "--url")):
        res["version_compatible"] = False
        res["hint"] = ("检测到不兼容的 GBrain 版本（要求 database_url）。请在"
                       "「系统配置 → 系统状态」点 GBrain 的「重装/修复」重装到兼容版本。")
    else:
        res["hint"] = "GBrain 本地库初始化失败，可重试或重装 GBrain。"
    return res


def ensure_ready() -> dict[str, Any]:
    """确认本地库是否就绪，**不再自作主张地装配任何东西**。

    以前这里会在面板加载时自动跑 ``gbrain init --pglite``、自动探测 Ollama 并把
    embedding 接上。现在知识库的前门是 IvyeaAgent，语义检索归它自己管，GBrain 只是
    还没迁完的旧读路径 —— 在新机器上自动初始化一个用不到的 pglite 库，只会在部署时
    抛一堆和当前功能无关的错。所以这里只做只读判断，装配交给用户显式操作。
    """
    result: dict[str, Any] = {"db_ready": _db_configured(), "embed_ready": False,
                              "version_compatible": True, "actions": [], "hint": ""}
    if not result["db_ready"]:
        result["hint"] = "GBrain 本地库未初始化（知识库主路径走 IvyeaAgent，不影响使用）。"
    return result


def overview() -> dict[str, Any]:
    stats = _parse_stats(_run_gbrain(["stats"], timeout=30).stdout)
    doctor_raw = ""
    doctor_status = "unknown"
    try:
        doctor_raw = _clean_cli_noise(_run_gbrain(["doctor", "--json", "--fast"], timeout=60).stdout)
        import json
        parsed = json.loads(doctor_raw[doctor_raw.find("{"):]) if "{" in doctor_raw else {}
        doctor_status = str(parsed.get("status", "unknown"))
    except Exception as e:  # best-effort overview
        doctor_status = f"error: {e}"
    search_mode = "unknown"
    try:
        cfg = _clean_cli_noise(_run_gbrain(["config", "get", "search.mode"], timeout=15).stdout)
        search_mode = cfg.strip() or "conservative"
    except Exception:
        search_mode = "conservative"
    git = _run_git(["status", "--short"], timeout=10)

    # Real embedding status: read GBrain's own config (provider:model). This
    # reflects ollama / zhipu / openai / etc — not just OPENAI_API_KEY, which
    # the old `openai_configured` flag wrongly assumed was the only option.
    embed_model = ""
    embed_provider = ""
    try:
        import json as _json
        from pathlib import Path as _P
        cfg_path = _P.home() / ".gbrain" / "config.json"
        if cfg_path.exists():
            gcfg = _json.loads(cfg_path.read_text(encoding="utf-8"))
            embed_model = str(gcfg.get("embedding_model") or "")
            if ":" in embed_model:
                embed_provider = embed_model.split(":", 1)[0]
            elif embed_model:
                embed_provider = "openai"  # bare model name = openai default
    except Exception:
        logger.debug("_P.home 失败（旁路，已忽略）", exc_info=True)
    # Configured if a non-openai provider is set, OR openai with a key present.
    embed_configured = bool(
        embed_provider and (embed_provider != "openai" or os.environ.get("OPENAI_API_KEY"))
    )

    return {
        "brain_root": str(_brain_root()),
        "gbrain_bin": _gbrain_bin(),
        "openai_configured": embed_configured,   # back-compat key, now provider-aware
        "embed_configured": embed_configured,
        "embed_provider": embed_provider,
        "embed_model": embed_model,
        "search_mode": search_mode,
        "doctor_status": doctor_status,
        "git_dirty": bool(git.stdout.strip()),
        "git_status": git.stdout.strip(),
        "stats": stats,
    }


def doctor() -> dict[str, Any]:
    raw = _clean_cli_noise(_run_gbrain(["doctor", "--json"], timeout=90).stdout)
    try:
        import json
        return json.loads(raw[raw.find("{"):])
    except Exception:
        return {"status": "unknown", "raw": raw}


def stats() -> dict[str, Any]:
    return _parse_stats(_run_gbrain(["stats"], timeout=30).stdout)


def search(query: str, mode: str = "search") -> dict[str, Any]:
    q = _validate_query(query)
    if mode not in {"search", "query"}:
        raise GBrainError("mode must be search or query")
    raw = _clean_cli_noise(_run_gbrain([mode, q], timeout=60).stdout)
    return {"mode": mode, "query": q, "raw": raw, "items": _parse_search(raw)}


def get_page(slug: str) -> dict[str, str]:
    s = _validate_query(slug)
    if len(s) > 200 or ".." in s:
        raise GBrainError("invalid slug")
    raw = _clean_cli_noise(_run_gbrain(["get", s], timeout=30).stdout)
    return {"slug": s, "content": raw}


def list_files() -> dict[str, Any]:
    if not _brain_root().exists():
        raise GBrainError(f"brain root not found: {_brain_root()}")
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
            logger.debug("line.strip 失败（旁路，已忽略）", exc_info=True)
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
        raise GBrainError("file not found")
    size = target.stat().st_size
    if size > MAX_FILE_BYTES:
        raise GBrainError(f"file too large to edit ({size} bytes)")
    return {
        "path": str(target.relative_to(_brain_root())),
        "content": target.read_text(encoding="utf-8", errors="replace"),
        "size": size,
    }


def write_file(rel_path: str, content: str) -> dict[str, Any]:
    target = _safe_rel_path(rel_path)
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_WRITE_BYTES:
        raise GBrainError(f"file too large to save (>{MAX_WRITE_BYTES} bytes)")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    rel = str(target.relative_to(_brain_root()))
    return {"ok": True, "path": rel, "size": len(encoded)}


def delete_file(rel_path: str) -> dict[str, Any]:
    target = _safe_rel_path(rel_path)
    if not target.is_file():
        raise GBrainError("file not found")
    target.unlink()
    return {"ok": True, "path": rel_path}


def import_brain() -> dict[str, Any]:
    raw = _clean_cli_noise(_run_gbrain(["import", str(_brain_root()), "--no-embed"], timeout=120).stdout)
    # Track source changes in git without auto-committing user edits.
    git_status = _run_git(["status", "--short"], timeout=10).stdout.strip()
    return {"ok": True, "raw": raw, "git_status": git_status}


def git_status() -> dict[str, str]:
    r = _run_git(["status", "--short"], timeout=10)
    return {"status": r.stdout.strip(), "stderr": r.stderr.strip()}
