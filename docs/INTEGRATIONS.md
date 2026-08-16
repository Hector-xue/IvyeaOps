# 外部集成

IvyeaOps 可独立运行。下面这些功能都是**可选**的：只有当你在同一台主机上装了
对应工具时才需要配置。

所有集成路径的配置方式：

- 网页 `系统配置 → 智能体 → 外部集成路径`，**或**
- 在 `server/.env` 里填对应的 `IVYEA_OPS_*` 环境变量作为兜底。

UI 的 **系统状态** 面板会显示哪些集成已就绪。

## 每个集成能开启什么

### Hermes（`hermes_bin`、`hermes_db`、`hermes_node_bin`）

[Hermes Agent](https://github.com/anthropics/hermes) —— 面向 Claude API 请求的
网关，带定时任务、MCP 服务、消息历史。

- `hermes_bin` 让工作台的智能体选择器能把 "hermes" 作为 ASIN 审计、广告审计、
  Listing 分析、知识库对话等的运行器。
- `hermes_db`（`/root/.hermes/state.db`）给 token 用量监控提供逐会话的
  token / 模型记录。
- `hermes_node_bin`（`/root/.hermes/node/bin`）会被加到 spawn 的 PATH 前面，
  让 Hermes 找到它自带的 `node` 和子 CLI。

### Codex（`codex_bin`、`codex_db`、`feishu_codex_db`）

[OpenAI Codex CLI](https://github.com/openai/codex)。智能体选择器会把它与
Hermes / Claude 并列。`codex_db`（以及飞书中继旁车安装的变体 `feishu_codex_db`）
提供 token 用量。

### Claude Code（`claude_bin`、`claude_projects_dir`）

[Claude Code CLI](https://docs.claude.com/en/docs/claude-code)。

- `claude_bin` —— 指向平台二进制，而不是 `claude.exe` 那个 shim。常见位置：
  - `/root/.hermes/node/lib/node_modules/@anthropic-ai/claude-code/node_modules/@anthropic-ai/claude-code-linux-x64/claude`
  - `/usr/local/bin/claude`（用官方安装器装的情况）
- `claude_projects_dir`（`~/.claude/projects`）—— Claude Code 的逐项目 jsonl
  会话日志；token 监控会读它。

### Kiro（`kiro_cli_bin`、`kiro_gateway_db`、`kiro_cli_db`、`kiro_cli_sessions_dir`）

[Kiro CLI](https://kiro.dev/)。可选的另一种智能体运行器。

- `kiro_cli_bin` —— 智能体选择器入口。
- `kiro_gateway_db` —— 本地 Kiro Gateway 的用量统计。
- `kiro_cli_db` + `kiro_cli_sessions_dir` —— CLI 会话的 token 估算（没有精确
  计数器，从 `context_usage_percentage` 估算）。

### Bun（`bun_bin`）

设置 `bun_bin`（`/root/.bun/bin`）会把 Bun 加到子进程 PATH 前面，
供依赖 Bun 运行时的 CLI 使用。

## 新增一个集成

1. 在 `server/app/core/hub_settings.py` 的 `_DEFAULTS` 和 `_ENV_MAP` 加键。
2. 在 `server/app/core/integrations.py` 加 getter（若是 PATH 增补则扩展
   `extra_path_dirs()`）。
3. 在 `client/src/api/settings.ts` 加 TypeScript 类型，并在
   `client/src/pages/workbench/HubSettings.tsx` 加一个 `<Field>`。
4. 在 integrations.py 的 `all_status()` 加一行，并在 HealthPanel 的 `rows[]`
   加对应行，让状态面板能显示它。

这个模式刻意重复——每层各一处——但好处是不用动态分发就能让每个集成都可发现。

## 完全去掉集成噪音

如果你不跑上面任何工具、想要更干净的 UI：

1. 所有路径留空（默认就是空）。智能体选择器只会显示二进制在 `PATH` 上的运行器；
   监控会对每个 token 来源显示「(未配置)」。
2. 可选：把 `HubSettings.tsx` 里对应的 `<Section>` 块删掉以隐藏「外部集成路径」。
   后端仍通过 API 接受这些键，方便以后重新启用。

> 注：**资讯**板块现在是按需用 RSS + 标准 AI 链现场生成日报，已不再依赖 Hermes
> 的定时任务，因此即使不装 Hermes 也能正常用。
