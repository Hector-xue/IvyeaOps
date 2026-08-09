"""File operations API — port of the file endpoints in claudecodeui's
``server/index.js`` (read/write/tree/create/rename/delete/upload/images, plus
top-level browse-filesystem and create-folder).

Mounted at the agents root (full paths below) so the routes reproduce the original
``/projects/:projectId/...`` and ``/browse-filesystem`` / ``/create-folder``
shapes the frontend calls. Project id → absolute path via the projects table.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.agents import repos
from app.agents.db import db_conn
from app.agents.routers._actor import actor_is_admin, bind_actor, require_admin_actor

router = APIRouter(dependencies=[Depends(bind_actor)])

WORKSPACES_ROOT = os.getenv("WORKSPACES_ROOT") or os.path.expanduser("~")

# Heavy build/VCS/cache/tooling dirs we never recurse into (keeps the tree small
# even when the "project" is a home dir). Mirrors index.js HEAVY_DIRS.
_HEAVY_DIRS = {
    "node_modules", "dist", "build", ".git", ".svn", ".hg", ".cache", ".npm",
    ".cargo", ".rustup", ".vscode-server", ".local", ".venv", "venv",
    "__pycache__", ".next", ".nuxt", "target", ".gradle", ".pnpm-store",
    ".yarn", ".m2", ".ollama", ".hermes", ".docker", ".cursor-server", ".bun",
    ".deno", "vendor", ".tox", ".mypy_cache", ".pytest_cache",
}

_INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_RE = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$", re.IGNORECASE)


# --- helpers ----------------------------------------------------------------

def _project_root(project_id: str) -> str:
    with db_conn() as conn:
        row = repos.get_project_by_id(conn, project_id)
    if not row:
        raise HTTPException(404, "Project not found")
    return row["project_path"]


def _resolve_in_project(project_root: str, target: str) -> str:
    """把（相对或绝对）路径解析成绝对路径，并按调用者身份决定边界。

    **管理员**：路径可以指到项目外面。文件面板的"上一级"导航要靠它，而管理员
    本来就有内置终端可以访问整台机器的文件系统 —— 这里不构成新的暴露面。

    **普通成员**：只能待在项目根之内。绝对路径、以及 ``../`` 爬出去的相对路径，
    一律 403。

    ——为什么要分开：这个函数原本对所有人都放行任意绝对路径，注释给的理由是
    "the agents board is admin-only"。**这个前提已经不成立了**：main.py 用的是
    ``require_module("agents")`` 而不是 ``require_admin``，而 permissions.py 的
    「技术助理」预设默认就授予 agents。也就是说一个非管理员被授予该板块后，可以
    ``GET /projects/{id}/file?filePath=/etc/shadow`` 读走整台机器上的任何文件
    （而服务在不少装机上是 root 跑的）。把放行范围收回到"仅管理员"，既保住了
    上面那个真实的浏览需求，又堵掉了越权读。
    """
    resolved = (os.path.abspath(target) if os.path.isabs(target)
                else os.path.abspath(os.path.join(project_root, target)))
    if actor_is_admin():
        return resolved

    root = os.path.abspath(project_root)
    if resolved != root and not resolved.startswith(root + os.sep):
        raise HTTPException(403, "只有管理员可以访问项目目录以外的路径")
    return resolved


def _validate_filename(name: str) -> None:
    if not name or not name.strip():
        raise HTTPException(400, "Filename cannot be empty")
    if _INVALID_FILENAME_RE.search(name):
        raise HTTPException(400, "Filename contains invalid characters")
    if _RESERVED_RE.match(name):
        raise HTTPException(400, "Filename is a reserved name")
    if re.match(r"^\.+$", name):
        raise HTTPException(400, "Filename cannot be only dots")


def _perm_rwx(perm: int) -> str:
    return ("r" if perm & 4 else "-") + ("w" if perm & 2 else "-") + ("x" if perm & 1 else "-")


def _build_file_tree(dir_path: str, max_depth: int = 3, depth: int = 0) -> list[dict]:
    items: list[dict] = []
    try:
        entries = os.scandir(dir_path)
    except OSError:
        return items
    with entries:
        for entry in entries:
            if entry.name in _HEAVY_DIRS:
                continue
            item_path = os.path.join(dir_path, entry.name)
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                is_dir = False
            item = {"name": entry.name, "path": item_path,
                    "type": "directory" if is_dir else "file"}
            try:
                st = entry.stat(follow_symlinks=False)
                item["size"] = st.st_size
                item["modified"] = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
                mode = st.st_mode
                owner, group, other = (mode >> 6) & 7, (mode >> 3) & 7, mode & 7
                item["permissions"] = f"{owner}{group}{other}"
                item["permissionsRwx"] = _perm_rwx(owner) + _perm_rwx(group) + _perm_rwx(other)
            except OSError:
                item.update({"size": 0, "modified": None, "permissions": "000",
                             "permissionsRwx": "---------"})
            if is_dir and depth < max_depth:
                if os.access(item_path, os.R_OK):
                    item["children"] = _build_file_tree(item_path, max_depth, depth + 1)
                else:
                    item["children"] = []
            items.append(item)
    items.sort(key=lambda it: (0 if it["type"] == "directory" else 1, it["name"]))
    return items


def _expand_workspace_path(p: str) -> str:
    if not p:
        return p
    if p == "~":
        return WORKSPACES_ROOT
    if p.startswith("~/") or p.startswith("~\\"):
        return os.path.join(WORKSPACES_ROOT, p[2:])
    return p


# --- read / write -----------------------------------------------------------

@router.get("/projects/{project_id}/file")
async def read_file(project_id: str, filePath: str = Query(...)) -> dict:
    if not filePath:
        raise HTTPException(400, "Invalid file path")
    resolved = _resolve_in_project(_project_root(project_id), filePath)
    try:
        with open(resolved, "r", encoding="utf-8") as fh:
            content = fh.read()
    except FileNotFoundError:
        raise HTTPException(404, "File not found")
    except PermissionError:
        raise HTTPException(403, "Permission denied")
    return {"content": content, "path": resolved}


@router.get("/projects/{project_id}/files/content")
async def file_content(project_id: str, path: str = Query(...)):
    if not path:
        raise HTTPException(400, "Invalid file path")
    resolved = _resolve_in_project(_project_root(project_id), path)
    if not os.path.exists(resolved):
        raise HTTPException(404, "File not found")
    mime = mimetypes.guess_type(resolved)[0] or "application/octet-stream"
    return FileResponse(resolved, media_type=mime)


class SaveFileBody(BaseModel):
    filePath: str
    content: str


@router.put("/projects/{project_id}/file")
async def save_file(project_id: str, body: SaveFileBody) -> dict:
    if not body.filePath:
        raise HTTPException(400, "Invalid file path")
    resolved = _resolve_in_project(_project_root(project_id), body.filePath)
    try:
        with open(resolved, "w", encoding="utf-8") as fh:
            fh.write(body.content)
    except FileNotFoundError:
        raise HTTPException(404, "File or directory not found")
    except PermissionError:
        raise HTTPException(403, "Permission denied")
    return {"success": True, "path": resolved, "message": "File saved successfully"}


@router.get("/projects/{project_id}/files")
async def file_tree(project_id: str, root: Optional[str] = Query(None)) -> list:
    # `root` overrides the project root so the panel can navigate up / into any
    # directory (admin-only local board — see _resolve_in_project). Defaults to
    # the project's own path.
    base = os.path.abspath(_expand_workspace_path(root)) if root else _project_root(project_id)
    if not os.path.isdir(base):
        raise HTTPException(404, f"Directory not found: {base}")
    return _build_file_tree(base, max_depth=4, depth=0)


# --- create / rename / delete -----------------------------------------------

class CreateFileBody(BaseModel):
    path: Optional[str] = ""
    type: str
    name: str


@router.post("/projects/{project_id}/files/create")
async def create_file(project_id: str, body: CreateFileBody) -> dict:
    if not body.name or not body.type:
        raise HTTPException(400, "Name and type are required")
    if body.type not in ("file", "directory"):
        raise HTTPException(400, 'Type must be "file" or "directory"')
    _validate_filename(body.name)
    root = _project_root(project_id)
    target = os.path.join(body.path or "", body.name) if (body.path or "") else body.name
    resolved = _resolve_in_project(root, target)
    if os.path.exists(resolved):
        raise HTTPException(409, f"{'File' if body.type == 'file' else 'Directory'} already exists")
    if body.type == "directory":
        os.makedirs(resolved, exist_ok=False)
    else:
        os.makedirs(os.path.dirname(resolved), exist_ok=True)
        with open(resolved, "w", encoding="utf-8"):
            pass
    return {"success": True, "path": resolved, "name": body.name, "type": body.type,
            "message": f"{'File' if body.type == 'file' else 'Directory'} created successfully"}


class RenameFileBody(BaseModel):
    oldPath: str
    newName: str


@router.put("/projects/{project_id}/files/rename")
async def rename_file(project_id: str, body: RenameFileBody) -> dict:
    if not body.oldPath or not body.newName:
        raise HTTPException(400, "oldPath and newName are required")
    _validate_filename(body.newName)
    root = _project_root(project_id)
    resolved_old = _resolve_in_project(root, body.oldPath)
    if not os.path.exists(resolved_old):
        raise HTTPException(404, "File or directory not found")
    resolved_new = os.path.join(os.path.dirname(resolved_old), body.newName)
    _resolve_in_project(root, resolved_new)
    if os.path.exists(resolved_new):
        raise HTTPException(409, "A file or directory with this name already exists")
    os.rename(resolved_old, resolved_new)
    return {"success": True, "oldPath": resolved_old, "newPath": resolved_new,
            "newName": body.newName, "message": "Renamed successfully"}


class DeleteFileBody(BaseModel):
    path: str
    type: Optional[str] = None


@router.delete("/projects/{project_id}/files")
async def delete_file(project_id: str, body: DeleteFileBody) -> dict:
    if not body.path:
        raise HTTPException(400, "Path is required")
    root = _project_root(project_id)
    resolved = _resolve_in_project(root, body.path)
    if not os.path.exists(resolved):
        raise HTTPException(404, "File or directory not found")
    if resolved == os.path.abspath(root):
        raise HTTPException(403, "Cannot delete project root directory")
    is_dir = os.path.isdir(resolved)
    if is_dir:
        import shutil
        shutil.rmtree(resolved, ignore_errors=True)
    else:
        os.unlink(resolved)
    return {"success": True, "path": resolved, "type": "directory" if is_dir else "file",
            "message": "Deleted successfully"}


# --- uploads ----------------------------------------------------------------

@router.post("/projects/{project_id}/files/upload")
async def upload_files(project_id: str, files: list[UploadFile] = File(...),
                       targetPath: str = Form(""), relativePaths: str = Form(None)) -> dict:
    if not files:
        raise HTTPException(400, "No files provided")
    root = _project_root(project_id)
    rel_paths = []
    if relativePaths:
        try:
            rel_paths = json.loads(relativePaths)
        except (ValueError, TypeError):
            rel_paths = []
    if not targetPath or targetPath in (".", "./"):
        target_dir = os.path.abspath(root)
    else:
        target_dir = _resolve_in_project(root, targetPath)
    os.makedirs(target_dir, exist_ok=True)
    uploaded = []
    for i, f in enumerate(files):
        name = rel_paths[i] if (rel_paths and i < len(rel_paths)) else f.filename
        dest = os.path.join(target_dir, name)
        try:
            _resolve_in_project(root, dest)
        except HTTPException:
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        # Stream the upload to disk in chunks instead of reading the whole file
        # into memory — large files (up to ~300MB) would otherwise spike RAM.
        size = 0
        with open(dest, "wb") as out:
            while True:
                chunk = await f.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                out.write(chunk)
        uploaded.append({"name": name, "path": dest, "size": size,
                         "mimeType": f.content_type})
    return {"success": True, "files": uploaded, "targetPath": target_dir,
            "message": f"Uploaded {len(uploaded)} file(s) successfully"}


_IMAGE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/svg+xml"}


@router.post("/projects/{project_id}/upload-images")
async def upload_images(project_id: str, images: list[UploadFile] = File(...)) -> dict:
    if not images:
        raise HTTPException(400, "No image files provided")
    out = []
    for f in images:
        if f.content_type not in _IMAGE_MIMES:
            raise HTTPException(400, "Invalid file type. Only JPEG, PNG, GIF, WebP, and SVG are allowed.")
        data = await f.read()
        b64 = base64.b64encode(data).decode()
        out.append({"name": f.filename, "data": f"data:{f.content_type};base64,{b64}",
                    "size": len(data), "mimeType": f.content_type})
    return {"images": out}


# --- top-level: browse-filesystem / create-folder ---------------------------

def _windows_drives() -> list[dict]:
    """List available Windows drive roots (C:\\, D:\\, …) as folder entries."""
    import string
    letters: list[str] = []
    try:
        letters = list(os.listdrives())  # Python 3.12+: ['C:\\', 'D:\\', …]
    except Exception:
        letters = []
    if not letters:
        letters = [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]
    return [{"path": d, "name": d.rstrip("\\"), "type": "directory"} for d in letters]


@router.get("/browse-filesystem")
async def browse_filesystem(path: Optional[str] = Query(None)) -> dict:
    # 给"新建项目"用的目录选择器，能浏览整个文件系统（用户要在别的盘打开项目）。
    # 这条对**管理员**保持原样；普通成员即使被授予了 agents 板块也不该拿到一个
    # 全盘目录浏览器 —— 那等于绕过 _resolve_in_project 的项目内包含检查去踩点。
    require_admin_actor("浏览文件系统")
    if os.name == "nt" and path == "__drives__":
        return {"path": "__drives__", "parent": "", "isDrives": True,
                "suggestions": _windows_drives()}
    target = _expand_workspace_path(path) if path else WORKSPACES_ROOT
    target = os.path.abspath(target)
    if not os.path.isdir(target):
        raise HTTPException(404, "Directory not accessible")
    tree = _build_file_tree(target, max_depth=1, depth=0)
    dirs = [{"path": it["path"], "name": it["name"], "type": "directory"}
            for it in tree if it["type"] == "directory"]
    dirs.sort(key=lambda d: (1 if d["name"].startswith(".") else 0, d["name"].lower()))
    suggestions = dirs
    try:
        resolved_root = os.path.realpath(WORKSPACES_ROOT)
    except OSError:
        resolved_root = WORKSPACES_ROOT
    if target == resolved_root:
        common = ["Desktop", "Documents", "Projects", "Development", "Dev", "Code", "workspace"]
        existing = [d for d in dirs if d["name"] in common]
        others = [d for d in dirs if d["name"] not in common]
        suggestions = existing + others
    # Parent for the "上一级" button. At a root, go to the Windows drive list
    # (so users can switch drives) or "" on POSIX (already at /).
    parent = os.path.dirname(target)
    if not parent or parent == target:
        parent = "__drives__" if os.name == "nt" else ""
    return {"path": target, "parent": parent, "suggestions": suggestions}


class CreateFolderBody(BaseModel):
    path: str


@router.post("/create-folder")
async def create_folder(body: CreateFolderBody) -> dict:
    if not body.path:
        raise HTTPException(400, "Path is required")
    # 和 browse-filesystem 同一套信任模型：允许在浏览到的任何位置建项目目录，
    # 但仅限管理员 —— 否则普通成员能在机器上任意位置创建目录。
    require_admin_actor("在任意位置创建目录")
    target = os.path.abspath(_expand_workspace_path(body.path))
    parent = os.path.dirname(target)
    if not os.path.exists(parent):
        raise HTTPException(404, "Parent directory does not exist")
    if os.path.exists(target):
        raise HTTPException(409, "Folder already exists")
    os.makedirs(target, exist_ok=False)
    return {"success": True, "path": target}
