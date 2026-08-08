"""Skill snapshots: point-in-time directory copies + diff + restore.

Snapshots live under ``STUDIO_ROOT/snapshots/<skill_name>/<snapshot_id>/``
and are completely independent of ``SKILLS_ROOT``. A sidecar
``.snapshot.json`` inside each snapshot records the label and creation time.

SNAPSHOT ID FORMAT
------------------
``YYYYMMDD_HHMMSS_<6hex>``. The hex suffix avoids collisions when two
snapshots land in the same second. We validate this format strictly before
any filesystem operation so snapshot_id is a safe path component.

RESTORE ATOMICITY
-----------------
Restore is not a single-syscall atomic operation (it's a copytree), but we
make best-effort safety:

  1. Auto-create a ``pre-restore`` snapshot of the current state (unless the
     caller opts out).
  2. Copy the target snapshot into a sibling temp dir ``<skill>.restoring``.
  3. Move current ``<skill>`` to ``<skill>.old`` (atomic rename).
  4. Move ``<skill>.restoring`` to ``<skill>`` (atomic rename).
  5. Remove ``<skill>.old`` in the background.

If step 2 or 3 fails, the original is untouched. If step 4 fails
mid-flight the pre-restore snapshot is the user's recovery path.
"""
from __future__ import annotations

import json
import re
import secrets
import shutil
from dataclasses import dataclass
from datetime import datetime
from difflib import unified_diff
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel

from app.core.skill_paths import SNAPSHOTS_DIR
from app.services.skill_repo import (
    _is_under,
    _resolved_skills_root,
    validate_skill_name,
)


# ---------------------------------------------------------------------------
# Snapshot ID
# ---------------------------------------------------------------------------

_SNAPSHOT_ID_RE = re.compile(r"^\d{8}_\d{6}_[0-9a-f]{6}$")

_SIDECAR = ".snapshot.json"
_DEFAULT_RETENTION = 20  # keep last N snapshots per skill


def _new_snapshot_id() -> str:
    return (
        datetime.now().strftime("%Y%m%d_%H%M%S")
        + "_"
        + secrets.token_hex(3)
    )


def _validate_snapshot_id(sid: str) -> None:
    if not sid or not isinstance(sid, str) or not _SNAPSHOT_ID_RE.match(sid):
        raise HTTPException(400, f"invalid snapshot id: {sid!r}")


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _resolved_snapshots_root() -> Path:
    return SNAPSHOTS_DIR.resolve()


def _skill_snapshots_dir(name: str) -> Path:
    """Directory holding all snapshots for a given skill; created on demand."""
    validate_skill_name(name)
    root = _resolved_snapshots_root()
    d = (root / name).resolve()
    if not _is_under(d, root):
        raise HTTPException(403, "snapshot path escape detected")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _snapshot_dir(name: str, snapshot_id: str) -> Path:
    _validate_snapshot_id(snapshot_id)
    base = _skill_snapshots_dir(name)
    d = (base / snapshot_id).resolve()
    if not _is_under(d, base):
        raise HTTPException(403, "snapshot path escape detected")
    if not d.is_dir():
        raise HTTPException(404, f"snapshot not found: {snapshot_id}")
    return d


def _skill_dir(name: str) -> Path:
    """Resolve the live skill dir; distinct from skill_repo._safe_skill_dir
    because we *sometimes* need the path even when the skill doesn't exist
    (e.g. restoring after a destructive op). Here we require existence."""
    validate_skill_name(name)
    root = _resolved_skills_root()
    d = (root / name).resolve()
    if not _is_under(d, root):
        raise HTTPException(403, "skill path escape detected")
    if not d.is_dir():
        raise HTTPException(404, f"skill not found: {name}")
    return d


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class SnapshotMeta(BaseModel):
    id: str
    label: str | None
    created_at: datetime
    size_bytes: int
    file_count: int


class DiffFile(BaseModel):
    path: str
    status: str          # "added" | "modified" | "deleted"
    diff: str            # empty for binary / very large files


class SnapshotDiff(BaseModel):
    snapshot_id: str
    files: list[DiffFile]


# ---------------------------------------------------------------------------
# Create / list / delete
# ---------------------------------------------------------------------------


def _read_sidecar(snap_dir: Path) -> dict:
    sc = snap_dir / _SIDECAR
    if not sc.is_file():
        return {}
    try:
        return json.loads(sc.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _dir_stats_excluding_sidecar(directory: Path) -> tuple[int, int]:
    total = 0
    count = 0
    for p in directory.rglob("*"):
        if p.is_file() and p.name != _SIDECAR:
            try:
                total += p.stat().st_size
                count += 1
            except OSError:
                pass
    return total, count


def create_snapshot(name: str, label: str | None = None) -> SnapshotMeta:
    """Snapshot the current state of ``name`` into the studio snapshots dir."""
    src = _skill_dir(name)
    base = _skill_snapshots_dir(name)

    sid = _new_snapshot_id()
    # Loop only a couple of times for the unlikely collision case.
    for _ in range(3):
        dst = base / sid
        if not dst.exists():
            break
        sid = _new_snapshot_id()
    else:
        raise HTTPException(500, "could not allocate snapshot id")

    # symlinks=False collapses any symlink into its real target so the
    # snapshot is self-contained and can't reintroduce escapes on restore.
    # ignore_dangling_symlinks=True means dangling links are silently
    # dropped rather than raising.
    shutil.copytree(
        src, dst, symlinks=False, ignore_dangling_symlinks=True
    )

    # Microsecond precision matters: rapid-fire snapshots (tests, batch
    # restores) within the same second must still sort deterministically,
    # otherwise prune() can't reliably pick "the newest".
    sidecar = {
        "id": sid,
        "label": label,
        "created_at": datetime.now().isoformat(),
        "skill_name": name,
    }
    (dst / _SIDECAR).write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    prune(name)  # enforce retention; best-effort, errors swallowed below
    return _meta_for(dst)


def _meta_for(snap_dir: Path) -> SnapshotMeta:
    sidecar = _read_sidecar(snap_dir)
    size, count = _dir_stats_excluding_sidecar(snap_dir)
    created = sidecar.get("created_at")
    try:
        created_at = datetime.fromisoformat(created) if created else datetime.fromtimestamp(
            snap_dir.stat().st_mtime
        )
    except ValueError:
        created_at = datetime.fromtimestamp(snap_dir.stat().st_mtime)
    return SnapshotMeta(
        id=snap_dir.name,
        label=sidecar.get("label"),
        created_at=created_at,
        size_bytes=size,
        file_count=count,
    )


def list_snapshots(name: str) -> list[SnapshotMeta]:
    base = _skill_snapshots_dir(name)
    metas: list[SnapshotMeta] = []
    for d in base.iterdir():
        if d.is_dir() and _SNAPSHOT_ID_RE.match(d.name):
            metas.append(_meta_for(d))
    metas.sort(key=lambda m: m.created_at, reverse=True)
    return metas


def delete_snapshot(name: str, snapshot_id: str) -> None:
    d = _snapshot_dir(name, snapshot_id)
    shutil.rmtree(d, ignore_errors=False)


def prune(name: str, retention: int = _DEFAULT_RETENTION) -> int:
    """Delete oldest snapshots beyond retention. Returns number deleted."""
    metas = list_snapshots(name)
    if len(metas) <= retention:
        return 0
    # Keep newest `retention`, remove the rest.
    victims = metas[retention:]
    deleted = 0
    for m in victims:
        try:
            d = _snapshot_dir(name, m.id)
            shutil.rmtree(d)
            deleted += 1
        except HTTPException:
            continue  # already gone or invalid
        except OSError:
            continue
    return deleted


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


_MAX_DIFF_BYTES = 256 * 1024  # 256 KB per file, else show placeholder


@dataclass(frozen=True)
class _FileSet:
    by_rel: dict[str, Path]


def _collect_files(root: Path) -> _FileSet:
    """Map rel-path → absolute Path for every file under root, excluding
    the sidecar and any hidden directories (shouldn't exist, but safe)."""
    out: dict[str, Path] = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if p.name == _SIDECAR:
            continue
        out[str(rel).replace("\\", "/")] = p
    return _FileSet(by_rel=out)


def _read_text_capped(p: Path) -> tuple[bool, str]:
    """Return (is_text, text_or_placeholder)."""
    try:
        size = p.stat().st_size
    except OSError:
        return False, ""
    if size > _MAX_DIFF_BYTES:
        return False, f"[file too large to diff: {size} bytes]"
    try:
        with p.open("rb") as f:
            chunk = f.read(_MAX_DIFF_BYTES)
        if b"\x00" in chunk:
            return False, "[binary file]"
        return True, chunk.decode("utf-8", errors="replace")
    except OSError:
        return False, ""


def diff_snapshot(name: str, snapshot_id: str, only_path: str | None = None) -> SnapshotDiff:
    """Return unified diffs between snapshot and current skill state."""
    snap = _snapshot_dir(name, snapshot_id)
    live = _skill_dir(name)

    snap_files = _collect_files(snap)
    live_files = _collect_files(live)

    files: list[DiffFile] = []
    all_rels = sorted(set(snap_files.by_rel) | set(live_files.by_rel))
    for rel in all_rels:
        if only_path and rel != only_path:
            continue
        in_snap = rel in snap_files.by_rel
        in_live = rel in live_files.by_rel

        if in_snap and not in_live:
            _, text = _read_text_capped(snap_files.by_rel[rel])
            diff_text = "\n".join(
                unified_diff(text.splitlines(), [], fromfile=rel, tofile="/dev/null", lineterm="")
            )
            files.append(DiffFile(path=rel, status="deleted", diff=diff_text))
        elif in_live and not in_snap:
            _, text = _read_text_capped(live_files.by_rel[rel])
            diff_text = "\n".join(
                unified_diff([], text.splitlines(), fromfile="/dev/null", tofile=rel, lineterm="")
            )
            files.append(DiffFile(path=rel, status="added", diff=diff_text))
        else:
            is_text_a, a = _read_text_capped(snap_files.by_rel[rel])
            is_text_b, b = _read_text_capped(live_files.by_rel[rel])
            if a == b:
                continue  # unchanged
            if not is_text_a or not is_text_b:
                files.append(DiffFile(path=rel, status="modified", diff="[binary or oversize]"))
                continue
            diff_text = "\n".join(
                unified_diff(
                    a.splitlines(), b.splitlines(),
                    fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm="",
                )
            )
            files.append(DiffFile(path=rel, status="modified", diff=diff_text))

    return SnapshotDiff(snapshot_id=snapshot_id, files=files)


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def restore_snapshot(
    name: str,
    snapshot_id: str,
    create_pre_restore_snapshot: bool = True,
) -> dict:
    """Restore ``name`` to the state captured by ``snapshot_id``.

    Returns: ``{"restored_from": id, "pre_restore_snapshot_id": id_or_None}``
    """
    snap = _snapshot_dir(name, snapshot_id)
    live = _skill_dir(name)

    pre_id: str | None = None
    if create_pre_restore_snapshot:
        pre = create_snapshot(name, label=f"pre-restore of {snapshot_id}")
        pre_id = pre.id

    parent = live.parent
    tmp_new = parent / f"{live.name}.restoring.{secrets.token_hex(3)}"
    tmp_old = parent / f"{live.name}.old.{secrets.token_hex(3)}"

    # Stage 1: copy snapshot contents (minus sidecar) into a fresh sibling.
    def _ignore_sidecar(_src: str, names: list[str]) -> list[str]:
        return [n for n in names if n == _SIDECAR]

    try:
        shutil.copytree(snap, tmp_new, ignore=_ignore_sidecar, symlinks=False)
    except Exception as e:
        shutil.rmtree(tmp_new, ignore_errors=True)
        raise HTTPException(500, f"restore staging failed: {e}") from e

    # Stage 2: swap directories atomically via rename.
    try:
        live.rename(tmp_old)
    except OSError as e:
        shutil.rmtree(tmp_new, ignore_errors=True)
        raise HTTPException(500, f"restore swap failed (stage 2): {e}") from e

    try:
        tmp_new.rename(live)
    except OSError as e:
        # Worst case: try to put the old one back.
        try:
            tmp_old.rename(live)
        except OSError:
            pass
        shutil.rmtree(tmp_new, ignore_errors=True)
        raise HTTPException(500, f"restore swap failed (stage 3): {e}") from e

    # Stage 3: discard the previous version.
    shutil.rmtree(tmp_old, ignore_errors=True)

    return {"restored_from": snapshot_id, "pre_restore_snapshot_id": pre_id}
