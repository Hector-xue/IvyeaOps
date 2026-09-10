"""任务台产物：落库、越权、下载 / 预览的出口形态。

这套用例盯死四件最容易出事的：

1. **入口**：SSE 流里的 `file_change` 要被记下来 —— 这条断了后面全是空的；
2. **越权**：别人会话产出的文件，我下不到，而且**看不出它存不存在**；
3. **不 inline 危险类型**：.html 永远不能在自己的域上直接打开；
4. **索引不是快照**：文件后来被删了要如实说"已不在"，不能拿旧的大小
   画一个点了报错的下载按钮。

写法跟 test_console_sessions 一致：直接调路由函数、传 info 字典，
不起 TestClient —— 这一层要验的是归属与出口形态，不是 HTTP 管道。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.routers import ivyea_agent as mod
from app.services import console_sessions as cs

ALICE = {"email": "alice@x.com", "role": "user", "id": 2}
BOB = {"email": "bob@x.com", "role": "user", "id": 3}
ADMIN = {"email": "admin@x.com", "role": "admin", "id": "admin"}


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    # 只改这个模块的落盘位置，不动全局 settings.data_dir（那是共享对象，
    # 改它会波及别的用例 —— test_console_sessions 里记着这个坑）。
    monkeypatch.setattr(cs, "_db_path", lambda: tmp_path / "console_sessions.sqlite3")
    cs.init_db()
    yield


def _principal(info: dict) -> str:
    """路由用 _principal_info 从 info 里推主体，测试里对齐同一套。"""
    return mod._principal_info(info)[0]


# ── 落库 ────────────────────────────────────────────────────────────────────

def test_record_file_is_idempotent():
    """同一会话同一路径写三次 = 一行，changes 累加。

    不幂等的话，一个循环写十遍的任务会在列表里堆出十行同名文件。
    """
    cs.register_session("s1", _principal(ALICE))
    for _ in range(3):
        cs.record_file("s1", _principal(ALICE), "/tmp/out.md", "overwrite")
    rows = cs.session_files("s1")
    assert len(rows) == 1
    assert rows[0]["changes"] == 3
    assert rows[0]["name"] == "out.md"


def test_record_file_ignores_empty():
    cs.record_file("", "alice@x.com", "/tmp/a.md")
    cs.record_file("s1", "alice@x.com", "")
    assert cs.session_files("s1") == []


def test_forget_session_drops_files():
    """删会话连产物索引一起删 —— 留着就是查不到主人的孤儿行。"""
    cs.register_session("s1", _principal(ALICE))
    cs.record_file("s1", _principal(ALICE), "/tmp/out.md")
    cs.forget_session("s1")
    assert cs.session_files("s1") == []


def test_file_id_is_stable_and_scoped():
    a = cs._file_id("s1", "/tmp/x.md")
    assert a == cs._file_id("s1", "/tmp/x.md")      # 幂等寻址
    assert a != cs._file_id("s2", "/tmp/x.md")      # 不跨会话撞车


# ── 列表：现场 stat ─────────────────────────────────────────────────────────

def test_list_reports_live_stat(tmp_path):
    f = tmp_path / "报表.md"
    f.write_text("# 标题\n正文", encoding="utf-8")
    gone = tmp_path / "没了.md"

    cs.register_session("s1", _principal(ALICE))
    cs.record_file("s1", _principal(ALICE), str(f), "create")
    cs.record_file("s1", _principal(ALICE), str(gone), "create")

    files = mod.console_session_files("s1", info=ALICE)["files"]
    by_name = {x["name"]: x for x in files}
    assert by_name["报表.md"]["exists"] is True
    # 跟**盘上的真实大小**比，别自己算字节数：Windows 上 write_text 会把 \n
    # 换成 \r\n，算出来的 15 和盘上的 16 对不上（CI 的 windows 任务抓到过）。
    assert by_name["报表.md"]["size"] == f.stat().st_size
    assert by_name["报表.md"]["kind"] == "markdown"
    assert by_name["报表.md"]["previewable"] is True
    # 文件被删了要如实说，不能拿库里的旧值画一个点了报错的按钮
    assert by_name["没了.md"]["exists"] is False
    assert by_name["没了.md"]["previewable"] is False
    assert by_name["没了.md"]["size"] == 0


def test_binary_is_listed_but_not_previewable(tmp_path):
    """看不了不等于拿不走：xlsx 没有预览器，下载照给。"""
    f = tmp_path / "明细.xlsx"
    f.write_bytes(b"PK\x03\x04" + b"0" * 64)
    cs.register_session("s1", _principal(ALICE))
    cs.record_file("s1", _principal(ALICE), str(f), "create")
    row = mod.console_session_files("s1", info=ALICE)["files"][0]
    assert row["kind"] == "binary"
    assert row["exists"] is True
    assert row["previewable"] is False


# ── 归属 ────────────────────────────────────────────────────────────────────

def test_other_peoples_session_list_is_403(tmp_path):
    cs.register_session("s-bob", _principal(BOB))
    with pytest.raises(HTTPException) as exc:
        mod.console_session_files("s-bob", info=ALICE)
    assert exc.value.status_code == 403


def test_other_peoples_file_is_404_not_403(tmp_path):
    """越权取单个文件要返回 **404 而不是 403**。

    403 等于确认"这个 id 存在，只是不归你" —— 那就成了一个可以拿来
    枚举别人会话的信号。查不到和不归你，对外必须长得一模一样。
    """
    f = tmp_path / "秘密.md"
    f.write_text("bob 的东西", encoding="utf-8")
    cs.register_session("s-bob", _principal(BOB))
    cs.record_file("s-bob", _principal(BOB), str(f), "create")
    fid = cs._file_id("s-bob", str(f))

    for call in (lambda: mod.console_file_raw(fid, 1, info=ALICE),
                 lambda: mod.console_file_text(fid, info=ALICE)):
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 404
        assert exc.value.detail == "文件不存在"

    # 不存在的 id 给出**同样**的回答
    with pytest.raises(HTTPException) as exc:
        mod.console_file_text("deadbeefdeadbeef", info=ALICE)
    assert exc.value.status_code == 404
    assert exc.value.detail == "文件不存在"


def test_admin_can_reach_any_file(tmp_path):
    f = tmp_path / "秘密.md"
    f.write_text("bob 的东西", encoding="utf-8")
    cs.register_session("s-bob", _principal(BOB))
    cs.record_file("s-bob", _principal(BOB), str(f), "create")
    fid = cs._file_id("s-bob", str(f))
    assert mod.console_file_text(fid, info=ADMIN)["text"] == "bob 的东西"


# ── 出口形态 ────────────────────────────────────────────────────────────────

def test_download_forces_attachment(tmp_path):
    f = tmp_path / "报表.md"
    f.write_text("# hi", encoding="utf-8")
    cs.register_session("s1", _principal(ALICE))
    cs.record_file("s1", _principal(ALICE), str(f), "create")
    fid = cs._file_id("s1", str(f))

    resp = mod.console_file_raw(fid, 1, info=ALICE)
    assert resp.media_type == "application/octet-stream"
    assert "attachment" in resp.headers["content-disposition"]


def test_html_is_never_inlined(tmp_path):
    """.html 就算点了"预览"也不许 inline —— 在自己的域上打开它等于执行别人的脚本。
    预览走 /text（前端再塞进沙箱 iframe）。"""
    f = tmp_path / "报表.html"
    f.write_text("<script>alert(1)</script>", encoding="utf-8")
    cs.register_session("s1", _principal(ALICE))
    cs.record_file("s1", _principal(ALICE), str(f), "create")
    fid = cs._file_id("s1", str(f))

    resp = mod.console_file_raw(fid, 0, info=ALICE)   # download=0 = 想 inline
    assert resp.media_type == "application/octet-stream"
    assert "attachment" in resp.headers["content-disposition"]
    # 但文本读得到，前端才有得渲染
    assert "<script>" in mod.console_file_text(fid, info=ALICE)["text"]


def test_svg_inline_is_sandboxed(tmp_path):
    """.svg 能带脚本，所以它 inline 时**必须**带 sandbox CSP。

    预览器用 `<img>` 装它 —— 那条路按规范就不执行脚本；这条 CSP 堵的是另一条：
    有人把 raw 地址粘到地址栏，浏览器会把 svg 当文档打开。
    """
    f = tmp_path / "图.svg"
    f.write_text("<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
                 encoding="utf-8")
    cs.register_session("s1", _principal(ALICE))
    cs.record_file("s1", _principal(ALICE), str(f), "create")
    fid = cs._file_id("s1", str(f))
    resp = mod.console_file_raw(fid, 0, info=ALICE)
    assert resp.media_type == "image/svg+xml"
    assert resp.headers["content-security-policy"].startswith("sandbox")
    assert "allow-scripts" not in resp.headers["content-security-policy"]


def test_image_inline_is_sandboxed(tmp_path):
    """图片可以 inline（预览要用），但带上 CSP sandbox + nosniff 兜底。"""
    f = tmp_path / "图.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    cs.register_session("s1", _principal(ALICE))
    cs.record_file("s1", _principal(ALICE), str(f), "create")
    fid = cs._file_id("s1", str(f))

    resp = mod.console_file_raw(fid, 0, info=ALICE)
    assert resp.media_type == "image/png"
    assert "inline" in resp.headers["content-disposition"]
    assert "sandbox" in resp.headers["content-security-policy"]
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_missing_file_is_404(tmp_path):
    cs.register_session("s1", _principal(ALICE))
    p = str(tmp_path / "没写出来.md")
    cs.record_file("s1", _principal(ALICE), p, "create")
    fid = cs._file_id("s1", p)
    for call in (lambda: mod.console_file_raw(fid, 1, info=ALICE),
                 lambda: mod.console_file_text(fid, info=ALICE)):
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 404
        assert "磁盘" in exc.value.detail


def test_text_preview_is_truncated(tmp_path):
    """预览有上限：一个 40MB 的 csv 不该把浏览器卡死，也不该整个读进内存。"""
    f = tmp_path / "大.log"
    f.write_text("x" * (mod._PREVIEW_MAX_BYTES + 500), encoding="utf-8")
    cs.register_session("s1", _principal(ALICE))
    cs.record_file("s1", _principal(ALICE), str(f), "create")
    fid = cs._file_id("s1", str(f))

    d = mod.console_file_text(fid, info=ALICE)
    assert d["truncated"] is True
    assert len(d["text"]) == mod._PREVIEW_MAX_BYTES


def test_kind_classification():
    assert mod._file_kind("a.md") == "markdown"
    assert mod._file_kind("a.CSV") == "csv"
    assert mod._file_kind("a.json") == "text"
    assert mod._file_kind("a.html") == "html"
    assert mod._file_kind("a.png") == "image"
    assert mod._file_kind("a.pdf") == "pdf"
    assert mod._file_kind("a.xlsx") == "binary"
    assert mod._file_kind("Makefile") == "binary"


# ── 入口：SSE 流 ────────────────────────────────────────────────────────────

def test_tee_records_file_change(monkeypatch):
    """流里的 file_change 要被记下来，且**转发一个字节都不能改**。"""
    frames = [
        b'event: start\ndata: {"session_id": "s9"}\n\n',
        b'event: file_change\ndata: {"session_id": "s9", "path": "/tmp/a.md", "action": "create"}\n\n',
        b'event: file_change\ndata: {"path": "/tmp/b.md", "action": "create"}\n\n',
    ]
    monkeypatch.setattr(mod, "_auto_title_session", lambda *a, **k: None)
    out = b"".join(mod._tee_session_events(iter(frames), "alice@x.com", "", "console", True))
    assert out == b"".join(frames)
    names = {r["name"] for r in cs.session_files("s9")}
    # 第二条没带 session_id，要退回本轮的 live_sid，否则这个文件就丢了
    assert names == {"a.md", "b.md"}


def test_tee_survives_broken_frames(monkeypatch):
    """半截 JSON 不能把转发弄坏 —— 记账失败最坏是少一条记录，弄坏流是毁掉整轮。"""
    frames = [
        b'event: file_change\ndata: {"session_id": "s9", "path": \n\n',
        b'event: file_change\ndata: {"session_id": "s9", "path": "/tmp/ok.md"}\n\n',
    ]
    monkeypatch.setattr(mod, "_auto_title_session", lambda *a, **k: None)
    out = b"".join(mod._tee_session_events(iter(frames), "alice@x.com", "", "console", True))
    assert out == b"".join(frames)


def test_files_only_tee_records_without_registering():
    """接流时只记文件：不登记会话、不推审批、不起名 —— 那些是发起方那条流的责任，
    重复做会推两遍通知、起两次名。"""
    frames = [b'event: file_change\ndata: {"session_id": "s7", "path": "/tmp/c.md"}\n\n']
    out = b"".join(mod._tee_files_only(iter(frames), "alice@x.com"))
    assert out == frames[0]
    assert [r["name"] for r in cs.session_files("s7")] == ["c.md"]
    assert cs.session_row("s7") is None


# ── 产物挂到哪一轮 ───────────────────────────────────────────────────────────
#
# 卡片要出现在**产出它的那条回答**下面。产物列在右侧那条 40px 图标条里没人找得到
# —— 上线以来生产库一直是 0 行，用户的原话是"我刚才测试了一下压根没看到"。

def test_turn_id_is_recorded():
    cs.record_file("s1", "u", "/w/a.md", "create", "turn-1")
    row = cs.session_files("s1")[0]
    assert row["turn_id"] == "turn-1"


def test_turn_id_follows_the_latest_write():
    """同一个文件被后面几轮又改了，卡片该跟到最后写它的那条回答下面。"""
    cs.record_file("s1", "u", "/w/a.md", "create", "turn-1")
    cs.record_file("s1", "u", "/w/a.md", "overwrite", "turn-4")
    rows = cs.session_files("s1")
    assert len(rows) == 1, "同路径重复写不该攒出多行"
    assert rows[0]["turn_id"] == "turn-4"
    assert rows[0]["changes"] == 2


def test_missing_turn_id_is_harmless():
    """老记录没有 turn_id —— 留空即可，别拿它当错误。"""
    cs.record_file("s1", "u", "/w/a.md", "create")
    assert cs.session_files("s1")[0]["turn_id"] == ""


def test_write_action_from_a_command_is_recorded():
    """run_command / run_python 写出来的文件，action 是 write（拿不到改前内容）。"""
    cs.record_file("s1", "u", "/w/report.csv", "write", "t1")
    row = cs.session_files("s1")[0]
    assert row["action"] == "write" and row["name"] == "report.csv"
