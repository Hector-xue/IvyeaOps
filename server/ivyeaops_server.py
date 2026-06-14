"""PyInstaller-friendly IvyeaOps backend launcher."""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _desktop_paths() -> list[Path]:
    paths: list[Path] = []
    for raw in (
        os.environ.get("USERPROFILE", "") and os.path.join(os.environ["USERPROFILE"], "Desktop"),
        os.path.expandvars(r"%OneDrive%\Desktop"),
        os.path.expandvars(r"%PUBLIC%\Desktop"),
    ):
        if raw and "%" not in raw:
            p = Path(raw)
            if p.exists() and p not in paths:
                paths.append(p)
    return paths


def _write_credentials(root: Path, password: str) -> Path:
    credentials = "\n".join(
        [
            "IvyeaOps 本机登录信息",
            "",
            "访问地址: http://127.0.0.1:8001",
            "用户名: admin",
            f"密码: {password}",
            "",
            "首次登录后可在 系统配置 -> 账号安全 修改密码。",
            "请只保存在自己的电脑上，不要发给他人。",
        ]
    )
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    cred_file = data_dir / "IvyeaOps 登录信息.txt"
    cred_file.write_text(credentials, encoding="utf-8")
    for desktop in _desktop_paths():
        try:
            (desktop / "IvyeaOps 登录信息.txt").write_text(credentials, encoding="utf-8")
        except Exception:
            pass
    return cred_file


def _create_desktop_shortcut(root: Path) -> None:
    if not sys.platform.startswith("win") or not getattr(sys, "frozen", False):
        return
    exe = Path(sys.executable).resolve()
    ps = r"""
$ErrorActionPreference = 'SilentlyContinue'
$Target = $env:IVYEA_SHORTCUT_TARGET
$WorkDir = $env:IVYEA_SHORTCUT_WORKDIR
$Candidates = @()
try { $Candidates += [Environment]::GetFolderPath('Desktop') } catch {}
try { $Candidates += (New-Object -ComObject WScript.Shell).SpecialFolders.Item('Desktop') } catch {}
if ($env:OneDrive) { $Candidates += (Join-Path $env:OneDrive 'Desktop') }
if ($env:PUBLIC) { $Candidates += (Join-Path $env:PUBLIC 'Desktop') }
$Candidates = $Candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique
foreach ($Desktop in $Candidates) {
  try {
    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut((Join-Path $Desktop 'IvyeaOps.lnk'))
    $Shortcut.TargetPath = $Target
    $Shortcut.WorkingDirectory = $WorkDir
    $Shortcut.Description = '启动 IvyeaOps 工作台'
    $Shortcut.IconLocation = $Target
    $Shortcut.Save()
    break
  } catch {}
}
"""
    env = {**os.environ, "IVYEA_SHORTCUT_TARGET": str(exe), "IVYEA_SHORTCUT_WORKDIR": str(root)}
    try:
        kwargs = {}
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            env=env,
            cwd=str(root),
            timeout=15,
            **kwargs,
        )
    except Exception:
        pass


def _open_text_file(path: Path) -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception:
        pass


def _already_running(host: str, port: int) -> bool:
    health_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    try:
        with urllib.request.urlopen(f"http://{health_host}:{port}/api/health", timeout=1) as resp:
            return resp.status == 200
    except Exception:
        return False


def _control_window_enabled() -> bool:
    return (
        getattr(sys, "frozen", False)
        and sys.platform.startswith("win")
        and os.getenv("IVYEA_OPS_CONTROL_WINDOW", "1").lower() not in {"0", "false", "no"}
    )


def _bootstrap_frozen_env() -> None:
    if not getattr(sys, "frozen", False):
        return
    root = _runtime_root()
    logs_dir = root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None:
        sys.stdout = (logs_dir / "ivyeaops.out.log").open("a", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = (logs_dir / "ivyeaops.err.log").open("a", encoding="utf-8", buffering=1)
    # When a console *is* attached (the control window), it defaults to the
    # system code page (GBK on 中文 Windows). Printing the app's Chinese log
    # lines then raises UnicodeEncodeError and can crash startup — force UTF-8.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass

    env_file = root / "server" / ".env"
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("ADMIN_PASSWORD="):
                    _write_credentials(root, line.split("=", 1)[1])
                    break
        except Exception:
            pass
        _create_desktop_shortcut(root)
        return

    password = os.getenv("IVYEA_OPS_ADMIN_PASSWORD") or os.getenv("ADMIN_PASSWORD") or secrets.token_urlsafe(12)
    secret = secrets.token_urlsafe(32)
    (root / "server").mkdir(parents=True, exist_ok=True)
    (root / "data").mkdir(parents=True, exist_ok=True)
    env_file.write_text(
        "\n".join(
            [
                "# Generated by IvyeaOpsServer.exe on first run.",
                "IVYEA_OPS_HOST=127.0.0.1",
                "IVYEA_OPS_PORT=8001",
                "IVYEA_OPS_DEV=0",
                f"IVYEA_OPS_SECRET={secret}",
                "IVYEA_OPS_USER=admin",
                f"ADMIN_PASSWORD={password}",
                "IVYEA_OPS_ALLOWED_ORIGINS=http://127.0.0.1:8001",
                "",
            ]
        ),
        encoding="utf-8",
    )
    cred_file = _write_credentials(root, password)
    _create_desktop_shortcut(root)
    _open_text_file(cred_file)


_bootstrap_frozen_env()

from app.core.config import settings
from app.core.version import app_version
from app.main import app


def _open_browser_when_ready() -> None:
    browser_host = "127.0.0.1" if settings.host in {"0.0.0.0", "::"} else settings.host
    url = f"http://{browser_host}:{settings.port}"
    health_url = f"{url}/api/health"
    for _ in range(40):
        try:
            with urllib.request.urlopen(health_url, timeout=1) as resp:
                if resp.status == 200:
                    webbrowser.open(url)
                    return
        except Exception:
            time.sleep(0.5)
    webbrowser.open(url)


def _run_with_control_window() -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox
    except Exception:
        uvicorn.run(app, host=settings.host, port=settings.port)
        return

    browser_host = "127.0.0.1" if settings.host in {"0.0.0.0", "::"} else settings.host
    url = f"http://{browser_host}:{settings.port}"
    config = uvicorn.Config(app, host=settings.host, port=settings.port)
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, name="ivyeaops-server", daemon=True)

    # ── Polished native window: rounded cards / badges / buttons drawn on a
    # Canvas (Tk widgets are square), inside a normal window so the taskbar entry
    # and the native title bar (min/max/close) stay. Status goes in the title.
    # Authored in 600x384 design coords; every primitive (rr/oval/line/rect/text)
    # is scaled by S to crisp integer pixels at draw time. No post-hoc cv.scale —
    # that put 1px borders on fractional pixels and blurred the text. Window ≈ 2/3.
    W, H, S = 600, 384, 0.66
    white = "#ffffff"
    cbord = "#e5e7eb"      # card border
    badge_bg = "#dcfce7"   # light-green icon badge
    label_fg = "#4b5563"
    val_fg = "#374151"
    faint = "#9ca3af"
    green = "#16a34a"
    green_hi = "#15803d"
    amber_bg = "#fffbeb"
    amber_bd = "#fde68a"
    amber_fg = "#b45309"
    red = "#dc2626"
    red_bd = "#fca5a5"
    red_hi = "#fef2f2"
    ui = "Microsoft YaHei UI"
    mono = "Consolas"

    root = tk.Tk()
    root.title("IvyeaOps")
    root.resizable(False, False)
    WS, HS = int(W * S), int(H * S)
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{WS}x{HS}+{(sw - WS) // 2}+{(sh - HS) // 3}")
    try:
        root.iconbitmap(str(_runtime_root() / "client" / "public" / "favicon.ico"))
    except Exception:
        pass
    root.configure(bg=white)

    cv = tk.Canvas(root, width=WS, height=HS, bg=white, highlightthickness=0, bd=0)
    cv.pack(fill="both", expand=True)

    def s(n):
        return round(n * S)

    def fui(n, *a):
        return (ui, max(7, round(n * S)), *a)

    def fmono(n):
        return (mono, max(7, round(n * S)))

    def _w(kw):
        if "width" in kw:
            kw["width"] = max(1, round(kw["width"] * S))
        return kw

    # Primitive wrappers: author in design coords, draw at scaled integer pixels.
    def oval(x1, y1, x2, y2, **kw):
        cv.create_oval(s(x1), s(y1), s(x2), s(y2), **_w(kw))

    def line(x1, y1, x2, y2, **kw):
        cv.create_line(s(x1), s(y1), s(x2), s(y2), **_w(kw))

    def rect(x1, y1, x2, y2, **kw):
        cv.create_rectangle(s(x1), s(y1), s(x2), s(y2), **_w(kw))

    def text(x, y, **kw):
        cv.create_text(s(x), s(y), **kw)

    def rr(x1, y1, x2, y2, r, fill, tags=()):
        x1, y1, x2, y2, r = s(x1), s(y1), s(x2), s(y2), s(r)
        cv.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline=fill, tags=tags)
        cv.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline=fill, tags=tags)
        for ax, ay, st in ((x1, y1, 90), (x2 - 2 * r, y1, 0),
                           (x1, y2 - 2 * r, 180), (x2 - 2 * r, y2 - 2 * r, 270)):
            cv.create_arc(ax, ay, ax + 2 * r, ay + 2 * r, start=st, extent=90,
                          style="pieslice", fill=fill, outline=fill, tags=tags)

    def card(x1, y1, x2, y2, r, fill, border):
        rr(x1, y1, x2, y2, r, border)
        rr(x1 + 1, y1 + 1, x2 - 1, y2 - 1, r - 1, fill)

    stopping = False

    def open_browser() -> None:
        webbrowser.open(url)

    def copy_url() -> None:
        try:
            root.clipboard_clear(); root.clipboard_append(url)
        except Exception:
            pass

    def stop_server() -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        root.title("IvyeaOps · 正在停止…")
        server.should_exit = True

        def finish() -> None:
            server_thread.join(timeout=8)
            root.after(0, root.destroy)

        threading.Thread(target=finish, daemon=True).start()

    # Main card (address + version rows)
    card(24, 24, W - 24, 192, 14, white, cbord)
    # address badge + globe icon
    rr(44, 52, 84, 92, 10, badge_bg)
    gx, gy = 64, 72
    oval(gx - 12, gy - 12, gx + 12, gy + 12, outline=green, width=2)
    oval(gx - 5, gy - 12, gx + 5, gy + 12, outline=green, width=1)
    line(gx - 12, gy, gx + 12, gy, fill=green, width=1)
    text(104, gy, text="地址", anchor="w", fill=label_fg, font=fui(12))
    text(168, gy, text=url, anchor="w", fill=green, font=fmono(13),
         tags=("link",))
    # copy button
    rr(468, 54, 560, 90, 9, cbord, ("copy", "copy_bg"))
    rr(469, 55, 559, 89, 8, white, ("copy", "copy_bg"))
    text(514, 72, text="⧉  复制", fill=val_fg, font=fui(11), tags=("copy",))
    # divider
    line(44, 122, W - 44, 122, fill=cbord)
    # version badge + box icon
    rr(44, 138, 84, 178, 10, badge_bg)
    bx, by = 64, 158
    rect(bx - 11, by - 9, bx + 11, by + 9, outline=green, width=2)
    line(bx - 11, by - 2, bx + 11, by - 2, fill=green, width=1)
    line(bx, by - 9, bx, by - 2, fill=green, width=1)
    text(104, by, text="版本", anchor="w", fill=label_fg, font=fui(12))
    text(168, by, text=app_version(), anchor="w", fill=val_fg, font=fmono(12))

    # Warning bar
    card(24, 212, W - 24, 256, 10, amber_bg, amber_bd)
    text(46, 234, text="⚠", fill=amber_fg, font=fui(13), anchor="w")
    text(72, 234, text="关闭窗口将停止后台服务", fill=amber_fg,
         font=fui(10), anchor="w")

    # Buttons
    rr(24, 280, 292, 356, 13, green, ("open", "open_bg"))
    text(158, 318, text=">_   打开控制台", fill=white,
         font=fui(13, "bold"), tags=("open",))
    rr(308, 280, W - 24, 356, 13, red_bd, ("stop", "stop_ring"))
    rr(309, 281, W - 25, 355, 12, white, ("stop", "stop_bg"))
    text(442, 318, text="⏻   停止并退出", fill=red,
         font=fui(13, "bold"), tags=("stop",))

    def _hover(tag_bg, normal, hi):
        cv.tag_bind(tag_bg.replace("_bg", "").replace("_ring", ""), "<Enter>",
                    lambda _e: (cv.itemconfigure(tag_bg, fill=hi, outline=hi),
                                cv.config(cursor="hand2")))
        cv.tag_bind(tag_bg.replace("_bg", "").replace("_ring", ""), "<Leave>",
                    lambda _e: (cv.itemconfigure(tag_bg, fill=normal, outline=normal),
                                cv.config(cursor="")))

    cv.tag_bind("open", "<Button-1>", lambda _e: open_browser())
    _hover("open_bg", green, green_hi)
    cv.tag_bind("stop", "<Button-1>", lambda _e: stop_server())
    _hover("stop_bg", white, red_hi)
    cv.tag_bind("copy", "<Button-1>", lambda _e: copy_url())
    _hover("copy_bg", white, "#f3f4f6")
    cv.tag_bind("link", "<Button-1>", lambda _e: open_browser())
    cv.tag_bind("link", "<Enter>", lambda _e: cv.config(cursor="hand2"))
    cv.tag_bind("link", "<Leave>", lambda _e: cv.config(cursor=""))

    root.protocol("WM_DELETE_WINDOW", stop_server)
    server_thread.start()
    threading.Thread(target=_open_browser_when_ready, daemon=True).start()

    def poll() -> None:
        if stopping:
            return
        if server_thread.is_alive():
            running = _already_running(settings.host, settings.port)
            root.title("IvyeaOps · 运行中" if running else "IvyeaOps · 启动中…")
            root.after(1000, poll)
            return
        root.title("IvyeaOps · 已停止")
        messagebox.showwarning("IvyeaOps", "IvyeaOps 服务已停止。如需排错，请查看 logs\\ivyeaops.err.log。")
        root.destroy()

    root.after(1000, poll)
    root.mainloop()
    if server_thread.is_alive() and not server.should_exit:
        server.should_exit = True
        server_thread.join(timeout=8)


if __name__ == "__main__":
    if getattr(sys, "frozen", False) and _already_running(settings.host, settings.port):
        browser_host = "127.0.0.1" if settings.host in {"0.0.0.0", "::"} else settings.host
        webbrowser.open(f"http://{browser_host}:{settings.port}")
        sys.exit(0)
    if _control_window_enabled():
        _run_with_control_window()
        sys.exit(0)
    if getattr(sys, "frozen", False) and os.getenv("IVYEA_OPS_SERVER_OPEN_BROWSER", "1") != "0":
        threading.Thread(target=_open_browser_when_ready, daemon=True).start()
    uvicorn.run(app, host=settings.host, port=settings.port)
