"""IvyeaAgent 的 MCP 注册表（~/.ivyea/mcp.json）读写。

为什么不走 agent daemon：serve 只暴露了 `/v1/mcp/self-config`，没有增删查。
而 IvyeaOps 本来就在直接写这个文件（hermes_config_sync.sync_agent_mcp 把数据源
密钥同步进去），两边同一台机器、同一个 HOME 约定。所以这里沿用同一条路，
不为了一个配置读写再逼 agent 发一次版。

agent 侧每次调 MCP 都重新读盘（tools_general._mcp_servers → config.load_mcp），
所以写完立即生效，不用重启 daemon。

⚠️ 写入即赋予执行能力：stdio 型 server 的 `command` 会被 agent 拿去起进程。
所以路由层把写操作限定为管理员。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

# 与 hermes_config_sync 用同一个解析规则，两处不能对同一个文件有不同理解。
from app.services.hermes_config_sync import _agent_mcp_file  # noqa: PLC2701

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

# 这几台由「系统配置 → 数据源」的密钥自动同步进来（sync_agent_mcp）。删掉后
# 下次保存设置又会回来 —— UI 得把这件事说清楚，否则用户会以为删除失败。
MANAGED_SERVERS = {"sorftime", "sif_mcp", "sellersprite"}

TRANSPORTS = ("http", "sse", "stdio")


class AgentMCPError(RuntimeError):
    pass


def _load() -> dict[str, Any]:
    path = _agent_mcp_file()
    try:
        data = json.loads(path.read_text("utf-8")) if path.exists() else {}
    except Exception:  # noqa: BLE001 — 文件损坏时按空处理，不要让整页 500
        data = {}
    if not isinstance(data, dict):
        data = {}
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        data["mcpServers"] = {}
    return data


def _save(data: dict[str, Any]) -> None:
    path = _agent_mcp_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)     # 里面有 key / Authorization 头
    except OSError:
        pass


def _redact(spec: dict[str, Any]) -> dict[str, Any]:
    """脱敏后的展示副本：URL 里的 key、Authorization 头、env 值都不回给前端。"""
    out: dict[str, Any] = {}
    for key, val in (spec or {}).items():
        if key == "url":
            out[key] = re.sub(r"(key=)[^&]+", r"\1***", str(val))
        elif key == "headers" and isinstance(val, dict):
            out[key] = {k: ("***" if k.lower() == "authorization" else v) for k, v in val.items()}
        elif key == "env" and isinstance(val, dict):
            out[key] = {k: "***" for k in val}
        else:
            out[key] = val
    return out


def list_servers() -> list[dict[str, Any]]:
    """agent 的 MCP 清单（已脱敏）。"""
    servers = _load()["mcpServers"]
    rows: list[dict[str, Any]] = []
    for name in sorted(servers):
        spec = servers[name] if isinstance(servers[name], dict) else {}
        rows.append({
            "name": name,
            "transport": str(spec.get("transport") or ("stdio" if spec.get("command") else "http")),
            "trusted": bool(spec.get("trusted")),
            "managed": name in MANAGED_SERVERS,
            "has_data_source": bool(spec.get("dataSource")),
            "spec": _redact(spec),
        })
    return rows


def validate_name(name: str) -> str:
    name = (name or "").strip()
    if not name or not _NAME_RE.match(name):
        raise AgentMCPError("非法服务器名（只允许字母、数字、_ . -）")
    return name


def upsert_server(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    """新增或更新一台 MCP 服务器。

    合并而非替换：只覆盖这次真的传了值的字段。这样在 UI 里改个 transport
    不会把用户当初用 `ivyea mcp add` 配好的 dataSource / writeActions 抹掉。
    """
    name = validate_name(name)
    transport = str(spec.get("transport") or "").strip().lower()
    if transport not in TRANSPORTS:
        raise AgentMCPError(f"transport 必须是 {', '.join(TRANSPORTS)} 之一")

    data = _load()
    servers = data["mcpServers"]
    current = servers.get(name)
    merged: dict[str, Any] = dict(current) if isinstance(current, dict) else {}
    merged["transport"] = transport
    for key in ("url", "command", "trusted"):
        if spec.get(key) not in (None, ""):
            merged[key] = spec[key]
    if isinstance(spec.get("args"), list):
        merged["args"] = [str(a) for a in spec["args"]]
    if isinstance(spec.get("headers"), dict) and spec["headers"]:
        merged["headers"] = {**(merged.get("headers") or {}), **spec["headers"]}
    if isinstance(spec.get("env"), dict) and spec["env"]:
        merged["env"] = {**(merged.get("env") or {}), **spec["env"]}
    # 换传输方式时把用不上的字段清掉，免得留下会误导人的残余配置
    if transport == "stdio":
        merged.pop("url", None)
    else:
        merged.pop("command", None)
        merged.pop("args", None)
    merged["trusted"] = bool(spec["trusted"]) if "trusted" in spec else bool(merged.get("trusted"))

    # 必填校验对着**合并后**的结果做，不是对着这次传了什么做 —— 否则「只改一下
    # trusted」这种局部更新会因为没重复带 url 而被打回，和上面承诺的合并语义自相矛盾。
    if transport == "stdio":
        if not str(merged.get("command") or "").strip():
            raise AgentMCPError("stdio 传输需要 command")
    elif not str(merged.get("url") or "").strip():
        raise AgentMCPError(f"{transport} 传输需要 url")

    servers[name] = merged
    _save(data)
    return {"name": name, "spec": _redact(merged)}


def remove_server(name: str) -> bool:
    name = validate_name(name)
    data = _load()
    servers = data["mcpServers"]
    if name not in servers:
        return False
    del servers[name]
    _save(data)
    return True


def claude_servers() -> list[dict[str, Any]]:
    """Claude Code 的 MCP 清单，只读展示 —— 两套注册表容易混，页面上要分开摆。

    读不到（没装 Claude / 文件不存在）时返回空列表，不当作错误。
    """
    path = Path(os.path.expanduser("~")) / ".claude.json"
    try:
        data = json.loads(path.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return []
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return []
    rows = []
    for name in sorted(servers):
        spec = servers[name] if isinstance(servers[name], dict) else {}
        rows.append({
            "name": name,
            "transport": str(spec.get("type") or spec.get("transport")
                             or ("stdio" if spec.get("command") else "http")),
            "spec": _redact(spec),
        })
    return rows
