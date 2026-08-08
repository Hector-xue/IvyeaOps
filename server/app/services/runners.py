"""Shared agent-runner helpers used by audit services.

The IvyeaOps workbench spawns agent CLIs (hermes / codex / claude) as
subprocesses to execute skills. Both the ASIN audit and the ad-report audit
share the same runner selection and invocation logic, so we lift it into a
standalone module.

Design notes
------------
- systemd runs the service with a minimal PATH that misses ~/.hermes/node/bin
  and ~/.local/bin, so we search a richer list ourselves via ``_find_bin``.
- ``_resolve_runner`` picks the first available CLI from ``RUNNER_ORDER``.
- ``runner_status`` exposes availability info for the UI selector.
- ``build_child_env`` prepares the env for the child process so the runner
  can spawn its own helpers (claude spawns node, hermes reads ~/.hermes, etc).
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

def _extra_paths() -> list[str]:
    """PATH dirs that hold agent CLIs beyond systemd's minimal default.

    Sources, in order:
      1. hub_settings (hermes_node_bin, bun_bin)
      2. ~/.hermes/node/bin and ~/.local/bin (works for non-root installs)
      3. The standard /usr/local/bin and /usr/bin.
    """
    from app.core import integrations
    return [
        *integrations.extra_path_dirs(),
        str(Path.home() / ".hermes" / "node" / "bin"),
        str(Path.home() / ".local" / "bin"),
        "/usr/local/bin",
        "/usr/bin",
    ]

# Ordered runner preference (auto-pick walks this list). IvyeaAgent 首选（自托管、自带亚马逊域）。
# 2026-08-06：hermes 从自动挑选序列里移除——它不再是任何板块的默认或兜底。
# _find_bin("hermes") / _build_runner_cmd("hermes", …) 仍然保留，供 /agents 板块里
# 用户手动选择 hermes provider 时使用；只是系统自己不会再走到它。
RUNNER_ORDER = ("ivyea-agent", "codex", "claude")

# Human-friendly labels shown in the UI selector.
RUNNER_LABELS = {
    "ivyea-agent": "IvyeaAgent（推荐 · 自托管 · 亚马逊域 · 自带 MCP）",
    "hermes": "Hermes（仅手动选择）",
    "codex":  "Codex（OpenAI）",
    "claude": "Claude Code",
}


def _find_bin(name: str) -> Optional[str]:
    """Locate an executable for runner ``name`` even when systemd's PATH is thin."""
    # First honor hub_settings explicit binary overrides.
    from app.core import integrations
    direct_lookup = {
        "hermes": integrations.hermes_bin,
        "codex":  integrations.codex_bin,
        "claude": integrations.claude_bin,
        "kiro-cli": integrations.kiro_cli_bin,
    }.get(name)
    if direct_lookup:
        configured = direct_lookup()
        if configured:
            return configured
    # ivyea-agent 的可执行是 `ivyea`（launcher / venv / Scripts），runner 名与 exe 名不同。
    exe = "ivyea" if name == "ivyea-agent" else name
    p = shutil.which(exe)
    if p:
        return p
    # Fallback over known dirs. On Windows the executable is `exe.exe` (or .cmd/
    # .bat) and os.access(..., X_OK) is unreliable — so try those suffixes and
    # don't gate on the exec bit. This is why a freshly-installed hermes.exe was
    # not detected (status stayed "需安装/修复") when it wasn't on PATH.
    win = sys.platform == "win32"
    candidates = [exe, exe + ".exe", exe + ".cmd", exe + ".bat"] if win else [exe]
    for root in _extra_paths():
        for n in candidates:
            cand = Path(root) / n
            if cand.is_file() and (win or os.access(cand, os.X_OK)):
                return str(cand)
    return None


def _resolve_runner() -> tuple[Optional[str], Optional[str]]:
    """Return ``(runner_name, absolute_path)`` for the first available CLI."""
    for name in RUNNER_ORDER:
        p = _find_bin(name)
        if p:
            return name, p
    return None, None


# ivyea-agent ≥1.2.0 支持 `chat -p --output-format stream-json`（NDJSON 事件，对齐
# Claude Code）。用 `chat --help` 探测一次并缓存，旧版自动回退纯文本 argv。
_IVYEA_HELP_CACHE: Dict[str, str] = {}


def _ivyea_chat_help(binary: str) -> str:
    """Return (cached) `ivyea chat --help` text; empty string on any failure."""
    if binary in _IVYEA_HELP_CACHE:
        return _IVYEA_HELP_CACHE[binary]
    import subprocess
    from app.core.proc import no_window_kwargs
    try:
        proc = subprocess.run([binary, "chat", "--help"], capture_output=True, text=True,
                              timeout=30, env=build_child_env(binary), **no_window_kwargs())
        text = (proc.stdout or "") + (proc.stderr or "")
    except Exception:
        text = ""
    _IVYEA_HELP_CACHE[binary] = text
    return text


def ivyea_stream_json_supported(binary: str) -> bool:
    return "--output-format" in _ivyea_chat_help(binary)


def _ivyea_permission_args(binary: str) -> List[str]:
    """无人值守审批档：默认 --approve-all（与历史行为一致，xlsx 方案等需要写文件）。
    运维在 ~/.ivyea/policy.json 配好写根/命令白名单后，可设环境变量
    IVYEA_OPS_IVYEA_PERMISSION_MODE=policy 切到 policy 档（单工具拒绝不终止整轮）。"""
    mode = (os.environ.get("IVYEA_OPS_IVYEA_PERMISSION_MODE") or "approve-all").strip().lower()
    if mode == "policy" and "--permission-mode" in _ivyea_chat_help(binary):
        return ["--permission-mode", "policy"]
    return ["--approve-all"]


def _build_runner_cmd(runner: str, binary: str, prompt: str) -> List[str]:
    """Build the subprocess argv for the given runner + prompt."""
    if runner == "ivyea-agent":
        # -p: 非交互一次性，结果打 stdout。新版走 stream-json 拿结构化过程事件。
        argv = [binary, "chat", "-p", prompt, *_ivyea_permission_args(binary)]
        if ivyea_stream_json_supported(binary):
            argv += ["--output-format", "stream-json"]
        return argv
    if runner == "hermes":
        # -z: one-shot prompt, reply to stdout, no TUI.
        return [binary, "-z", prompt]
    if runner == "codex":
        # exec: non-interactive mode.
        return [binary, "exec", prompt]
    # claude
    return [binary, "--print", "--permission-mode", "bypassPermissions", prompt]


def runner_status() -> List[Dict[str, Any]]:
    """Report availability of each runner for the UI selector.

    Returns a list starting with an ``auto`` row (indicating which runner
    auto-pick would resolve to), followed by one row per canonical runner.
    """
    rows: List[Dict[str, Any]] = []
    for name in RUNNER_ORDER:
        p = _find_bin(name)
        rows.append({
            "name": name,
            "label": RUNNER_LABELS.get(name, name),
            "available": bool(p),
            "path": p,
            "reason": None if p else "未安装",
        })
    auto_name, _ = _resolve_auto()
    rows.insert(0, {
        "name": "auto",
        "label": f"自动（当前：{auto_name or '无可用'}）",
        "available": auto_name is not None,
        "path": None,
        "reason": None if auto_name else "未找到任何可用的 CLI",
        "auto_resolved_to": auto_name,
    })
    return rows


def _audit_default_runner() -> str:
    """Configured default runner for audits (settings ``audit_default_runner``).

    Defaults to ``hermes`` — the only runner IvyeaOps wires up with both the
    skill (seeded to ~/.hermes/skills) and the data-source MCP (sorftime/sif_mcp
    in ~/.hermes/config.yaml), so audits get skill guidance + real data and can
    emit the structured JSON the UI parses. Falls back through RUNNER_ORDER.
    """
    try:
        from app.core import hub_settings as _hs
        return (str(_hs.get("audit_default_runner") or "").strip().lower()) or "hermes"
    except Exception:
        return "hermes"


def _resolve_auto() -> tuple[Optional[str], Optional[str]]:
    """Auto-pick: configured default (hermes) first, then RUNNER_ORDER."""
    default = _audit_default_runner()
    for cand in [default, *RUNNER_ORDER]:
        if not cand:
            continue
        p = _find_bin(cand)
        if p:
            return cand, p
    return None, None


def resolve_with_pref(pref: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Resolve a runner preference to ``(runner_name, binary_path, error)``.

    ``pref`` may be ``"auto"`` or one of :data:`RUNNER_ORDER`.
    Returns ``(name, path, None)`` on success; ``(None, None, error_message)``
    on failure.
    """
    pref = (pref or "auto").lower()
    # 2026-08-06：hermes 已移出 RUNNER_ORDER。历史 job / 旧前端里存下来的
    # runner_pref="hermes" 不该直接报 unknown runner 把任务打死，退回 auto
    # （会落到 ivyea-agent）。
    if pref == "hermes":
        pref = "auto"
    if pref == "auto":
        name, path = _resolve_auto()
        if not name:
            return None, None, "no agent CLI is available on this host"
        return name, path, None
    if pref in RUNNER_ORDER:
        path = _find_bin(pref)
        if not path:
            return None, None, f"runner '{pref}' is not available"
        return pref, path, None
    return None, None, f"unknown runner: {pref}"


def build_child_env(runner_bin: str) -> Dict[str, str]:
    """Prepare the env for a runner subprocess.

    - Prepends the runner's own directory to ``PATH`` so it can spawn helpers
      (claude spawns node, hermes spawns MCP servers, etc).
    - Ensures ``HOME`` is set (systemd minimal env can drop it).
    - Sets ``IS_SANDBOX=1`` so claude's --dangerously-skip-permissions check
      doesn't refuse when running as root.
    """
    # 从 os.environ 全量复制改成走 core.proc.child_env：这条路径跑的是 AI 自己
    # 决定的工具调用（hermes/claude/codex + 它们再拉起的 MCP server），是整个
    # 系统里最不该看到 IVYEA_OPS_SECRET 的地方 —— 拿到它就能伪造管理员会话。
    from app.core.proc import child_env as _scrubbed_env
    child_env = _scrubbed_env()
    bin_dir = str(Path(runner_bin).parent)
    if bin_dir not in child_env.get("PATH", "").split(os.pathsep):
        child_env["PATH"] = bin_dir + os.pathsep + child_env.get("PATH", "")
    child_env.setdefault("HOME", str(Path.home()))
    child_env.setdefault("IS_SANDBOX", "1")
    return child_env


# ---------------------------------------------------------------------------
# ivyea-agent stream-json 解析：把 NDJSON 事件流还原成 最终文本 + 过程事件 + 花费。
# 事件 schema 对齐 Claude Code：system/init → assistant(text/tool_use) →
# user(tool_result) → result（费用字段是 total_cost_cny，人民币）。
# 非 JSON 行（stderr 混入/旧版纯文本）一律跳过；没有 result 事件时降级为原文透传。
# ---------------------------------------------------------------------------

_TOOL_RESULT_KEEP = 2000   # steps 里工具结果保留的最大字符（防 steps.json 爆炸）


class IvyeaStreamJsonParser:
    """行缓冲增量解析器。feed() 喂原始 chunk，随时读 .events / .progress / .result_event。"""

    def __init__(self) -> None:
        self._buf = ""
        self.events: List[Dict[str, Any]] = []   # 压缩后的过程事件（tool_use/tool_result/text）
        self.progress: str = ""                  # 最近一次人读进度（"正在调用 xxx…"）
        self.result_event: Optional[Dict[str, Any]] = None
        self.session_id: str = ""
        self.saw_json = False                    # 是否见过任何合法事件（判定新旧 CLI）

    def feed(self, chunk: str) -> None:
        self._buf += chunk
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._handle_line(line.strip())

    def close(self) -> None:
        if self._buf.strip():
            self._handle_line(self._buf.strip())
        self._buf = ""

    def _handle_line(self, line: str) -> None:
        if not line.startswith("{"):
            return
        import json as _json
        try:
            ev = _json.loads(line)
        except ValueError:
            return
        if not isinstance(ev, dict):
            return
        et = ev.get("type")
        if et == "system" and ev.get("subtype") == "init":
            self.saw_json = True
            self.session_id = str(ev.get("session_id") or "")
            return
        if et == "assistant":
            self.saw_json = True
            for block in (ev.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use":
                    self.events.append({"type": "tool_use", "name": block.get("name") or "",
                                        "input": block.get("input") or {}})
                    self.progress = f"正在调用 {block.get('name') or '工具'}…"
                elif block.get("type") == "text" and (block.get("text") or "").strip():
                    self.events.append({"type": "text", "text": block["text"]})
            return
        if et == "user":
            for block in (ev.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_result":
                    self.events.append({
                        "type": "tool_result", "tool_use_id": block.get("tool_use_id") or "",
                        "is_error": bool(block.get("is_error")),
                        "content": str(block.get("content") or "")[:_TOOL_RESULT_KEEP],
                    })
            return
        if et == "result":
            self.saw_json = True
            self.result_event = ev

    @property
    def final_text(self) -> str:
        return str((self.result_event or {}).get("result") or "")

    @property
    def cost_cny(self) -> Optional[float]:
        v = (self.result_event or {}).get("total_cost_cny")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None


def extract_runner_output(runner: str, raw: str) -> Dict[str, Any]:
    """统一的 runner stdout 后处理。

    返回 {"text", "events", "cost_cny", "session_id", "structured"}：
    - ivyea-agent 且 stdout 是 stream-json → text=result 事件里的最终答案，
      events=过程事件（工具调用/结果/中间文本），structured=True。
    - 其它 runner / 旧版纯文本 / 解析不出 result → text=原文透传，structured=False。
    """
    if runner == "ivyea-agent" and '"type"' in raw:
        # 不按首行判定（stderr 告警可能混在最前面），解析器本身会跳过所有非 JSON 行。
        p = IvyeaStreamJsonParser()
        p.feed(raw)
        p.close()
        if p.result_event is not None:
            return {"text": p.final_text, "events": p.events, "cost_cny": p.cost_cny,
                    "session_id": p.session_id, "structured": True}
    return {"text": raw, "events": [], "cost_cny": None, "session_id": "", "structured": False}
