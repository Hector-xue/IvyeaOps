"""Agent registry: static catalog + runtime discovery.

We support a small fixed set out of the box (hermes, codex, claude-code,
kiro-cli). Adding a new agent is two steps:

  1. Drop a new entry into AGENT_DEFS below.
  2. Restart IvyeaOps. discover_agents() will probe and persist.

Each AgentDef captures everything pty_manager and the chat/SSE router need
to know to launch and converse with the binary:

  - bin / fallback_paths : how to find the executable.
  - models               : the model list shown to the user (static, with an
                           optional dynamic merge from kiro-gateway).
  - resume_strategy      : how to wake a dormant session.
  - cli_args / chat_args : argv templates for the two modes.
  - prompt_regex         : where the binary's interactive prompt ends, so we
                           can chunk PTY output into "assistant turns".

Discovery is best-effort. A missing binary is recorded as enabled=False so
the UI can grey-out the card without breaking other agents.
"""
from __future__ import annotations

from app.core.proc import no_window_kwargs

import os
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.services import agent_session_service as svc

# ---------------------------------------------------------------------------
# Optional legacy gateway config. Leave unset by default: the old local
# kiro gateway on :8000 has been retired.
# ---------------------------------------------------------------------------
KIRO_GATEWAY_URL = os.environ.get("IVYEA_OPS_KIRO_GATEWAY", "").strip()
from app.core import secret_env as _secret_env

logger = logging.getLogger("ivyea.services.agent_registry")

KIRO_GATEWAY_KEY = _secret_env.get("IVYEA_OPS_KIRO_GATEWAY_KEY", "hermes2024")


def _list_kiro_models() -> list[str]:
    """Best-effort fetch of available models from a legacy kiro-style gateway.

    When no legacy gateway is configured we simply return an empty list.
    """
    if not KIRO_GATEWAY_URL:
        return []
    try:
        r = httpx.get(
            f"{KIRO_GATEWAY_URL}/v1/models",
            headers={"Authorization": f"Bearer {KIRO_GATEWAY_KEY}"},
            timeout=2.5,
        )
        if r.status_code != 200:
            return []
        data = r.json().get("data", [])
        return [m["id"] for m in data if isinstance(m, dict) and "id" in m]
    except Exception:
        return []


_KIRO_MODELS_FALLBACK = []


# ---------------------------------------------------------------------------
# Agent definitions
# ---------------------------------------------------------------------------
@dataclass
class AgentDef:
    id: str
    display_name: str
    bin_candidates: list[str]
    default_model: str | None
    static_models: list[str] = field(default_factory=list)
    use_kiro_models: bool = False  # merge in kiro-gateway model catalog
    # CLI mode — interactive PTY argv. {workdir}/{model} expanded at launch time.
    cli_args: list[str] = field(default_factory=list)
    # Chat mode — non-interactive one-shot argv (preferred when supported).
    # If None, chat falls back to "write into the session PTY".
    chat_args: list[str] | None = None
    # When True, do NOT auto-append --model/-m to the chat-mode argv.  Some
    # agents (hermes) silently fail when given a model via the -z flag and
    # we must rely on their persisted default instead.
    chat_skip_model: bool = False
    # When True, chat mode emits newline-delimited stream-json events (claude
    # `--output-format stream-json`) rather than plain text. The router uses a
    # structured handler (_chat_stream_json) that surfaces tool calls/results.
    chat_stream_json: bool = False
    # Regex patterns (re.MULTILINE | re.DOTALL) to strip from the final
    # accumulated chat-mode output.  Used to drop banners / footers / shell
    # prompt artifacts that the agent prints alongside its actual reply.
    chat_strip_patterns: list[str] = field(default_factory=list)
    # Optional regex with a (?P<answer>...) capture group.  When set, only
    # the captured group is kept as the assistant message; everything else
    # is discarded.  Used for codex which dumps a session header / footer
    # around the actual model response.
    chat_extract_pattern: str | None = None
    # How to wake a dormant session:
    #   "flag"   : add resume_flag to argv; resume_id arg gives the session id.
    #   "prompt" : prepend a context message to first user input.
    #   "none"   : no native support; we always prompt-inject.
    resume_strategy: str = "prompt"
    resume_flag: str | None = None  # e.g. "--resume" / "--resume-id"
    # Used to detect when the agent is ready for the next user message.
    prompt_regex: str = r"\n[$#>] $"
    env: dict[str, str] = field(default_factory=dict)
    # Anything the renderer might want to know, surfaced in caps.
    caps_extra: dict[str, Any] = field(default_factory=dict)


AGENT_DEFS: dict[str, AgentDef] = {
    # ---- Hermes Agent ---------------------------------------------------
    "hermes": AgentDef(
        id="hermes",
        display_name="Hermes",
        # First candidate (if any) comes from hub_settings.hermes_bin at
        # discovery time; PATH lookup ("hermes") is the documented fallback.
        bin_candidates=["hermes"],
        default_model="gpt-5.4",
        static_models=[
            "gpt-5.4",
            "gpt-5.5",
            "anthropic/claude-sonnet-4.6",
            "anthropic/claude-sonnet-4.5",
            "anthropic/claude-haiku-4.5",
            "anthropic/claude-opus-4.5",
            "anthropic/claude-opus-4.6",
            "anthropic/claude-opus-4.7",
            "anthropic/deepseek-3.2",
            "anthropic/qwen3-coder-next",
            "anthropic/glm-5",
            "mimo-v2.5-pro",
            "mimo-v2.5",
        ],
        # Interactive: just `hermes chat`. We pass --model when given.
        cli_args=["chat"],
        # One-shot: `hermes -z PROMPT` is the documented oneshot flag.
        # NOTE: hermes silently swallows output when given `-m MODEL` together
        # with `-z` (we tested both `-m anthropic/...` and `-m claude-...`).
        # Skip the model flag in chat mode and let hermes use its persisted
        # default; the user can switch via `hermes model` interactively.
        chat_args=["-z", "{prompt}"],
        chat_skip_model=True,
        resume_strategy="flag",
        resume_flag="--resume",
        prompt_regex=r"hermes ?[>›❯]\s*$",
        caps_extra={"supports_oneshot": True, "supports_resume": True},
    ),
    # ---- Codex (OpenAI) -------------------------------------------------
    "codex": AgentDef(
        id="codex",
        display_name="Codex",
        bin_candidates=["codex"],
        default_model="gpt-5.5",
        # gpt-5.5 is what `codex` ships with for ChatGPT-account users; the
        # other listed names are forbidden in that mode but can be unlocked
        # via api-key login.  Keep them visible so users on api-key auth
        # still see the picker.
        static_models=["gpt-5.5", "gpt-5", "gpt-5-codex", "o3", "o4-mini", "codex-mini"],
        cli_args=[],  # `codex` alone enters interactive mode
        # `codex exec` is the non-interactive variant.
        chat_args=["exec", "{prompt}"],
        resume_strategy="flag",
        resume_flag="resume",  # `codex resume --last`
        prompt_regex=r"\n[›❯>]\s*$",
        # codex exec prints a session header (reasoning summaries / session id),
        # the user's echoed prompt, then `codex\n<reply>\ntokens used\n<n>`,
        # and finally repeats the reply.  Extract just the first model reply.
        chat_extract_pattern=r"\ncodex(?:[^\n]*)?\n(?P<answer>.+?)(?:\ntokens used\n|\Z)",
        caps_extra={"supports_oneshot": True, "supports_resume": True},
    ),
    # ---- Claude Code ----------------------------------------------------
    # The binary is a multi-platform wrapper that picks the linux-x64 native
    # binary at runtime. Calling the wrapper through node fails when run from
    # a non-shell (we saw "could not find native binary" earlier) so we point
    # straight at the platform binary.
    "claude": AgentDef(
        id="claude",
        display_name="Claude Code",
        # Per-install: the npm package's claude.exe shim is an error stub when
        # the postinstall didn't run, so users typically have to point at the
        # platform-specific binary directly. Configure via hub_settings.claude_bin.
        bin_candidates=["claude"],
        # Claude Code CLI accepts tier aliases (it resolves to the latest of
        # each tier) or a full id like `claude-opus-4-8`. Aliases never go
        # stale and always match the user's subscription, so we use them as
        # the safe default list. The actual configured model (from
        # ~/.claude/settings.json) is injected at the top by _live_models_for.
        default_model="default",
        static_models=["default", "opus", "sonnet", "haiku"],
        cli_args=[],
        # Structured chat: stream-json emits init/assistant/tool_use/tool_result/
        # result events line by line, so the UI can render the full agentic flow.
        # Input is ALSO stream-json (sent via stdin by _chat_stream_json), which
        # lets us pass real multimodal image blocks — hence no "{prompt}" arg
        # here. --resume (build_argv resume_id) keeps claude's own history.
        # --permission-mode is appended per-session by _chat_stream_json
        # (defaults to acceptEdits) so the user can switch策略 per session.
        chat_args=[
            "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
        ],
        chat_stream_json=True,
        resume_strategy="flag",
        resume_flag="--resume",
        prompt_regex=r"\n[>❯]\s*$",
        env={},
        caps_extra={"supports_oneshot": True, "supports_resume": True},
    ),
    # ---- Kiro CLI -------------------------------------------------------
    "kiro": AgentDef(
        id="kiro",
        display_name="Kiro CLI",
        bin_candidates=["kiro-cli"],
        # Live list comes from `kiro-cli chat --list-models`. These are only
        # an offline fallback, in kiro's real dotted format (not dashes).
        default_model="auto",
        static_models=["auto", "claude-sonnet-4.6", "claude-opus-4.6", "claude-haiku-4.5"],
        use_kiro_models=True,  # merge in gateway catalog when available
        # `kiro-cli chat` is the chat subcommand.
        cli_args=["chat"],
        # `kiro-cli chat --no-interactive "<prompt>"` prints the answer.
        chat_args=["chat", "--no-interactive", "--trust-all-tools", "{prompt}"],
        resume_strategy="flag",
        resume_flag="--resume-id",
        prompt_regex=r"\n[>❯]\s*$",
        # kiro chat prints a security banner, the prompt arrow `> `, and a
        # "▸ Credits: ..." footer around the actual answer.  Drop them.
        chat_strip_patterns=[
            r"^All tools are now trusted[\s\S]*?security/[^\n]*\n+",
            r"\n+\s*▸ Credits:[^\n]*",
            r"^>\s+",
        ],
        caps_extra={"supports_oneshot": True, "supports_resume": True},
    ),
    # ---- Antigravity (agy) ----------------------------------------------
    "agy": AgentDef(
        id="agy",
        display_name="Antigravity",
        bin_candidates=["agy", "antigravity"],
        default_model="default",
        static_models=["default"],
        cli_args=[],
        chat_args=["-p", "{prompt}"],
        chat_skip_model=True,  # Agy does not have --model flag yet
        resume_strategy="flag",
        resume_flag="--conversation",
        prompt_regex=r"\n(?:agy|Antigravity) ?[>›❯]\s*$",
        caps_extra={"supports_oneshot": True, "supports_resume": True},
    ),
    # ---- Ivyea Agent (native provider, driven by agents/ivyea_driver) ----
    # Registered so the deep-analysis / market-research agent picker
    # (/api/agents/catalog) can surface it. The actual chat is driven by the
    # native agents provider system (agents/routers/providers.py id="ivyea"),
    # not these cli_args — the model is configured server-side via `ivyea /model`.
    "ivyea": AgentDef(
        id="ivyea",
        display_name="Ivyea Agent",
        bin_candidates=[
            "ivyea",
            os.path.expanduser("~/.local/bin/ivyea"),
            os.path.expanduser("~/.ivyea/bin/ivyea"),
        ],
        default_model="default",
        static_models=["default"],
        cli_args=["chat"],
        chat_args=["chat", "-p", "{prompt}"],
        chat_skip_model=True,
        resume_strategy="prompt",
        caps_extra={"supports_oneshot": True, "native_provider": True},
    ),
}


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def _resolve_bin(candidates: list[str]) -> str | None:
    for c in candidates:
        if os.path.isabs(c) and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
        # Fall back to PATH lookup for relative names.
        if not os.path.isabs(c):
            found = shutil.which(c)
            if found:
                return found
    return None


# Per-agent hub_settings key holding a user-configured absolute path.
_INTEGRATION_KEYS = {
    "hermes": "hermes_bin",
    "codex":  "codex_bin",
    "claude": "claude_bin",
    "kiro":   "kiro_cli_bin",
    "agy":    "agy_bin",
}


def _candidates_for(adef: "AgentDef") -> list[str]:
    """Prepend a hub_settings override (if set) to the static candidate list."""
    from app.core import hub_settings as _hs
    extra: list[str] = []
    key = _INTEGRATION_KEYS.get(adef.id)
    if key:
        v = (_hs.get(key) or "").strip()
        if v:
            extra.append(v)
    return [*extra, *adef.bin_candidates]


def discover_agents() -> list[dict[str, Any]]:
    """Probe each defined agent, persist its projection, return runtime info.

    Called from app lifespan. Cheap enough to call again on demand if the
    user installs a new binary at runtime.
    """
    svc.init_db()
    # "重新探测" is an explicit user request for fresh data, so bust the kiro
    # model cache and force a live `--list-models` call (the passive picker
    # read keeps the 5-min cache for speed).
    global _kiro_models_cache
    _kiro_models_cache = (0.0, [])
    kiro_models = _list_kiro_models()

    discovered: list[dict[str, Any]] = []
    for adef in AGENT_DEFS.values():
        bin_path = _resolve_bin(_candidates_for(adef))
        models = list(adef.static_models)
        if adef.use_kiro_models:
            # Merge gateway catalog (when reachable) on top of the static
            # list, so kiro always has usable models even with no gateway.
            for m in (kiro_models or _KIRO_MODELS_FALLBACK):
                if m not in models:
                    models.append(m)

        # Prepend the agent's actual configured models (read live from its
        # own config), so they appear first and drive the default.
        configured = _live_models_for(adef.id)
        merged = _merge_models(adef.id, configured, models)
        default_model = configured[0] if configured else adef.default_model

        caps = {
            "cli": True,
            "chat": adef.chat_args is not None,
            "resume": adef.resume_strategy != "none",
            "binary_found": bin_path is not None,
            **adef.caps_extra,
        }
        if adef.id == "claude":
            caps["authenticated"] = _claude_authenticated()
        svc.upsert_agent(
            agent_id=adef.id,
            display_name=adef.display_name,
            binary_path=bin_path or "",
            default_model=default_model,
            models=merged,
            caps=caps,
            enabled=bin_path is not None,
        )
        discovered.append(
            {
                "id": adef.id,
                "display_name": adef.display_name,
                "binary_path": bin_path or "",
                "default_model": default_model,
                "models": merged,
                "caps": caps,
                "enabled": bin_path is not None,
            }
        )
    return discovered


def get_agent_def(agent_id: str) -> AgentDef:
    if agent_id not in AGENT_DEFS:
        raise KeyError(f"unknown agent: {agent_id}")
    return AGENT_DEFS[agent_id]


def _read_hermes_models() -> list[str]:
    """Read the actual model(s) hermes is configured to use from its own
    config.yaml. Returns [primary, fallback, ...] with no duplicates."""
    import yaml
    config_path = os.path.join(os.path.expanduser("~"), ".hermes", "config.yaml")
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:
        return []
    models: list[str] = []
    # Primary model
    model_block = cfg.get("model") or {}
    if isinstance(model_block, dict):
        m = (model_block.get("default") or "").strip()
        if m and m not in models:
            models.append(m)
    # Fallback providers
    for fb in cfg.get("fallback_providers") or []:
        if isinstance(fb, dict):
            m = (fb.get("model") or "").strip()
            if m and m not in models:
                models.append(m)
    return models


def _read_claude_models() -> list[str]:
    """Return claude's configured default model + the tier aliases the
    Claude Code CLI actually accepts. The configured model goes first so it
    becomes the picker default."""
    import json
    settings_path = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
    configured = ""
    try:
        with open(settings_path, encoding="utf-8") as f:
            configured = (json.load(f).get("model") or "").strip()
    except Exception:
        configured = ""
    models: list[str] = []
    if configured:
        models.append(configured)
    for alias in ("default", "opus", "sonnet", "haiku"):
        if alias not in models:
            models.append(alias)
    return models


def _claude_authenticated() -> bool:
    """True when Claude Code has a stored login (OAuth credentials or an
    oauthAccount in ~/.claude.json). Used to avoid showing a misleading
    'please log in' hint to users who are already authenticated."""
    import json
    home = os.path.expanduser("~")
    if os.path.isfile(os.path.join(home, ".claude", ".credentials.json")):
        return True
    try:
        with open(os.path.join(home, ".claude.json"), encoding="utf-8") as f:
            if json.load(f).get("oauthAccount"):
                return True
    except Exception:
        logger.debug("True 失败（旁路，已忽略）", exc_info=True)
    return False


def _read_codex_models() -> list[str]:
    """Return codex's configured default + the models codex actually offers
    (visibility=='list' in its models cache). Configured model goes first."""
    import json
    home = os.path.expanduser("~")
    default = ""
    try:
        with open(os.path.join(home, ".codex", "config.toml"), encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if "=" in s and s.split("=", 1)[0].strip() == "model":
                    default = s.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    except Exception:
        default = ""
    models: list[str] = []
    if default:
        models.append(default)
    try:
        with open(os.path.join(home, ".codex", "models_cache.json"), encoding="utf-8") as f:
            for m in json.load(f).get("models", []):
                if m.get("visibility") == "list":
                    slug = (m.get("slug") or "").strip()
                    if slug and slug not in models:
                        models.append(slug)
    except Exception:
        logger.debug("slug = 失败（旁路，已忽略）", exc_info=True)
    return models


# kiro --list-models hits the network, so cache the result for a few minutes
# to keep the picker snappy. (timestamp, models)
_kiro_models_cache: tuple[float, list[str]] = (0.0, [])


def _read_kiro_models() -> list[str]:
    """Return kiro's actual available models via `kiro-cli chat --list-models`
    (default first). Cached 5 min; empty on failure so static fallback wins."""
    import json
    import time
    global _kiro_models_cache
    ts, cached = _kiro_models_cache
    if cached and (time.time() - ts) < 300:
        return cached
    bin_path = _resolve_bin(_candidates_for(AGENT_DEFS["kiro"]))
    if not bin_path:
        return []
    try:
        out = subprocess.run(
            [bin_path, "chat", "--list-models", "--format", "json"],
            capture_output=True, text=True, timeout=12,
            **no_window_kwargs(),
        )
        data = json.loads(out.stdout)
        models: list[str] = []
        default = (data.get("default_model") or "").strip()
        if default:
            models.append(default)
        for m in data.get("models", []):
            name = (m.get("model_id") or m.get("model_name") or "").strip()
            if name and name not in models:
                models.append(name)
        if models:
            _kiro_models_cache = (time.time(), models)
        return models
    except Exception:
        return []


def _live_models_for(agent_id: str) -> list[str]:
    """Return the agent's actually-configured/available models (live, from
    its own config or CLI). Empty when no live source or the read fails —
    callers fall back to the static list."""
    if agent_id == "hermes":
        return _read_hermes_models()
    if agent_id == "claude":
        return _read_claude_models()
    if agent_id == "codex":
        return _read_codex_models()
    if agent_id == "kiro":
        return _read_kiro_models()
    return []


# Agents whose live source is the COMPLETE, authoritative model list
# (codex models cache, kiro --list-models, claude tier aliases). For these
# we use the live list as-is. Hermes only reports its configured primary +
# fallback, so we append its static "other selectable" models after them.
_LIVE_IS_COMPLETE = {"codex", "kiro", "claude"}


def _merge_models(agent_id: str, live: list[str], static: list[str]) -> list[str]:
    """Combine live + static models per agent semantics."""
    if not live:
        return list(static)
    if agent_id in _LIVE_IS_COMPLETE:
        return list(live)
    merged = list(live)
    for m in static:
        if m not in merged:
            merged.append(m)
    return merged


def list_agents() -> list[dict[str, Any]]:
    """Cheap read for the picker.

    Returns the persisted projection with the agent's actual configured
    models injected at the top — reads each agent's own config so the
    picker shows what it is really using, not a stale static list.
    """
    rows = svc.list_agents_db()
    if not rows:
        return discover_agents()

    result = []
    for row in rows:
        agent_id = row.get("id", "")
        configured = _live_models_for(agent_id)
        if not configured:
            result.append(row)
            continue

        adef = AGENT_DEFS.get(agent_id)
        static = list(adef.static_models) if adef else []
        merged = _merge_models(agent_id, configured, static)
        result.append({**row, "models": merged, "default_model": configured[0]})

    return result


def build_argv(
    agent_id: str,
    *,
    mode: str,
    model: str | None = None,
    prompt: str | None = None,
    resume_id: str | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Construct the argv + env extension for spawning an agent.

    mode = 'cli'  -> interactive PTY launch.
    mode = 'chat' -> one-shot non-interactive call (subprocess.Popen).
    """
    adef = get_agent_def(agent_id)
    # Honor hub_settings.<agent>_bin override (same logic as discover_agents),
    # otherwise systemd's narrow PATH causes the spawn path to disagree with
    # the discovery path — fine at boot, broken on first user message.
    bin_path = _resolve_bin(_candidates_for(adef))
    if not bin_path:
        raise RuntimeError(f"agent binary missing: {agent_id}")
    if mode == "cli":
        argv = [bin_path, *adef.cli_args]
    elif mode == "chat":
        if adef.chat_args is None:
            raise RuntimeError(f"agent {agent_id} has no chat-mode args defined")
        argv = [bin_path]
        for a in adef.chat_args:
            argv.append(a.replace("{prompt}", prompt or ""))
    else:
        raise ValueError(f"bad mode: {mode}")

    if model and adef.id != "hermes":
        # Most agents take --model; hermes uses -m and may already have one
        # in argv (we let the user-side model flow win).
        # In chat mode some agents must skip the model flag entirely.
        if not (mode == "chat" and adef.chat_skip_model):
            argv.extend(["--model", model])
    elif model and adef.id == "hermes":
        if not (mode == "chat" and adef.chat_skip_model):
            argv.extend(["-m", model])

    if resume_id and adef.resume_strategy == "flag" and adef.resume_flag:
        if adef.id == "codex":
            # `codex resume <id>` — subcommand form, not a flag. The
            # previous `--last` hardcode ignored the actual id; we now
            # pass the session id explicitly so users get the rollout
            # they picked from the sidebar.
            argv = [bin_path, "resume", resume_id]
        else:
            argv.extend([adef.resume_flag, resume_id])

    env = dict(adef.env)
    return argv, env
