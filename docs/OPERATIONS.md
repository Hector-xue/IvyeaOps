# 运维手册：备份、升级、配置与部署

> 从 README 拆出来的进阶内容。安装入门见 [README](../README.md#快速开始)，排障见 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)。

## 备份与迁移

设置页可以一键生成备份包（也可以 `POST /api/admin/backup`）。429MB 的 `data/` 大约压成 30MB —— 数据库走 SQLite 在线快照（不停服，且不会丢 WAL 里还没落盘的事务），图片、日志这类可再生的内容默认不收。

> ⚠️ **换机器请务必用「带口令」的备份包。**
>
> 从 v1.2 起，配置里的 API 密钥在磁盘上是加密的，解密要用 `data/.master.key`。
> 这是个**隐藏文件**，手动拷 `data/` 目录时极容易漏掉 —— 漏了之后所有密钥都得重填。
>
> 备份时填一个口令，主密钥就会被加密后一并打进包里，换机器能完整还原。
> 不填口令也能备份，只是恢复后需要重新填写各处的 API 密钥。

恢复默认只做**干跑**，先告诉你会覆盖哪些文件、包完不完整、口令对不对；确认无误后再带 `confirm=true` 真正执行。

每天凌晨 3 点会自动备份一次（本地保留 7 份）。自动备份不带口令 —— 它保的是"数据还在"，换机器完整还原仍需手动做一次带口令的备份。

## 更新升级（不影响你的数据）

代码与数据是分离的：你的配置和数据**全部被 `.gitignore` 保护、不在版本控制里**，更新时不会被动到——
- `server/.env`（密钥/密码/API Key）
- `data/`（所有 `*.sqlite3` 数据库、`hub_settings.json` 设置、上传文件）
- `~/.hermes/skill-studio/`（Skill、快照）

**Linux / macOS** —— 一条命令：
```bash
bash scripts/update.sh
```
> 等价于 `git pull` + 刷新依赖 + 重建前端；`.env`/`data/` 原样保留。完成后按提示重启服务
> （systemd：`sudo systemctl restart ivyea-ops`；脚本启动：Ctrl+C 后 `bash scripts/start.sh`）。

**Windows x64 免 Python 包** —— 优先在网页侧边栏更新：

打开 IvyeaOps 后，侧边栏左下角会显示当前版本。检测到新版本时，版本号旁会出现红点，点击旁边的 **更新** 即可自动下载最新版、保留数据并重启。

如果网页打不开，也可以直接双击：

```text
更新 IvyeaOps Windows x64.bat
```

它会自动停止后台服务、下载最新版 `IvyeaOps-Windows-x64.zip`、覆盖程序文件、保留
`data\` / `logs\` / `server\.env`，最后重新启动。用户不需要手动备份或搬目录。

**Windows 源码 / 普通包（git clone 安装）** —— 在仓库目录里：
```powershell
git pull
```
然后重新双击「安装 IvyeaOps.bat」（可选安装项一路回车/选 N 即可；`.env` 已存在会自动跳过、不覆盖），完成后重新双击「启动 IvyeaOps.bat」。

---

## 配置模型（两层）

IvyeaOps 采用两层配置：

**第一层 · 启动配置（`server/.env`）**：仅启动时读取一次。
必填：`IVYEA_OPS_SECRET`、`IVYEA_OPS_PASSWORD_HASH`、`IVYEA_OPS_ALLOWED_ORIGINS`。
由 `install.sh` / `install.ps1` 自动生成。

**第二层 · 运行时配置（`data/hub_settings.json`）**：在网页「系统配置」里编辑。
存放各类 API 密钥、集成路径、告警阈值等。留空时自动回退到对应的 `IVYEA_OPS_*` 环境变量，
再回退到内置默认值。该文件已 gitignore，不入代码库。

> 完整配置项参考 [`docs/CONFIG.md`](CONFIG.md)。

---

## AI 智能体

IvyeaOps 默认内置 IvyeaAgent：右下角常驻图标提供会话、知识库上传、搜索和本地检索。外部 CLI 是**可选增强项**，没装也可以用「全局兜底大模型」跑大部分 AI 功能。首启向导和「系统配置 → 系统状态」都提供安装 / 修复入口：

| 智能体 / 组件 | 说明 |
|---|---|
| **IvyeaAgent** | 默认内置主链，提供 Agent、知识库、上传文档、本地检索和 IvyeaOps 桥接 API |
| **Hermes Agent** | 旧兼容 / 可选增强，支持 MCP / 工具调用 / Skill |
| **Ollama** | 可选本地模型运行环境；用于本地模型工作流 |
| **Claude Code** | 「外部智能体」原生移植 claudecodeui，走 stream-json 结构化输出，支持会话 resume、工具调用可视化 |
| **Codex** *(可选)* | OpenAI Codex CLI；如不使用可忽略 |

安装后 IvyeaOps 会从 `$PATH` 自动探测，多数情况下无需手动配置路径。

---

## 领星 ERP 接入

「领星 ERP」板块经领星**官方 OpenAPI**把店铺数据与广告操作接入工作台，覆盖四类需求，
并以**自建网关作唯一咽喉**做安全隔离：

- **浏览分析**：数据浏览 / 大盘 / 多店铺对比，只读拉取真实经营与广告数据。
- **优化引擎**：确定性**规则引擎**产出否词 / 出价 / 加词等建议（数据不足不动手），LLM 仅复核。
- **自动化建议**：定时分析产出优化建议（仅建议，不直接写入）。
- **受控写操作**：默认**双开关全关**；开启后写操作强制**三重复核 + 确定性护栏 + 人工确认**，
  执行前抓回滚快照、失败自动熔断、全程审计。

> 详细使用说明见 [`docs/lingxing-erp-guide.md`](lingxing-erp-guide.md)，或板块内「帮助」tab。
> 凭证（AppID / AppSecret 等）写入后端 `data/hub_settings.json`，不入代码库。

---

## 生产环境部署（Linux）

生产环境推荐 nginx 反向代理 + Let's Encrypt + systemd：

```bash
cp deploy/install.conf.example deploy/install.conf
$EDITOR deploy/install.conf   # 设置 SERVER_NAME、INSTALL_DIR 等
bash scripts/render-deploy.sh
# 按打印出的 sudo cp 指引落地 nginx / systemd 配置
```

服务以 systemd 单元 `ivyea-ops` 运行（监听 :8001，托管 `client/dist`）。
完整指南见 [`docs/INSTALL.md`](INSTALL.md)，可选集成见 [`docs/INTEGRATIONS.md`](INTEGRATIONS.md)。

---

## 项目结构

```
IvyeaOps/
├── server/                FastAPI 后端（Python）
│   ├── app/
│   │   ├── core/          配置、设置、安全、集成
│   │   ├── routers/       每个功能一个路由（lingxing / brain / market / listing / agent_hub …）
│   │   ├── services/      各功能的业务逻辑
│   │   └── agents/        智能体（providers / projects 等）
│   ├── .env.example
│   └── requirements.txt
├── client/                React + Vite 前端（TypeScript）
│   └── src/
│       ├── pages/workbench/   各工作台板块
│       ├── agents/            「外部智能体」原生移植子应用
│       ├── components/        通用组件
│       ├── layouts/           侧边栏 / 主框架
│       └── api/               类型化 API 客户端
├── data/                  运行时数据（SQLite、hub_settings.json）— gitignore
├── deploy/                nginx / systemd / cron / docker 模板
├── scripts/
│   ├── install.sh         Linux / macOS 一键安装
│   ├── install.ps1        Windows 普通包一键安装
│   ├── install-components.ps1  Windows 安装 / 修复 IvyeaAgent 与可选外部组件
│   ├── update-exe.ps1     Windows x64 免 Python 包一键更新
│   ├── windows-action-gui.ps1  Windows x64 更新 / 停止图形窗口
│   ├── start.sh           Linux / macOS 启动
│   └── render-deploy.sh   渲染生产部署配置
└── docs/
    ├── CONFIG.md          完整配置参考
    ├── INSTALL.md         生产部署指南
    ├── INTEGRATIONS.md    可选集成
    ├── OPERATIONS.md      运维手册（备份 / 升级 / 配置 / 部署）
    ├── TROUBLESHOOTING.md 常见问题排查
    └── lingxing-erp-guide.md  领星 ERP 使用文档
```
