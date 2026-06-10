"""Auto bug-fix service.

When a feature/tool operation fails (e.g. skill generation, visualization tool
build), the frontend offers to launch an AI repair flow. This module is the
backend engine for that flow. Design goals, in priority order:

  1. NEVER touch production source during diagnosis. hermes runs inside an
     isolated ``git worktree`` copy of the repo; only an explicit ``apply``
     (after the user reviews the diff) writes to the real working tree.
  2. NEVER leak resources. hermes runs as a *subprocess* (not in-process), so
     its memory dies with it. A single-flight lock allows at most one active
     job; a hard timeout kills a runaway process; worktrees are always removed.
  3. Be reversible. ``apply`` commits onto the real repo and records the
     previous SHA, so ``rollback`` is a single ``git reset --hard``.

The whole feature is gated by the ``autofix_enabled`` hub setting (default
off), so when disabled this module is never invoked and costs nothing.
"""
from __future__ import annotations

from app.core.proc import no_window_kwargs

import asyncio
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

# repo root: server/app/services/autofix_service.py -> parents[3] == IvyeaOps/
REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICE_UNIT = "ivyea-ops.service"

# Caps. Diagnosis is the only long-running step; keep a hard ceiling so a stuck
# hermes can never pin memory/CPU indefinitely.
_DIAGNOSE_TIMEOUT_S = 900
_DIFF_MAX_CHARS = 60_000
_LOG_TAIL_CHARS = 8_000
_WORKTREE_PARENT = Path("/tmp/ivyea-ops-autofix")


@dataclass
class Job:
    id: str
    error: Dict[str, Any]
    status: str = "running"  # running|diagnosed|applying|applied|failed|rejected|restarting
    summary: str = ""        # hermes' narrative (root cause + fix)
    diff: str = ""           # proposed git diff (capped)
    changed_files: list[str] = field(default_factory=list)
    needs_restart: bool = False   # any server/**.py changed
    needs_rebuild: bool = False   # any client/** changed
    error_detail: str = ""        # failure reason when status == failed
    pre_sha: str = ""             # repo HEAD before apply (for rollback)
    worktree: str = ""
    branch: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def public(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "summary": self.summary,
            "diff": self.diff,
            "changed_files": self.changed_files,
            "needs_restart": self.needs_restart,
            "needs_rebuild": self.needs_rebuild,
            "error": self.error,
            "error_detail": self.error_detail,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ── single-flight state ────────────────────────────────────────────────────
_active: Optional[Job] = None
_task: Optional[asyncio.Task] = None
_lock = asyncio.Lock()


def _git(*args: str, cwd: Path | str | None = None, timeout: int = 60) -> subprocess.CompletedProcess:
    # Resolve REPO_ROOT at call time (not as a default arg, which binds once at
    # definition) so tests can repoint REPO_ROOT and never touch the real repo.
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd if cwd is not None else REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        **no_window_kwargs(),
    )


def current_job() -> Optional[Dict[str, Any]]:
    return _active.public() if _active else None


def _is_busy() -> bool:
    return _active is not None and _active.status in ("running", "applying", "restarting")


# ── prompt ─────────────────────────────────────────────────────────────────
def _build_prompt(err: Dict[str, Any]) -> str:
    endpoint = err.get("endpoint") or err.get("url") or "(unknown)"
    method = err.get("method") or ""
    status = err.get("status") or ""
    detail = err.get("detail") or ""
    feature = err.get("feature") or ""
    return f"""你是 IvyeaOps 项目（FastAPI 后端 server/ + React 前端 client/）的维护工程师。
当前工作目录就是该项目的一个隔离副本，可以直接读写文件。

用户在使用功能时遇到报错，需要你定位根因并修复：

- 功能/板块: {feature}
- 失败接口: {method} {endpoint}
- HTTP 状态: {status}
- 错误信息: {detail}

要求：
1. 先在代码里定位这个接口的实现（后端通常在 server/app/routers 与 server/app/services），读懂相关逻辑。
2. 找到根因后，**只改必要的文件**做最小修复，不要大范围重构、不要改无关代码。
3. **禁止**启动任何服务器/uvicorn、**禁止**执行 systemctl/重启、**禁止**跑长时间命令或安装依赖。只做代码修改。
4. 如果根因无法从代码确定（例如依赖外部服务、数据缺失），不要乱改，直接说明你的判断。
5. 完成后用中文简要输出：根因是什么、你改了哪些文件、为什么这样改。

现在开始。"""


# ── diagnose ───────────────────────────────────────────────────────────────
async def start_diagnose(error: Dict[str, Any]) -> Dict[str, Any]:
    """Launch an isolated diagnosis. Refuses if a job is already active."""
    global _active, _task
    async with _lock:
        if _is_busy():
            raise RuntimeError("已有一个修复任务在进行中，请等待它完成")
        from app.services.runners import _find_bin
        if not _find_bin("hermes"):
            raise RuntimeError("hermes CLI 不可用，无法启动自动修复")
        job = Job(id=uuid.uuid4().hex[:12], error=error)
        _active = job
        _task = asyncio.create_task(_run_diagnose(job), name=f"autofix-{job.id}")
    return job.public()


async def _run_diagnose(job: Job) -> None:
    from app.services.runners import _find_bin, build_child_env

    wt = _WORKTREE_PARENT / job.id
    job.branch = f"autofix/{job.id}"
    try:
        _WORKTREE_PARENT.mkdir(parents=True, exist_ok=True)
        # Fresh isolated checkout of current HEAD on a throwaway branch.
        r = _git("worktree", "add", "-b", job.branch, str(wt), "HEAD")
        if r.returncode != 0:
            raise RuntimeError(f"创建隔离副本失败: {r.stderr.strip()[:300]}")
        job.worktree = str(wt)

        binary = _find_bin("hermes")
        env = build_child_env(binary)
        env["HERMES_YOLO_MODE"] = "1"        # auto-approve tool calls
        env["HERMES_ACCEPT_HOOKS"] = "1"
        env.setdefault("TERM", "dumb")
        env.setdefault("NO_COLOR", "1")

        prompt = _build_prompt(job.error)
        proc = await asyncio.create_subprocess_exec(
            binary, "-z", prompt,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(wt),
            env=env,
            **no_window_kwargs(),
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=_DIAGNOSE_TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            try:
                await asyncio.wait_for(proc.communicate(), timeout=5)
            except Exception:
                pass
            raise RuntimeError(f"修复超时（>{_DIAGNOSE_TIMEOUT_S}s），已终止")

        text = (out or b"").decode("utf-8", errors="replace")
        job.summary = text[-_LOG_TAIL_CHARS:].strip()

        # Collect what hermes changed in the isolated copy.
        _git("add", "-A", cwd=wt)
        diff = _git("diff", "--cached", cwd=wt).stdout
        names = _git("diff", "--cached", "--name-only", cwd=wt).stdout.strip()
        job.changed_files = [f for f in names.splitlines() if f.strip()]
        job.diff = diff[:_DIFF_MAX_CHARS] + ("\n…(diff 已截断)…" if len(diff) > _DIFF_MAX_CHARS else "")
        job.needs_restart = any(f.startswith("server/") and f.endswith(".py") for f in job.changed_files)
        job.needs_rebuild = any(f.startswith("client/") for f in job.changed_files)

        if not job.changed_files:
            job.status = "failed"
            job.error_detail = "hermes 未能修改任何文件（可能根因不在代码层面）。下方为它的分析。"
        else:
            job.status = "diagnosed"
    except Exception as exc:  # noqa: BLE001 — surface everything to the UI
        job.status = "failed"
        job.error_detail = str(exc)[:500]
    finally:
        job.updated_at = time.time()
        # Diagnosis is done either way; the diff is captured, so the worktree
        # is no longer needed. apply() works from the stored diff text.
        _cleanup_worktree(job)


def _cleanup_worktree(job: Job) -> None:
    if job.worktree:
        try:
            _git("worktree", "remove", "--force", job.worktree)
        except Exception:
            shutil.rmtree(job.worktree, ignore_errors=True)
        job.worktree = ""
    if job.branch:
        try:
            _git("branch", "-D", job.branch)
        except Exception:
            pass
        job.branch = ""


# ── apply ──────────────────────────────────────────────────────────────────
async def apply(job_id: str) -> Dict[str, Any]:
    """Apply the reviewed diff to the real working tree and commit it."""
    global _active
    job = _require(job_id)
    if job.status != "diagnosed":
        raise RuntimeError(f"当前状态 {job.status} 无法应用")
    if not job.diff.strip():
        raise RuntimeError("没有可应用的改动")
    job.status = "applying"
    job.updated_at = time.time()
    try:
        job.pre_sha = _git("rev-parse", "HEAD").stdout.strip()
        # The diff was generated against this same HEAD, so a plain apply
        # normally succeeds. If HEAD drifted, retry with --3way to merge.
        diff_text = job.diff if job.diff.endswith("\n") else job.diff + "\n"
        # Run in a thread so `git apply` (up to 60s) doesn't block the event loop.
        proc = await asyncio.to_thread(
            subprocess.run,
            ["git", "apply", "--whitespace=nowarn"],
            cwd=str(REPO_ROOT), input=diff_text, capture_output=True, text=True, timeout=60,
            **no_window_kwargs(),
        )
        if proc.returncode != 0:
            proc = await asyncio.to_thread(
                subprocess.run,
                ["git", "apply", "--3way", "--whitespace=nowarn"],
                cwd=str(REPO_ROOT), input=diff_text, capture_output=True, text=True, timeout=60,
                **no_window_kwargs(),
            )
        if proc.returncode != 0:
            raise RuntimeError(f"补丁应用失败: {(proc.stderr or proc.stdout).strip()[:400]}")
        _git("add", "-A")
        msg = f"autofix: {(job.error.get('feature') or job.error.get('endpoint') or 'fix')} [{job.id}]"
        c = _git("commit", "-m", msg)
        if c.returncode != 0 and "nothing to commit" not in (c.stdout + c.stderr):
            raise RuntimeError(f"提交失败: {c.stderr.strip()[:300]}")

        if job.needs_rebuild:
            await _rebuild_frontend()
        job.status = "applied"
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.error_detail = str(exc)[:500]
        raise
    finally:
        job.updated_at = time.time()
    return job.public()


async def _rebuild_frontend() -> None:
    client = REPO_ROOT / "client"
    proc = await asyncio.create_subprocess_exec(
        "npm", "run", "build",
        cwd=str(client),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        **no_window_kwargs(),
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("前端构建超时")
    if proc.returncode != 0:
        tail = (out or b"").decode("utf-8", errors="replace")[-1500:]
        raise RuntimeError(f"前端构建失败:\n{tail}")


# ── restart ────────────────────────────────────────────────────────────────
def restart(job_id: str) -> Dict[str, Any]:
    """Restart the systemd unit via a detached helper.

    The restart kills *this* process, so we spawn a fully detached shell that
    sleeps briefly (letting the HTTP response flush) then restarts the unit.
    """
    job = _require(job_id)
    if job.status not in ("applied", "failed"):
        raise RuntimeError(f"当前状态 {job.status} 无法重启")
    job.status = "restarting"
    job.updated_at = time.time()
    subprocess.Popen(
        ["bash", "-c", f"sleep 1; sudo systemctl restart {SERVICE_UNIT}"],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {"ok": True, "restarting": True}


# ── rollback ───────────────────────────────────────────────────────────────
async def rollback(job_id: str) -> Dict[str, Any]:
    """Revert an applied fix to the pre-apply SHA, rebuild if needed."""
    job = _require(job_id)
    if not job.pre_sha:
        raise RuntimeError("没有可回滚的提交记录")
    r = _git("reset", "--hard", job.pre_sha)
    if r.returncode != 0:
        raise RuntimeError(f"回滚失败: {r.stderr.strip()[:300]}")
    if job.needs_rebuild:
        await _rebuild_frontend()
    job.status = "rejected"
    job.error_detail = "已回滚到修复前版本"
    job.updated_at = time.time()
    return job.public()


# ── reject / clear ─────────────────────────────────────────────────────────
def reject(job_id: str) -> Dict[str, Any]:
    global _active
    job = _require(job_id)
    _cleanup_worktree(job)
    job.status = "rejected"
    job.updated_at = time.time()
    if _active and _active.id == job.id:
        _active = None
    return {"ok": True}


def _require(job_id: str) -> Job:
    if not _active or _active.id != job_id:
        raise KeyError("任务不存在或已失效")
    return _active
