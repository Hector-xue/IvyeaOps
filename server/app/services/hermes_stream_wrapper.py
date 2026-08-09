#!/usr/bin/env python3
"""Streaming wrapper for hermes AIAgent.

Reads the prompt from stdin, calls AIAgent.chat() with a stream_callback
that writes each text delta to stdout immediately — giving true token-by-token
streaming that hermes -z intentionally suppresses.

Key design choices:
  * discover_mcp_tools() called before AIAgent init so sorftime MCP tools are
    registered. AIAgent.__init__ does NOT call this; only the CLI entrypoint
    does. Without it, enabled_toolsets=["sorftime"] has nothing to enable.
  * enabled_toolsets=["sorftime"] → only sorftime MCP tools available; no
    web-search or shell tools that stall on large prompts.
  * stream_callback is wired → tokens flow out per-token.
  * Model/provider come from hermes's own config.yaml.

Usage (called by IvyeaOps ai_synthesis_service):
    echo "prompt" | python hermes_stream_wrapper.py

Exit codes:
    0  success
    1  error — message on stderr
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("ivyea.services.hermes_stream_wrapper")

# ── Path bootstrap (mirrors hermes_cli/main.py) ────────────────────────────
_HERMES_ROOT = Path.home() / ".hermes" / "hermes-agent"
if str(_HERMES_ROOT) not in sys.path:
    sys.path.insert(0, str(_HERMES_ROOT))

# Silence all loggers — only the streamed response text belongs on stdout.
logging.disable(logging.CRITICAL)

os.environ["HERMES_YOLO_MODE"] = "1"
os.environ["HERMES_ACCEPT_HOOKS"] = "1"


def main() -> int:
    prompt = sys.stdin.read()
    if not prompt.strip():
        sys.stderr.write("hermes_stream_wrapper: empty prompt on stdin\n")
        return 1

    devnull_w = open(os.devnull, "w", encoding="utf-8")
    old_stderr = sys.stderr
    sys.stderr = devnull_w

    try:
        from hermes_cli.config import load_config
        from hermes_cli.runtime_provider import resolve_runtime_provider
        from run_agent import AIAgent

        # Discover MCP tools (sorftime, sif_mcp, etc.) before creating the
        # agent. AIAgent.__init__ does not call this; it is normally done by
        # hermes_cli/main.py at CLI startup. Without this call,
        # enabled_toolsets=["sorftime"] has nothing to enable and the agent
        # has no access to any sorftime tool.
        try:
            from tools.mcp_tool import discover_mcp_tools
            discover_mcp_tools()
        except Exception:
            logger.debug("discover_mcp_tools 失败（旁路，已忽略）", exc_info=True)

        cfg = load_config()
        model_cfg = cfg.get("model") or {}
        if isinstance(model_cfg, str):
            cfg_model = model_cfg
        else:
            cfg_model = model_cfg.get("default") or model_cfg.get("model") or ""

        runtime = resolve_runtime_provider(
            requested=None,
            target_model=cfg_model or None,
        )

        try:
            from hermes_cli.oneshot import _create_session_db_for_oneshot
            session_db = _create_session_db_for_oneshot()
        except Exception:
            session_db = None

        agent = AIAgent(
            api_key=runtime.get("api_key"),
            base_url=runtime.get("base_url"),
            provider=runtime.get("provider"),
            api_mode=runtime.get("api_mode"),
            model=cfg_model,
            # Only sorftime MCP tools — no web-search / shell tools that would
            # stall for minutes on a research prompt.
            enabled_toolsets=["sorftime"],
            quiet_mode=True,
            platform="cli",
            session_db=session_db,
        )

        agent.suppress_status_output = True
        agent.tool_gen_callback = None

        real_stdout = sys.stdout

        def _stream_cb(delta: str) -> None:
            real_stdout.write(delta)
            real_stdout.flush()

        agent.chat(prompt, stream_callback=_stream_cb)
        return 0

    except Exception as exc:
        sys.stderr = old_stderr
        sys.stderr.write(f"hermes_stream_wrapper error: {exc}\n")
        return 1
    finally:
        sys.stderr = old_stderr
        devnull_w.close()


if __name__ == "__main__":
    sys.exit(main())
