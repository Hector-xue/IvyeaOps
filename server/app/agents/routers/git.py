"""Git API — port of claudecodeui's ``server/routes/git.js`` (mounted at ``/git``).

Subprocess-driven git with the same validation, path-resolution, and response
shapes as the Node version. GET endpoints return ``{error}`` with HTTP 200 (as
Node does); mutating POST endpoints return HTTP 500 ``{error[, details]}``.
``generate-commit-message`` reuses the native claude driver.
"""
from __future__ import annotations

from app.core.proc import no_window_kwargs

import asyncio
import logging
import os
import re
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.agents import repos
from app.agents.db import db_conn

logger = logging.getLogger("ivyea.agents.routers.git")

router = APIRouter()

_COMMIT_DIFF_CHAR_LIMIT = 500_000


class _GitError(Exception):
    def __init__(self, message: str, stdout: str = "", stderr: str = "", code: int = 1):
        super().__init__(message)
        self.stdout, self.stderr, self.code = stdout, stderr, code

    def details(self) -> str:
        return f"{self} {self.stderr} {self.stdout}"


async def _git(args: list[str], cwd: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        **no_window_kwargs())
    out, err = await proc.communicate()
    out_s, err_s = out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
    if proc.returncode != 0:
        raise _GitError(f"Command failed: git {' '.join(args)}", out_s, err_s, proc.returncode or 1)
    return out_s


def _err(status: int, **body) -> JSONResponse:
    return JSONResponse(status_code=status, content=body)


# --- validation / helpers (git.js) -----------------------------------------

def _validate_commit_ref(commit: str) -> str:
    if not re.match(r"^[a-zA-Z0-9._~^{}@/\-]+$", commit):
        raise _GitError("Invalid commit reference")
    return commit


def _validate_branch(branch: str) -> str:
    if not re.match(r"^[a-zA-Z0-9._/\-]+$", branch):
        raise _GitError("Invalid branch name")
    return branch


def _validate_remote(remote: str) -> str:
    if not re.match(r"^[a-zA-Z0-9._\-]+$", remote):
        raise _GitError("Invalid remote name")
    return remote


def _validate_file_path(file: str, project_path: Optional[str] = None) -> str:
    if not file or "\0" in file:
        raise _GitError("Invalid file path")
    if project_path:
        resolved = os.path.abspath(os.path.join(project_path, file))
        root = os.path.abspath(project_path)
        if not resolved.startswith(root + os.sep) and resolved != root:
            raise _GitError("Invalid file path: path traversal detected")
    return file


def _validate_project_path(project_path: str) -> str:
    if not project_path or "\0" in project_path:
        raise _GitError("Invalid project path")
    resolved = os.path.abspath(project_path)
    if resolved == "/" or resolved == os.sep:
        raise _GitError("Invalid project path: root directory not allowed")
    return resolved


def _actual_project_path(project_id: str) -> str:
    with db_conn() as conn:
        row = repos.get_project_by_id(conn, project_id)
    if not row:
        raise _GitError(f'Unable to resolve project path for "{project_id}"')
    return _validate_project_path(row["project_path"])


def _strip_diff_headers(diff: str) -> str:
    if not diff:
        return ""
    out, including = [], False
    for line in diff.split("\n"):
        if (line.startswith("diff --git") or line.startswith("index ")
                or line.startswith("new file mode") or line.startswith("deleted file mode")
                or line.startswith("---") or line.startswith("+++")):
            continue
        if line.startswith("@@") or including:
            including = True
            out.append(line)
    return "\n".join(out)


async def _validate_git_repo(project_path: str) -> None:
    if not os.path.exists(project_path):
        raise _GitError(f"Project path not found: {project_path}")
    try:
        out = await _git(["rev-parse", "--is-inside-work-tree"], project_path)
        if out.strip() != "true":
            raise _GitError("Not inside a git work tree")
        await _git(["rev-parse", "--show-toplevel"], project_path)
    except _GitError:
        raise _GitError('Not a git repository. This directory does not contain a .git folder. '
                        'Initialize a git repository with "git init" to use source control features.')


def _is_missing_head(e: _GitError) -> bool:
    d = e.details().lower()
    return any(s in d for s in ("unknown revision", "ambiguous argument",
                                "needed a single revision", "bad revision"))


async def _current_branch(project_path: str) -> str:
    try:
        out = await _git(["symbolic-ref", "--short", "HEAD"], project_path)
        if out.strip():
            return out.strip()
    except _GitError:
        logger.debug("out = await _git 失败（旁路，已忽略）", exc_info=True)
    return (await _git(["rev-parse", "--abbrev-ref", "HEAD"], project_path)).strip()


async def _has_commits(project_path: str) -> bool:
    try:
        await _git(["rev-parse", "--verify", "HEAD"], project_path)
        return True
    except _GitError as e:
        if _is_missing_head(e):
            return False
        raise


async def _repo_root(project_path: str) -> str:
    return (await _git(["rev-parse", "--show-toplevel"], project_path)).strip()


def _norm_rel(file_path: str) -> str:
    return re.sub(r"^/+", "", re.sub(r"^\./+", "", str(file_path).replace("\\", "/"))).strip()


def _parse_status_paths(status_output: str) -> list[str]:
    out = []
    for line in status_output.split("\n"):
        line = line.rstrip()
        if not line.strip():
            continue
        status_path = line[3:]
        renamed = status_path.split(" -> ")[1] if " -> " in status_path else None
        p = _norm_rel(renamed or status_path)
        if p:
            out.append(p)
    return out


def _file_path_candidates(project_path: str, repo_root: str, file_path: str) -> list[str]:
    normalized = _norm_rel(file_path)
    project_rel = _norm_rel(os.path.relpath(project_path, repo_root))
    candidates = [normalized]
    if project_rel and project_rel != "." and not normalized.startswith(project_rel + "/"):
        candidates.append(f"{project_rel}/{normalized}")
    seen, uniq = set(), []
    for c in candidates:
        if c and c not in seen:
            seen.add(c); uniq.append(c)
    return uniq


async def _resolve_repo_file(project_path: str, file_path: str) -> tuple[str, str]:
    _validate_file_path(file_path)
    repo_root = await _repo_root(project_path)
    candidates = _file_path_candidates(project_path, repo_root, file_path)
    for cand in candidates:
        out = await _git(["status", "--porcelain", "--", cand], repo_root)
        if out.strip():
            return repo_root, cand
    normalized = _norm_rel(file_path)
    if "/" not in normalized:
        status_out = await _git(["status", "--porcelain"], repo_root)
        changed = _parse_status_paths(status_out)
        suffix = [c for c in changed if c == normalized or c.endswith("/" + normalized)]
        if len(suffix) == 1:
            return repo_root, suffix[0]
    return repo_root, candidates[0]


# --- read endpoints (return {error} with 200 on failure) --------------------

@router.get("/status")
async def status(project: Optional[str] = None):
    if not project:
        return _err(400, error="Project id is required")
    try:
        pp = _actual_project_path(project)
        await _validate_git_repo(pp)
        branch = await _current_branch(pp)
        has_commits = await _has_commits(pp)
        status_out = await _git(["status", "--porcelain"], pp)
        modified, added, deleted, untracked = [], [], [], []
        for line in status_out.split("\n"):
            if not line.strip():
                continue
            st, file = line[:2], line[3:]
            if st in ("M ", " M", "MM"):
                modified.append(file)
            elif st in ("A ", "AM"):
                added.append(file)
            elif st in ("D ", " D"):
                deleted.append(file)
            elif st == "??":
                untracked.append(file)
        return {"branch": branch, "hasCommits": has_commits, "modified": modified,
                "added": added, "deleted": deleted, "untracked": untracked}
    except Exception as e:
        msg = str(e)
        is_not_repo = "not a git repository" in msg.lower()
        return {"error": msg if is_not_repo else "Git operation failed",
                "details": msg if is_not_repo else f"Failed to get git status: {msg}"}


@router.get("/diff")
async def diff(project: Optional[str] = None, file: Optional[str] = None):
    if not project or not file:
        return _err(400, error="Project id and file path are required")
    try:
        pp = _actual_project_path(project)
        await _validate_git_repo(pp)
        repo_root, rel = await _resolve_repo_file(pp, file)
        status_out = await _git(["status", "--porcelain", "--", rel], repo_root)
        is_untracked = status_out.startswith("??")
        is_deleted = status_out.strip().startswith("D ") or status_out.strip().startswith(" D")
        if is_untracked:
            fp = os.path.join(repo_root, rel)
            if os.path.isdir(fp):
                result = f"Directory: {rel}\n(Cannot show diff for directories)"
            else:
                with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                    lines = fh.read().split("\n")
                result = (f"--- /dev/null\n+++ b/{rel}\n@@ -0,0 +1,{len(lines)} @@\n"
                          + "\n".join("+" + ln for ln in lines))
        elif is_deleted:
            content = await _git(["show", f"HEAD:{rel}"], repo_root)
            lines = content.split("\n")
            result = (f"--- a/{rel}\n+++ /dev/null\n@@ -1,{len(lines)} +0,0 @@\n"
                      + "\n".join("-" + ln for ln in lines))
        else:
            unstaged = await _git(["diff", "--", rel], repo_root)
            if unstaged:
                result = _strip_diff_headers(unstaged)
            else:
                staged = await _git(["diff", "--cached", "--", rel], repo_root)
                result = _strip_diff_headers(staged) or ""
        return {"diff": result}
    except Exception as e:
        return {"error": str(e)}


@router.get("/file-with-diff")
async def file_with_diff(project: Optional[str] = None, file: Optional[str] = None):
    if not project or not file:
        return _err(400, error="Project id and file path are required")
    try:
        pp = _actual_project_path(project)
        await _validate_git_repo(pp)
        repo_root, rel = await _resolve_repo_file(pp, file)
        status_out = await _git(["status", "--porcelain", "--", rel], repo_root)
        is_untracked = status_out.startswith("??")
        is_deleted = status_out.strip().startswith("D ") or status_out.strip().startswith(" D")
        current_content, old_content = "", ""
        if is_deleted:
            head = await _git(["show", f"HEAD:{rel}"], repo_root)
            old_content = current_content = head
        else:
            fp = os.path.join(repo_root, rel)
            if os.path.isdir(fp):
                return _err(400, error="Cannot show diff for directories")
            with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                current_content = fh.read()
            if not is_untracked:
                try:
                    old_content = await _git(["show", f"HEAD:{rel}"], repo_root)
                except _GitError:
                    old_content = ""
        return {"currentContent": current_content, "oldContent": old_content,
                "isDeleted": is_deleted, "isUntracked": is_untracked}
    except Exception as e:
        return {"error": str(e)}


@router.get("/branches")
async def branches(project: Optional[str] = None):
    if not project:
        return _err(400, error="Project id is required")
    try:
        pp = _actual_project_path(project)
        await _validate_git_repo(pp)
        out = await _git(["branch", "-a"], pp)
        raw = [b.strip() for b in out.split("\n") if b.strip() and "->" not in b]
        local = [(b[2:] if b.startswith("* ") else b) for b in raw if not b.startswith("remotes/")]
        remote = [re.sub(r"^remotes/[^/]+/", "", b) for b in raw if b.startswith("remotes/")]
        remote = [r for r in remote if r not in local]
        merged, seen = [], set()
        for b in local + remote:
            if b not in seen:
                seen.add(b); merged.append(b)
        return {"branches": merged, "localBranches": local, "remoteBranches": remote}
    except Exception as e:
        return {"error": str(e)}


@router.get("/commits")
async def commits(project: Optional[str] = None, limit: int = 10):
    if not project:
        return _err(400, error="Project id is required")
    try:
        pp = _actual_project_path(project)
        await _validate_git_repo(pp)
        safe_limit = min(limit, 100) if isinstance(limit, int) and limit > 0 else 10
        out = await _git(["log", "--pretty=format:%H|%an|%ae|%ad|%s", "--date=iso-strict",
                          "-n", str(safe_limit)], pp)
        result = []
        for line in out.split("\n"):
            if not line.strip():
                continue
            parts = line.split("|")
            result.append({"hash": parts[0], "author": parts[1] if len(parts) > 1 else "",
                           "email": parts[2] if len(parts) > 2 else "",
                           "date": parts[3] if len(parts) > 3 else "",
                           "message": "|".join(parts[4:])})
        for commit in result:
            try:
                stats = await _git(["show", "--stat", "--format=", commit["hash"]], pp)
                commit["stats"] = stats.strip().split("\n")[-1] if stats.strip() else ""
            except _GitError:
                commit["stats"] = ""
        return {"commits": result}
    except Exception as e:
        return {"error": str(e)}


@router.get("/commit-diff")
async def commit_diff(project: Optional[str] = None, commit: Optional[str] = None):
    if not project or not commit:
        return _err(400, error="Project id and commit hash are required")
    try:
        pp = _actual_project_path(project)
        _validate_commit_ref(commit)
        out = await _git(["show", commit], pp)
        truncated = len(out) > _COMMIT_DIFF_CHAR_LIMIT
        result = (out[:_COMMIT_DIFF_CHAR_LIMIT] + "\n\n... Diff truncated to keep the UI responsive ..."
                  if truncated else out)
        return {"diff": result, "isTruncated": truncated}
    except Exception as e:
        return {"error": str(e)}


@router.get("/remote-status")
async def remote_status(project: Optional[str] = None):
    if not project:
        return _err(400, error="Project id is required")
    try:
        pp = _actual_project_path(project)
        await _validate_git_repo(pp)
        branch = await _current_branch(pp)
        has_commits = await _has_commits(pp)
        remotes = [r for r in (await _git(["remote"], pp)).strip().split("\n") if r.strip()]
        has_remote = len(remotes) > 0
        fallback = ("origin" if "origin" in remotes else remotes[0]) if has_remote else None
        if not has_commits:
            return {"hasRemote": has_remote, "hasUpstream": False, "branch": branch,
                    "remoteName": fallback, "ahead": 0, "behind": 0, "isUpToDate": False,
                    "message": "Repository has no commits yet"}
        try:
            tracking = (await _git(["rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}"], pp)).strip()
            remote_name = tracking.split("/")[0]
        except _GitError:
            return {"hasRemote": has_remote, "hasUpstream": False, "branch": branch,
                    "remoteName": fallback, "message": "No remote tracking branch configured"}
        count_out = (await _git(["rev-list", "--count", "--left-right", f"{tracking}...HEAD"], pp)).strip()
        behind, ahead = (int(x) for x in count_out.split("\t"))
        return {"hasRemote": True, "hasUpstream": True, "branch": branch, "remoteBranch": tracking,
                "remoteName": remote_name, "ahead": ahead or 0, "behind": behind or 0,
                "isUpToDate": ahead == 0 and behind == 0}
    except Exception as e:
        return {"error": str(e)}


# --- mutating endpoints (500 {error[,details]} on failure) ------------------

class _ProjBody(BaseModel):
    project: Optional[str] = None


class _BranchBody(BaseModel):
    project: Optional[str] = None
    branch: Optional[str] = None


class _CommitBody(BaseModel):
    project: Optional[str] = None
    message: Optional[str] = None
    files: Optional[list] = None


class _FileBody(BaseModel):
    project: Optional[str] = None
    file: Optional[str] = None


class _GenMsgBody(BaseModel):
    project: Optional[str] = None
    files: Optional[list] = None
    provider: str = "claude"


@router.post("/initial-commit")
async def initial_commit(body: _ProjBody):
    if not body.project:
        return _err(400, error="Project id is required")
    try:
        pp = _actual_project_path(body.project)
        await _validate_git_repo(pp)
        try:
            await _git(["rev-parse", "HEAD"], pp)
            return _err(400, error="Repository already has commits. Use regular commit instead.")
        except _GitError:
            logger.debug("_git 失败（旁路，已忽略）", exc_info=True)
        await _git(["add", "."], pp)
        out = await _git(["commit", "-m", "Initial commit"], pp)
        return {"success": True, "output": out, "message": "Initial commit created successfully"}
    except _GitError as e:
        if "nothing to commit" in e.details():
            return _err(400, error="Nothing to commit",
                        details="No files found in the repository. Add some files first.")
        return _err(500, error=str(e))


@router.post("/commit")
async def commit(body: _CommitBody):
    if not body.project or not body.message or not body.files:
        return _err(400, error="Project name, commit message, and files are required")
    try:
        pp = _actual_project_path(body.project)
        await _validate_git_repo(pp)
        repo_root = await _repo_root(pp)
        for f in body.files:
            _, rel = await _resolve_repo_file(pp, f)
            await _git(["add", "--", rel], repo_root)
        out = await _git(["commit", "-m", body.message], repo_root)
        return {"success": True, "output": out}
    except Exception as e:
        return _err(500, error=str(e))


@router.post("/revert-local-commit")
async def revert_local_commit(body: _ProjBody):
    if not body.project:
        return _err(400, error="Project id is required")
    try:
        pp = _actual_project_path(body.project)
        await _validate_git_repo(pp)
        try:
            await _git(["rev-parse", "--verify", "HEAD"], pp)
        except _GitError:
            return _err(400, error="No local commit to revert", details="This repository has no commit yet.")
        try:
            await _git(["reset", "--soft", "HEAD~1"], pp)
        except _GitError as e:
            d = e.details()
            if "HEAD~1" in d and ("unknown revision" in d or "ambiguous argument" in d):
                await _git(["update-ref", "-d", "HEAD"], pp)
            else:
                raise
        return {"success": True, "output": "Latest local commit reverted successfully. Changes were kept staged."}
    except Exception as e:
        return _err(500, error=str(e))


@router.post("/checkout")
async def checkout(body: _BranchBody):
    if not body.project or not body.branch:
        return _err(400, error="Project id and branch are required")
    try:
        pp = _actual_project_path(body.project)
        _validate_branch(body.branch)
        out = await _git(["checkout", body.branch], pp)
        return {"success": True, "output": out}
    except Exception as e:
        return _err(500, error=str(e))


@router.post("/create-branch")
async def create_branch(body: _BranchBody):
    if not body.project or not body.branch:
        return _err(400, error="Project id and branch name are required")
    try:
        pp = _actual_project_path(body.project)
        _validate_branch(body.branch)
        out = await _git(["checkout", "-b", body.branch], pp)
        return {"success": True, "output": out}
    except Exception as e:
        return _err(500, error=str(e))


@router.post("/delete-branch")
async def delete_branch(body: _BranchBody):
    if not body.project or not body.branch:
        return _err(400, error="Project id and branch name are required")
    try:
        pp = _actual_project_path(body.project)
        await _validate_git_repo(pp)
        current = (await _git(["branch", "--show-current"], pp)).strip()
        if current == body.branch:
            return _err(400, error="Cannot delete the currently checked-out branch")
        out = await _git(["branch", "-d", body.branch], pp)
        return {"success": True, "output": out}
    except Exception as e:
        return _err(500, error=str(e))


def _enhance(kind: str, msg: str) -> tuple[str, str]:
    """Map common git failure text to a friendly (error, details) pair."""
    table = {
        "Could not resolve hostname": ("Network error", "Unable to connect to remote repository. Check your internet connection."),
        "does not appear to be a git repository": ("Remote not configured", "No remote repository configured. Add a remote with: git remote add origin <url>"),
        "Permission denied": ("Authentication failed", "Permission denied. Check your credentials or SSH keys."),
    }
    for needle, (err, det) in table.items():
        if needle in msg:
            return err, det
    return kind, msg


@router.post("/fetch")
async def fetch(body: _ProjBody):
    if not body.project:
        return _err(400, error="Project id is required")
    try:
        pp = _actual_project_path(body.project)
        await _validate_git_repo(pp)
        branch = await _current_branch(pp)
        remote_name = "origin"
        try:
            remote_name = (await _git(["rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}"], pp)).strip().split("/")[0]
        except _GitError:
            logger.debug("remote_name = 失败（旁路，已忽略）", exc_info=True)
        _validate_remote(remote_name)
        out = await _git(["fetch", remote_name], pp)
        return {"success": True, "output": out or "Fetch completed successfully", "remoteName": remote_name}
    except Exception as e:
        _, details = _enhance("Fetch failed", str(e))
        return _err(500, error="Fetch failed", details=details)


@router.post("/pull")
async def pull(body: _ProjBody):
    if not body.project:
        return _err(400, error="Project id is required")
    try:
        pp = _actual_project_path(body.project)
        await _validate_git_repo(pp)
        branch = await _current_branch(pp)
        remote_name, remote_branch = "origin", branch
        try:
            tracking = (await _git(["rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}"], pp)).strip()
            remote_name = tracking.split("/")[0]
            remote_branch = "/".join(tracking.split("/")[1:])
        except _GitError:
            logger.debug("tracking = 失败（旁路，已忽略）", exc_info=True)
        _validate_remote(remote_name); _validate_branch(remote_branch)
        out = await _git(["pull", remote_name, remote_branch], pp)
        return {"success": True, "output": out or "Pull completed successfully",
                "remoteName": remote_name, "remoteBranch": remote_branch}
    except Exception as e:
        msg = str(getattr(e, "stderr", "") or "") + " " + str(e)
        if "CONFLICT" in msg:
            return _err(500, error="Merge conflicts detected", details="Pull created merge conflicts. Please resolve conflicts manually in the editor, then commit the changes.")
        if "Please commit your changes or stash them" in msg:
            return _err(500, error="Uncommitted changes detected", details="Please commit or stash your local changes before pulling.")
        if "diverged" in msg:
            return _err(500, error="Branches have diverged", details="Your local branch and remote branch have diverged. Consider fetching first to review changes.")
        err, details = _enhance("Pull failed", msg)
        return _err(500, error=err if err != "Pull failed" else "Pull failed", details=details)


@router.post("/push")
async def push(body: _ProjBody):
    if not body.project:
        return _err(400, error="Project id is required")
    try:
        pp = _actual_project_path(body.project)
        await _validate_git_repo(pp)
        branch = await _current_branch(pp)
        remote_name, remote_branch = "origin", branch
        try:
            tracking = (await _git(["rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}"], pp)).strip()
            remote_name = tracking.split("/")[0]
            remote_branch = "/".join(tracking.split("/")[1:])
        except _GitError:
            logger.debug("tracking = 失败（旁路，已忽略）", exc_info=True)
        _validate_remote(remote_name); _validate_branch(remote_branch)
        out = await _git(["push", remote_name, remote_branch], pp)
        return {"success": True, "output": out or "Push completed successfully",
                "remoteName": remote_name, "remoteBranch": remote_branch}
    except Exception as e:
        msg = str(getattr(e, "stderr", "") or "") + " " + str(e)
        if "rejected" in msg:
            return _err(500, error="Push rejected", details="The remote has newer commits. Pull first to merge changes before pushing.")
        if "non-fast-forward" in msg:
            return _err(500, error="Non-fast-forward push", details="Your branch is behind the remote. Pull the latest changes first.")
        if "no upstream branch" in msg:
            return _err(500, error="No upstream branch", details="No upstream branch configured. Use: git push --set-upstream origin <branch>")
        err, details = _enhance("Push failed", msg)
        return _err(500, error=err if err != "Push failed" else "Push failed", details=details)


@router.post("/publish")
async def publish(body: _BranchBody):
    if not body.project or not body.branch:
        return _err(400, error="Project id and branch are required")
    try:
        pp = _actual_project_path(body.project)
        await _validate_git_repo(pp)
        _validate_branch(body.branch)
        current = await _current_branch(pp)
        if current != body.branch:
            return _err(400, error=f"Branch mismatch. Current branch is {current}, but trying to publish {body.branch}")
        remotes = [r for r in (await _git(["remote"], pp)).strip().split("\n") if r.strip()]
        if not remotes:
            return _err(400, error="No remote repository configured. Add a remote with: git remote add origin <url>")
        remote_name = "origin" if "origin" in remotes else remotes[0]
        _validate_remote(remote_name)
        out = await _git(["push", "--set-upstream", remote_name, body.branch], pp)
        return {"success": True, "output": out or "Branch published successfully",
                "remoteName": remote_name, "branch": body.branch}
    except Exception as e:
        err, details = _enhance("Publish failed", str(e))
        return _err(500, error=err if err != "Publish failed" else "Publish failed", details=details)


@router.post("/discard")
async def discard(body: _FileBody):
    if not body.project or not body.file:
        return _err(400, error="Project id and file path are required")
    try:
        pp = _actual_project_path(body.project)
        await _validate_git_repo(pp)
        repo_root, rel = await _resolve_repo_file(pp, body.file)
        status_out = await _git(["status", "--porcelain", "--", rel], repo_root)
        if not status_out.strip():
            return _err(400, error="No changes to discard for this file")
        st = status_out[:2]
        fp = os.path.join(repo_root, rel)
        if st == "??":
            import shutil
            if os.path.isdir(fp):
                shutil.rmtree(fp, ignore_errors=True)
            else:
                os.unlink(fp)
        elif "M" in st or "D" in st:
            await _git(["restore", "--", rel], repo_root)
        elif "A" in st:
            await _git(["reset", "HEAD", "--", rel], repo_root)
        return {"success": True, "message": f"Changes discarded for {rel}"}
    except Exception as e:
        return _err(500, error=str(e))


@router.post("/delete-untracked")
async def delete_untracked(body: _FileBody):
    if not body.project or not body.file:
        return _err(400, error="Project id and file path are required")
    try:
        pp = _actual_project_path(body.project)
        await _validate_git_repo(pp)
        repo_root, rel = await _resolve_repo_file(pp, body.file)
        status_out = await _git(["status", "--porcelain", "--", rel], repo_root)
        if not status_out.strip():
            return _err(400, error="File is not untracked or does not exist")
        if status_out[:2] != "??":
            return _err(400, error="File is not untracked. Use discard for tracked files.")
        fp = os.path.join(repo_root, rel)
        if os.path.isdir(fp):
            import shutil
            shutil.rmtree(fp, ignore_errors=True)
            return {"success": True, "message": f"Untracked directory {rel} deleted successfully"}
        os.unlink(fp)
        return {"success": True, "message": f"Untracked file {rel} deleted successfully"}
    except Exception as e:
        return _err(500, error=str(e))


# --- AI commit message ------------------------------------------------------

def _clean_commit_message(text: str) -> str:
    if not text or not text.strip():
        return ""
    cleaned = text.strip()
    cleaned = re.sub(r"```[a-z]*\n", "", cleaned)
    cleaned = cleaned.replace("```", "")
    cleaned = re.sub(r"^#+\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r'^["\']|["\']$', "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    m = re.search(r"(feat|fix|docs|style|refactor|perf|test|build|ci|chore)(\(.+?\))?:.+", cleaned, re.DOTALL)
    if m:
        cleaned = cleaned[cleaned.index(m.group(0)):]
    return cleaned.strip()


@router.post("/generate-commit-message")
async def generate_commit_message(body: _GenMsgBody):
    if not body.project or not body.files:
        return _err(400, error="Project id and files are required")
    if body.provider not in ("claude", "cursor"):
        return _err(400, error='provider must be "claude" or "cursor"')
    try:
        pp = _actual_project_path(body.project)
        await _validate_git_repo(pp)
        repo_root = await _repo_root(pp)
        diff_context = ""
        for f in body.files:
            try:
                _, rel = await _resolve_repo_file(pp, f)
                out = await _git(["diff", "HEAD", "--", rel], repo_root)
                if out:
                    diff_context += f"\n--- {rel} ---\n{out}"
            except Exception:
                logger.debug("_ 失败（旁路，已忽略）", exc_info=True)
        if not diff_context.strip():
            for f in body.files:
                try:
                    _, rel = await _resolve_repo_file(pp, f)
                    fp = os.path.join(repo_root, rel)
                    if os.path.isdir(fp):
                        diff_context += f"\n--- {rel} (new directory) ---\n"
                    else:
                        with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                            diff_context += f"\n--- {rel} (new file) ---\n{fh.read()[:1000]}\n"
                except Exception:
                    logger.debug("_ 失败（旁路，已忽略）", exc_info=True)
        message = await _generate_with_ai(body.files, diff_context, pp)
        return {"message": message}
    except Exception as e:
        return _err(500, error=str(e))


async def _generate_with_ai(files: list, diff_context: str, project_path: str) -> str:
    prompt = (
        "Generate a conventional commit message for these changes.\n\n"
        "REQUIREMENTS:\n- Format: type(scope): subject\n- Include body explaining what changed and why\n"
        "- Types: feat, fix, docs, style, refactor, perf, test, build, ci, chore\n"
        "- Subject under 50 chars, body wrapped at 72 chars\n"
        "- Focus on user-facing changes, not implementation details\n- Consider what's being added AND removed\n"
        "- Return ONLY the commit message (no markdown, explanations, or code blocks)\n\n"
        "FILES CHANGED:\n" + "\n".join(f"- {f}" for f in files) + "\n\n"
        "DIFFS:\n" + diff_context[:4000] + "\n\nGenerate the commit message:")
    collected: list[str] = []

    class _W:
        def update_ws(self, ws): pass
        def set_session_id(self, sid): pass
        async def send(self, m):
            if m.get("kind") == "text" and m.get("role") == "assistant":
                collected.append(m.get("content") or "")

    try:
        from app.agents import claude_driver
        await claude_driver.query_claude(prompt, {
            "cwd": project_path, "permissionMode": "bypassPermissions", "model": "sonnet",
            "toolsSettings": {"skipPermissions": True}}, _W())
        return _clean_commit_message("".join(collected)) or "chore: update files"
    except Exception:
        n = len(files)
        return f"chore: update {n} file{'s' if n != 1 else ''}"
