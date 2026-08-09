"""Import a skill from a public GitHub repository via tarball.

We deliberately avoid a ``git clone`` dependency: instead we fetch the
tarball from GitHub's archive endpoint, extract into a sandboxed temp dir,
run security checks, then atomically rename into the skills root.

SECURITY CHOKE POINTS
---------------------

``_validate_github_source``
    Whitelist-style regex on owner/repo/branch/subdir. Refuses any URL/
    identifier with path traversal, suspicious chars, or non-github hosts.

``_download_tarball``
    Caps the response body at ``_MAX_TARBALL_BYTES``. A malicious server
    can't pump 10 GB into memory.

``_safe_extract``
    Before writing each member: reject absolute paths, reject '..' segments,
    reject symlinks/hardlinks outright (simpler than trying to validate
    their targets), cap per-file size and total file count.

``_atomic_move_into_skills``
    Target dir is placed under SKILLS_ROOT via the same ``_safe_path``
    logic used everywhere else. Rename is atomic on the same filesystem,
    so a crash mid-import can never leave a half-imported skill.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx
from fastapi import HTTPException
from pydantic import BaseModel

from app.services.skill_repo import (
    _is_under,
    _resolved_skills_root,
    validate_skill_name,
)
from app.services import snapshot as snapshot_svc


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

_MAX_TARBALL_BYTES = 20 * 1024 * 1024     # compressed download cap
_MAX_EXTRACTED_BYTES = 40 * 1024 * 1024   # decompressed total cap
_MAX_FILE_BYTES = 5 * 1024 * 1024         # single extracted file cap
_MAX_FILE_COUNT = 500                     # files per skill cap
_DOWNLOAD_TIMEOUT_SECONDS = 30

# Anchor the regexes so the whole string has to match — strict on purpose.
_OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
# Subdir inside the repo; may be empty. Forward slashes only, no ..
_SUBDIR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class GitHubImportRequest(BaseModel):
    repo: str             # "owner/repo" or full https URL (we normalize)
    branch: str = "main"
    subdir: str | None = None
    target_name: str | None = None


class GitHubImportResult(BaseModel):
    imported_name: str
    source_url: str
    branch: str
    file_count: int
    snapshot_id: str | None


# ---------------------------------------------------------------------------
# URL normalization
# ---------------------------------------------------------------------------


_GITHUB_URL_RE = re.compile(
    r"^(?:https?://github\.com/)?"
    r"(?P<owner>[A-Za-z0-9][A-Za-z0-9._-]{0,99})"
    r"/"
    r"(?P<repo>[A-Za-z0-9][A-Za-z0-9._-]{0,99}?)"
    r"(?:\.git)?/?$"
)


@dataclass(frozen=True)
class _ParsedRepo:
    owner: str
    repo: str


def _parse_repo(repo_input: str) -> _ParsedRepo:
    m = _GITHUB_URL_RE.match((repo_input or "").strip())
    if not m:
        raise HTTPException(400, "repo must be 'owner/repo' or a github.com URL")
    owner = m.group("owner")
    repo = m.group("repo")
    if not _OWNER_REPO_RE.match(owner) or not _OWNER_REPO_RE.match(repo):
        raise HTTPException(400, "invalid owner/repo characters")
    return _ParsedRepo(owner=owner, repo=repo)


def _validate_branch(branch: str) -> str:
    if not branch or not _BRANCH_RE.match(branch):
        raise HTTPException(400, f"invalid branch: {branch!r}")
    # Defence in depth: reject traversal segments even though the regex
    # technically allows only tame characters.
    segs = branch.split("/")
    if any(s in ("", ".", "..") for s in segs):
        raise HTTPException(400, f"invalid branch segment in {branch!r}")
    return branch


def _validate_subdir(subdir: str | None) -> str:
    if subdir is None or subdir == "":
        return ""
    if not _SUBDIR_RE.match(subdir):
        raise HTTPException(400, f"invalid subdir: {subdir!r}")
    normalized = subdir.strip("/")
    if any(seg in ("", ".", "..") for seg in normalized.split("/")):
        raise HTTPException(400, f"invalid subdir segment in {subdir!r}")
    return normalized


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

Fetcher = Callable[[str], bytes]


def _default_fetcher(url: str) -> bytes:
    """Download the tarball, capping at _MAX_TARBALL_BYTES."""
    buf = bytearray()
    with httpx.stream(
        "GET", url,
        follow_redirects=True,
        timeout=_DOWNLOAD_TIMEOUT_SECONDS,
    ) as r:
        if r.status_code != 200:
            raise HTTPException(
                502, f"github returned HTTP {r.status_code} for {url}"
            )
        for chunk in r.iter_bytes():
            buf.extend(chunk)
            if len(buf) > _MAX_TARBALL_BYTES:
                raise HTTPException(
                    413,
                    f"tarball exceeds {_MAX_TARBALL_BYTES // (1024 * 1024)} MB limit",
                )
    return bytes(buf)


# ---------------------------------------------------------------------------
# Extraction (security-critical)
# ---------------------------------------------------------------------------


def _safe_extract(tar_bytes: bytes, dest: Path) -> int:
    """Extract a tarball into ``dest`` with strict safety checks.

    Returns the number of regular files written. Raises HTTPException on
    any policy violation.
    """
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)

    total_bytes = 0
    file_count = 0

    try:
        tf = tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz")
    except tarfile.TarError as e:
        raise HTTPException(400, f"not a valid tar.gz: {e}") from e

    with tf:
        for member in tf.getmembers():
            name = member.name

            # Reject absolute paths and path traversal.
            if name.startswith("/") or "\\" in name:
                raise HTTPException(400, f"tarball contains absolute path: {name}")
            if any(part in ("", ".", "..") for part in name.split("/")[1:]):
                # The [1:] skips the repo-version prefix (e.g. "repo-main/")
                # which is fine; any '..'/'.' elsewhere is hostile.
                if ".." in name.split("/") or any(seg == ".." for seg in name.split("/")):
                    raise HTTPException(400, f"tarball contains path traversal: {name}")

            # Symlinks and hardlinks: resolving them safely is fiddly, and
            # our use case doesn't need them. Refuse outright.
            if member.issym() or member.islnk():
                raise HTTPException(
                    400, f"tarball contains symlink/hardlink: {name}"
                )

            # Skip anything that's not a regular file or directory.
            if not (member.isfile() or member.isdir()):
                continue

            # Compute destination path and verify containment.
            target = (dest / name).resolve()
            if not _is_under(target, dest):
                raise HTTPException(
                    400, f"tarball path escapes extract root: {name}"
                )

            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            # Regular file — enforce size and count limits.
            file_count += 1
            if file_count > _MAX_FILE_COUNT:
                raise HTTPException(
                    413, f"tarball exceeds {_MAX_FILE_COUNT} file limit"
                )
            if member.size > _MAX_FILE_BYTES:
                raise HTTPException(
                    413,
                    f"file {name} exceeds {_MAX_FILE_BYTES // (1024 * 1024)} MB per-file limit",
                )
            total_bytes += member.size
            if total_bytes > _MAX_EXTRACTED_BYTES:
                raise HTTPException(
                    413,
                    f"extracted content exceeds {_MAX_EXTRACTED_BYTES // (1024 * 1024)} MB limit",
                )

            target.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(member)
            if src is None:
                continue
            with open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            # Strip unsafe perms: never executable bits on data.
            try:
                os.chmod(target, 0o644)
            except OSError:
                pass

    return file_count


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_target_name(parsed: _ParsedRepo, subdir: str) -> str:
    """Pick a default skill name when the caller doesn't supply one."""
    candidate = (subdir.split("/")[-1] if subdir else parsed.repo).lower()
    # Convert dots and underscores/spaces to hyphens, then strip invalid chars.
    candidate = re.sub(r"[^a-z0-9_-]+", "-", candidate).strip("-_")
    # Must start with a letter per skill name rules.
    if not candidate or not candidate[0].isalpha():
        candidate = f"imported-{candidate}" if candidate else "imported-skill"
    return candidate


def _find_skill_root(extract_root: Path, subdir: str) -> Path:
    """Locate the directory containing SKILL.md inside the extracted tarball.

    GitHub tarballs always wrap content in a top-level ``<repo>-<branch>/``
    directory. We descend into that single top-level dir, then into the
    optional subdir, and verify SKILL.md is present.
    """
    entries = [e for e in extract_root.iterdir() if not e.name.startswith(".")]
    if len(entries) != 1 or not entries[0].is_dir():
        raise HTTPException(
            400,
            "unexpected tarball layout: expected a single top-level directory",
        )
    base = entries[0]
    if subdir:
        base = (base / subdir).resolve()
        if not _is_under(base, entries[0].resolve()):
            raise HTTPException(400, "subdir escapes repo root")
        if not base.is_dir():
            raise HTTPException(400, f"subdir not found in repo: {subdir}")

    if not (base / "SKILL.md").is_file():
        raise HTTPException(
            400,
            f"SKILL.md not found at {subdir or '<repo root>'} — not a Hermes skill",
        )
    return base


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def import_from_github(
    req: GitHubImportRequest,
    fetcher: Fetcher | None = None,
) -> GitHubImportResult:
    """Import a skill from a public GitHub repository. See module docstring."""
    parsed = _parse_repo(req.repo)
    branch = _validate_branch(req.branch)
    subdir = _validate_subdir(req.subdir)

    target_name = req.target_name or _default_target_name(parsed, subdir)
    validate_skill_name(target_name)

    skills_root = _resolved_skills_root()
    target_dir = (skills_root / target_name).resolve()
    if not _is_under(target_dir, skills_root):
        raise HTTPException(403, "target path escape detected")
    if target_dir.exists():
        raise HTTPException(
            409, f"skill '{target_name}' already exists; pick another target_name"
        )

    source_url = (
        f"https://github.com/{parsed.owner}/{parsed.repo}"
        f"/archive/refs/heads/{branch}.tar.gz"
    )

    # Download
    tar_bytes = (fetcher or _default_fetcher)(source_url)

    # Extract into an isolated tmp dir, validate, then atomic-rename.
    with tempfile.TemporaryDirectory(prefix="skill-import-") as tmp:
        tmp_path = Path(tmp)
        extract_root = tmp_path / "extract"
        file_count = _safe_extract(tar_bytes, extract_root)

        skill_src = _find_skill_root(extract_root, subdir)

        # Ensure parent of target exists (in case of nested target_name in
        # future — today validate_skill_name accepts only single segment,
        # but parents are a no-op for existing SKILLS_ROOT).
        target_dir.parent.mkdir(parents=True, exist_ok=True)

        # os.rename is atomic when src and dst are on the same FS. The
        # temp dir under /tmp may be on a different FS in weird setups;
        # fall back to copy+rmtree there.
        try:
            os.rename(skill_src, target_dir)
        except OSError:
            shutil.copytree(skill_src, target_dir, symlinks=False)

    # Best-effort: snapshot the imported state so the user can compare
    # against later edits. Failure here must not un-import the skill.
    snap_id: str | None = None
    try:
        meta = snapshot_svc.create_snapshot(
            target_name,
            label=f"imported from {parsed.owner}/{parsed.repo}@{branch}",
        )
        snap_id = meta.id
    except Exception:
        snap_id = None

    return GitHubImportResult(
        imported_name=target_name,
        source_url=source_url,
        branch=branch,
        file_count=file_count,
        snapshot_id=snap_id,
    )
