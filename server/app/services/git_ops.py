"""Thin wrappers around the ``git`` CLI, scoped to a project's cwd.

Everything here resolves the project via projects.get_project so the path
is always the canonical workdir (and confined to the user's known set of
projects). Subprocess output is capped so a huge diff or a stuck command
can't take the server down.

We intentionally do NOT implement push/pull/merge/rebase here — the goal
is to give the user a usable "status + diff + stage + commit" loop, not
a full git client. The full client lives in the terminal tab.
"""
from __future__ import annotations

from app.core.proc import no_window_kwargs

import subprocess
from pathlib import Path
from typing import Any

from app.services import projects as proj_svc


# Hard limits so a runaway command can't OOM the server.
_GIT_TIMEOUT_S = 8.0           # plenty for status/diff/log; commit is fast too
_DIFF_MAX_BYTES = 256 * 1024   # 256 KB per file is more than any human reads


class GitError(RuntimeError):
    """Raised for any user-visible git failure; carries a short message."""


def _project_cwd(project_id: str) -> Path:
    p = proj_svc.get_project(project_id)
    if p is None:
        raise GitError(f"项目不存在: {project_id}")
    if p.path == "(unknown)":
        raise GitError("该项目没有有效的 cwd")
    cwd = Path(p.path)
    if not cwd.is_dir():
        raise GitError(f"项目目录不存在: {p.path}")
    return cwd


def _run_git(cwd: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    try:
        cp = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            env={"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "LANG": "C"},
            **no_window_kwargs(),
        )
    except FileNotFoundError as e:
        raise GitError("git 命令不可用，请安装 git") from e
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git {args[0]} 超时（>{_GIT_TIMEOUT_S}s）") from e
    if check and cp.returncode != 0:
        # Strip git's leading "fatal: " for cleaner UI messages
        err = (cp.stderr or cp.stdout or "git error").strip()
        if err.startswith("fatal: "):
            err = err[len("fatal: "):]
        raise GitError(err[:200])
    return cp


def is_repo(cwd: Path) -> bool:
    try:
        cp = _run_git(cwd, ["rev-parse", "--is-inside-work-tree"], check=False)
        return cp.returncode == 0 and cp.stdout.strip() == "true"
    except GitError:
        return False


# ─── Status ─────────────────────────────────────────────────────────────────

# Mapping from `git status --porcelain` XY two-char codes to a short label
# the frontend renders as a colored badge.
_STATUS_LABEL = {
    "M": "modified",
    "A": "added",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "U": "unmerged",
    "?": "untracked",
    "!": "ignored",
    " ": None,
}


def get_status(project_id: str) -> dict[str, Any]:
    cwd = _project_cwd(project_id)
    if not is_repo(cwd):
        return {
            "is_repo": False,
            "path": str(cwd),
            "error": "该目录不是 git 仓库",
            "branch": None,
            "ahead": 0,
            "behind": 0,
            "files": [],
        }

    # Branch + tracking info
    branch = ""
    ahead = behind = 0
    try:
        cp = _run_git(cwd, ["status", "--porcelain=v2", "--branch", "-z"], check=True)
        records = cp.stdout.split("\x00")
        files: list[dict[str, Any]] = []
        i = 0
        while i < len(records):
            line = records[i]
            if not line:
                i += 1
                continue
            if line.startswith("# branch.head "):
                branch = line[len("# branch.head "):].strip()
            elif line.startswith("# branch.ab "):
                rest = line[len("# branch.ab "):].strip()
                parts = rest.split()
                # Format: "+<ahead> -<behind>"
                for p in parts:
                    if p.startswith("+"):
                        try: ahead = int(p[1:])
                        except: pass
                    elif p.startswith("-"):
                        try: behind = int(p[1:])
                        except: pass
            elif line.startswith("1 "):
                # 1 XY sub mH mI mW hH hI path
                tokens = line.split(" ", 8)
                if len(tokens) >= 9:
                    xy = tokens[1]
                    path = tokens[8]
                    files.append(_xy_to_entry(xy, path))
            elif line.startswith("2 "):
                # 2 XY ... origPath  (renamed); orig path comes in NEXT record
                tokens = line.split(" ", 9)
                if len(tokens) >= 10:
                    xy = tokens[1]
                    path = tokens[9]
                    # The original path is the next NUL-separated record
                    orig = records[i + 1] if i + 1 < len(records) else ""
                    i += 1
                    entry = _xy_to_entry(xy, path)
                    entry["from"] = orig
                    files.append(entry)
            elif line.startswith("? "):
                files.append({
                    "path": line[2:],
                    "status": "?",
                    "label": "untracked",
                    "staged": False,
                    "unstaged": True,
                })
            elif line.startswith("u "):
                # Unmerged; we treat as both staged+unstaged conflict
                tokens = line.split(" ", 10)
                if len(tokens) >= 11:
                    path = tokens[10]
                    files.append({
                        "path": path,
                        "status": "U",
                        "label": "unmerged",
                        "staged": False,
                        "unstaged": True,
                    })
            # skip "# stash", "! ignored", anything else
            i += 1
    except GitError as e:
        return {
            "is_repo": True,
            "path": str(cwd),
            "error": str(e),
            "branch": branch or "",
            "ahead": ahead,
            "behind": behind,
            "files": [],
        }
    return {
        "is_repo": True,
        "path": str(cwd),
        "error": None,
        "branch": branch,
        "ahead": ahead,
        "behind": behind,
        "files": files,
    }


def _xy_to_entry(xy: str, path: str) -> dict[str, Any]:
    """Map XY into staged/unstaged flags + a label. XY is index-then-worktree.

    ``git status --porcelain=v2`` uses ``.`` (not space) to mean "no
    change in this column", so both ``.`` and the regular space must be
    treated as unchanged.
    """
    x = xy[0] if len(xy) >= 1 else "."
    y = xy[1] if len(xy) >= 2 else "."
    unchanged = (".", " ")
    staged = x not in unchanged and x != "?"
    unstaged = y not in unchanged and y != "?"
    # Prefer the worktree change for label when both are set (more relevant
    # to what the user is actively editing).
    primary = y if unstaged else x
    return {
        "path": path,
        "status": primary,
        "label": _STATUS_LABEL.get(primary, primary),
        "staged": staged,
        "unstaged": unstaged,
        "xy": xy,
    }


# ─── Diff ───────────────────────────────────────────────────────────────────

def get_diff(project_id: str, file: str, *, staged: bool = False) -> dict[str, Any]:
    cwd = _project_cwd(project_id)
    if not is_repo(cwd):
        raise GitError("不是 git 仓库")
    # Reject ../ traversal
    resolved = (cwd / file).resolve()
    if not str(resolved).startswith(str(cwd.resolve())):
        raise GitError("路径越界")
    args = ["diff", "--no-color", "-U3"]
    if staged:
        args.append("--cached")
    args.extend(["--", file])
    cp = _run_git(cwd, args, check=False)
    if cp.returncode != 0 and cp.returncode != 1:
        # git diff returns 1 when there ARE changes; 0 when none; >1 = error.
        raise GitError(cp.stderr.strip() or "git diff failed")
    diff = cp.stdout or ""
    truncated = False
    if len(diff) > _DIFF_MAX_BYTES:
        diff = diff[:_DIFF_MAX_BYTES] + "\n…(diff 超过 256KB 被截断)…\n"
        truncated = True
    return {
        "file": file,
        "staged": staged,
        "diff": diff,
        "truncated": truncated,
    }


# ─── Stage / Unstage / Discard ──────────────────────────────────────────────

def stage(project_id: str, paths: list[str]) -> dict[str, Any]:
    cwd = _project_cwd(project_id)
    if not is_repo(cwd):
        raise GitError("不是 git 仓库")
    if not paths:
        return {"ok": True, "staged": []}
    # Safety: reject any path with .. or absolute path or starting with /
    safe_paths = _validate_paths(paths)
    _run_git(cwd, ["add", "--", *safe_paths])
    return {"ok": True, "staged": safe_paths}


def unstage(project_id: str, paths: list[str]) -> dict[str, Any]:
    cwd = _project_cwd(project_id)
    if not is_repo(cwd):
        raise GitError("不是 git 仓库")
    if not paths:
        return {"ok": True, "unstaged": []}
    safe = _validate_paths(paths)
    # `git restore --staged` is the modern form; falls back to reset HEAD if old git.
    cp = _run_git(cwd, ["restore", "--staged", "--", *safe], check=False)
    if cp.returncode != 0:
        _run_git(cwd, ["reset", "HEAD", "--", *safe])
    return {"ok": True, "unstaged": safe}


def discard(project_id: str, paths: list[str]) -> dict[str, Any]:
    """Discard working-tree changes for tracked files. Untracked files
    deleted only if the user explicitly opts in via "delete_untracked"
    (which we don't currently expose) — safer that way."""
    cwd = _project_cwd(project_id)
    if not is_repo(cwd):
        raise GitError("不是 git 仓库")
    if not paths:
        return {"ok": True, "discarded": []}
    safe = _validate_paths(paths)
    _run_git(cwd, ["checkout", "--", *safe])
    return {"ok": True, "discarded": safe}


def _validate_paths(paths: list[str]) -> list[str]:
    out = []
    for p in paths:
        p = p.strip()
        if not p:
            continue
        if p.startswith("/") or ".." in p.split("/"):
            raise GitError(f"非法路径: {p}")
        out.append(p)
    if not out:
        raise GitError("paths 为空")
    return out


# ─── Commit ─────────────────────────────────────────────────────────────────

def commit(project_id: str, message: str, *, allow_empty: bool = False) -> dict[str, Any]:
    cwd = _project_cwd(project_id)
    if not is_repo(cwd):
        raise GitError("不是 git 仓库")
    msg = (message or "").strip()
    if not msg:
        raise GitError("提交信息为空")
    args = ["commit", "-m", msg]
    if allow_empty:
        args.append("--allow-empty")
    cp = _run_git(cwd, args, check=False)
    from app.core import audit as _audit
    _audit.record("git", "commit", target=f"{project_id}: {msg[:120]}",
                  outcome="ok" if cp.returncode == 0 else "failed")
    if cp.returncode != 0:
        err = (cp.stderr or cp.stdout or "").strip()
        # Most common case: nothing to commit
        if "nothing to commit" in err or "no changes added" in err:
            raise GitError("没有已暂存的改动，无法提交")
        raise GitError(err[:200] or "git commit failed")
    # Report the new HEAD sha.
    sha_cp = _run_git(cwd, ["rev-parse", "HEAD"], check=False)
    return {"ok": True, "sha": sha_cp.stdout.strip()[:12], "message": msg}


# ─── Log ────────────────────────────────────────────────────────────────────

_LOG_FORMAT = "%h\x1f%an\x1f%ar\x1f%s"


def get_log(project_id: str, limit: int = 20) -> dict[str, Any]:
    cwd = _project_cwd(project_id)
    if not is_repo(cwd):
        raise GitError("不是 git 仓库")
    limit = max(1, min(200, limit))
    cp = _run_git(cwd, ["log", f"-n{limit}", f"--pretty=format:{_LOG_FORMAT}"], check=False)
    if cp.returncode != 0:
        # Empty repo (no commits) returns non-zero; treat as empty list.
        return {"commits": []}
    commits = []
    for line in cp.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) >= 4:
            commits.append({
                "sha": parts[0],
                "author": parts[1],
                "when": parts[2],
                "subject": parts[3],
            })
    return {"commits": commits}


# ─── Branches ───────────────────────────────────────────────────────────────

def _validate_branch_name(name: str) -> str:
    name = (name or "").strip()
    bad = (" ", "~", "^", ":", "?", "*", "[", "\\", "..")
    if not name or name.startswith("-") or any(c in name for c in bad):
        raise GitError(f"非法分支名: {name or '(空)'}")
    return name


def list_branches(project_id: str) -> dict[str, Any]:
    cwd = _project_cwd(project_id)
    if not is_repo(cwd):
        raise GitError("不是 git 仓库")
    cp = _run_git(cwd, ["branch", "--format=%(refname:short)"], check=False)
    branches = [b.strip() for b in cp.stdout.splitlines() if b.strip()]
    head = _run_git(cwd, ["rev-parse", "--abbrev-ref", "HEAD"], check=False)
    current = head.stdout.strip() if head.returncode == 0 else ""
    return {"branches": branches, "current": current}


def checkout_branch(project_id: str, name: str) -> dict[str, Any]:
    cwd = _project_cwd(project_id)
    if not is_repo(cwd):
        raise GitError("不是 git 仓库")
    name = _validate_branch_name(name)
    cp = _run_git(cwd, ["checkout", name], check=False)
    from app.core import audit as _audit
    _audit.record("git", "checkout", target=f"{project_id}: {name}",
                  outcome="ok" if cp.returncode == 0 else "failed")
    if cp.returncode != 0:
        # Most common: uncommitted changes would be overwritten.
        raise GitError((cp.stderr or cp.stdout or "切换分支失败").strip()[:300])
    return {"ok": True, "current": name}


def create_branch(project_id: str, name: str) -> dict[str, Any]:
    cwd = _project_cwd(project_id)
    if not is_repo(cwd):
        raise GitError("不是 git 仓库")
    name = _validate_branch_name(name)
    cp = _run_git(cwd, ["checkout", "-b", name], check=False)
    if cp.returncode != 0:
        raise GitError((cp.stderr or cp.stdout or "创建分支失败").strip()[:300])
    return {"ok": True, "current": name}
