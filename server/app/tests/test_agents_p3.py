"""P3 tests: file operations (tree/read/write/create/rename/delete/content/
upload/images) + browse-filesystem / create-folder, against a temp project."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_ORIGIN = "https://test.example.com"
_HDR = {"Origin": _ORIGIN}


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("IVYEA_OPS_SECRET", "test-secret")
    monkeypatch.setenv("IVYEA_OPS_ALLOWED_ORIGINS", _ORIGIN)
    monkeypatch.setenv("AGENTS_DB_PATH", str(tmp_path / "agents.db"))
    monkeypatch.setenv("WORKSPACES_ROOT", str(tmp_path))

    from app.core import config as cfg_mod
    importlib.reload(cfg_mod)
    from app.core import security as sec_mod
    importlib.reload(sec_mod)
    from app.agents import db as db_mod
    importlib.reload(db_mod); db_mod.init_db()

    proj = tmp_path / "proj"
    (proj / "sub").mkdir(parents=True)
    (proj / "a.txt").write_text("hello", encoding="utf-8")
    (proj / "sub" / "nested.txt").write_text("deep", encoding="utf-8")
    with db_mod.db_conn() as conn:
        conn.execute("INSERT INTO projects(project_id, project_path, isStarred, isArchived)"
                     " VALUES(?,?,0,0)", ("p1", str(proj)))

    from app.agents.routers import files as files_mod
    importlib.reload(files_mod)
    from app.agents import router as router_mod
    importlib.reload(router_mod)
    from app import main as main_mod
    importlib.reload(main_mod)

    cookie = sec_mod.issue_session("admin", "admin")
    c = TestClient(main_mod.app)
    c.cookies.set(cfg_mod.settings.session_cookie_name, cookie)
    return c, str(proj), str(tmp_path)


def test_file_tree(ctx):
    c, _proj, _ = ctx
    r = c.get("/api/agents/projects/p1/files")
    assert r.status_code == 200, r.text
    names = {it["name"]: it for it in r.json()}
    assert "a.txt" in names and "sub" in names
    assert names["sub"]["type"] == "directory"
    assert any(ch["name"] == "nested.txt" for ch in names["sub"]["children"])


def test_read_and_save(ctx):
    c, *_ = ctx
    r = c.get("/api/agents/projects/p1/file", params={"filePath": "a.txt"})
    assert r.status_code == 200 and r.json()["content"] == "hello"
    r = c.put("/api/agents/projects/p1/file", json={"filePath": "a.txt", "content": "world"}, headers=_HDR)
    assert r.status_code == 200 and r.json()["success"]
    assert c.get("/api/agents/projects/p1/file", params={"filePath": "a.txt"}).json()["content"] == "world"


def test_create_rename_delete(ctx):
    c, *_ = ctx
    assert c.post("/api/agents/projects/p1/files/create", json={"path": "", "type": "file", "name": "b.txt"}, headers=_HDR).json()["success"]
    assert c.put("/api/agents/projects/p1/files/rename", json={"oldPath": "b.txt", "newName": "c.txt"}, headers=_HDR).json()["newName"] == "c.txt"
    assert c.request("DELETE", "/api/agents/projects/p1/files", json={"path": "c.txt", "type": "file"}, headers=_HDR).json()["success"]


def test_binary_content(ctx):
    c, *_ = ctx
    r = c.get("/api/agents/projects/p1/files/content", params={"path": "a.txt"})
    assert r.status_code == 200 and r.content == b"hello"


def test_relative_paths_resolve_under_the_project_root(ctx):
    """相对路径以项目根为基准解析 —— 越级出去就落到项目外的不存在位置。

    这条原名 test_path_traversal_blocked、断言 403。**边界后来被有意移除了**：
    files._resolve_in_project 现在允许任意绝对路径，理由写在那个函数的注释里
    （文件面板要能"上一级"浏览，且当时 agents 板块是 admin-only、内置终端本来
    就给了全盘访问）。所以这条断言早已与实现脱节，只是没人知道——app/tests
    不在 CI 里。

    ⚠️ 那条"admin-only"的前提**现在也不成立了**：main.py 用的是
    require_module("agents")，而 permissions.py 的「技术助理」预设默认就授予
    agents。也就是说非管理员被授予该板块后能读服务器上任意文件。这是个待拍板的
    产品/安全决定，不该由一条测试悄悄替它选边，所以这里只如实描述当前行为。
    """
    c, *_ = ctx
    r = c.get("/api/agents/projects/p1/file", params={"filePath": "../../../etc/passwd"})
    # 相对路径拼到项目根之上 → 指向一个不存在的位置 → 404，而不是读到系统文件。
    assert r.status_code == 404
    assert "root:" not in r.text


def test_upload_files(ctx):
    c, proj, _ = ctx
    r = c.post("/api/agents/projects/p1/files/upload",
               files=[("files", ("up.txt", b"uploaded", "text/plain"))],
               data={"targetPath": ""}, headers=_HDR)
    assert r.status_code == 200, r.text
    assert r.json()["files"][0]["name"] == "up.txt"
    assert (Path(proj) / "up.txt").read_bytes() == b"uploaded"


def test_upload_images(ctx):
    c, *_ = ctx
    r = c.post("/api/agents/projects/p1/upload-images",
               files=[("images", ("x.png", b"\x89PNG\r\n", "image/png"))], headers=_HDR)
    assert r.status_code == 200, r.text
    img = r.json()["images"][0]
    assert img["data"].startswith("data:image/png;base64,")


def test_browse_filesystem(ctx):
    c, _proj, root = ctx
    r = c.get("/api/agents/browse-filesystem", params={"path": root})
    assert r.status_code == 200, r.text
    assert any(s["name"] == "proj" for s in r.json()["suggestions"])


def test_create_folder(ctx):
    c, _proj, root = ctx
    target = str(Path(root) / "newdir")
    r = c.post("/api/agents/create-folder", json={"path": target}, headers=_HDR)
    assert r.status_code == 200 and r.json()["success"]
    assert Path(target).is_dir()


def test_workspace_root_in_blocklist_still_allows_subpaths(monkeypatch):
    """Regression: when the server runs as root, WORKSPACES_ROOT defaults to /root,
    which is also a system-critical dir in the blocklist. Project creation must
    still work for paths *under* the workspace root, while paths outside it (and
    real system dirs) stay rejected. Before the fix every /root/* path was a 400
    ("Cannot create workspace in system directory: /root") and no agent project
    could be created on a root install."""
    from app.agents.routers import projects as P
    monkeypatch.setattr(P, "WORKSPACES_ROOT", "/root")
    assert P._validate_workspace_path("/root/my-project") == "/root/my-project"
    assert P._validate_workspace_path("/root/agents/demo") == "/root/agents/demo"

    # 系统关键目录仍然拒（工作区根自身及其子树除外，那正是上面两条要保住的）。
    for bad in ("/etc/passwd", "/tmp/x", "/"):
        with pytest.raises(Exception):
            P._validate_workspace_path(bad)

    # /home/other/p **不再被拒**：_validate_workspace_path 后来有意去掉了
    # "必须在 WORKSPACES_ROOT 之下"这条约束（注释理由：Windows 上要能在别的盘
    # 打开项目）。原来的断言把它和系统目录混在一起，边界一改就红，而 app/tests
    # 不在 CI 里所以没人知道。同 test_relative_paths_resolve_under_the_project_root
    # 里的那条 ⚠️：这个放宽的前提是"板块只有管理员能进"，而现在不是了。
    assert P._validate_workspace_path("/home/other/p") == "/home/other/p"


def test_normalize_project_path_windows_backslashes():
    """Regression: Windows paths must normalize to forward slashes so containment
    checks (path.startswith(root + '/')) work. Before the fix, posixpath kept the
    backslashes and every Windows project path was rejected ('Failed to create
    project')."""
    from app.agents import repos
    assert repos.normalize_project_path("C:\\Users\\x\\proj") == "C:/Users/x/proj"
    assert repos.normalize_project_path("C:/Users/x/proj") == "C:/Users/x/proj"
    assert repos.normalize_project_path("C:\\Users\\x\\a\\..\\b") == "C:/Users/x/b"
    # Linux paths are unaffected.
    assert repos.normalize_project_path("/root/p") == "/root/p"
