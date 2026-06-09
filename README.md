# IvyeaOps · 自托管亚马逊运营工作台

**IvyeaOps** 是一套**开源、自托管**的亚马逊运营工作台：一台服务器、一次登录，把
Listing 制作、AI 生图、市场调研、深度分析、广告优化（含领星 ERP 接入）、AI 智能体、
GBrain 知识库、Skill 工坊、服务器运维等运营全流程，统一收进浏览器里。

> 数据与密钥都留在你自己的服务器，不绑定任何第三方云。**AGPL-3.0** 开源，可自由使用、修改、自托管。

- **技术栈**：后端 FastAPI（Python）+ 前端 React / Vite（TypeScript），后端直接托管前端构建产物。
- **预构建发行包**：`IvyeaOps.zip` 已含前端 `client/dist` 与 Windows Python 3.12 后端依赖 wheels；`IvyeaOps-Windows-x64.zip` 额外内置后端 `IvyeaOpsServer.exe`，Windows 可免 Python / Node。
- **可选组件**：Hermes Agent 与 GBrain 不强塞主包，可在安装脚本、首启向导或系统配置里一键安装 / 修复。
- **支持平台**：Linux / macOS（完整）· Windows（除 PTY 终端外完整；Windows x64 有免 Python 包）。
- **默认端口**：`8001`（`http://127.0.0.1:8001`）。
- **仓库**：<https://github.com/Hector-xue/IvyeaOps>

---

## 目录

- [核心特性](#核心特性)
- [功能板块总览](#功能板块总览)
- [快速开始](#快速开始)
- [更新升级](#更新升级不影响你的数据)
- [配置模型（两层）](#配置模型两层)
- [AI 智能体](#ai-智能体)
- [领星 ERP 接入](#领星-erp-接入)
- [生产环境部署（Linux）](#生产环境部署linux)
- [项目结构](#项目结构)
- [安全须知](#安全须知)
- [致谢](#致谢)
- [许可证](#许可证)

---

## 核心特性

- **本地部署**：跑在自己的服务器，不依赖第三方 SaaS，业务数据不出私域。
- **数据安全**：店铺数据、API 密钥统一存放在后端 `data/`（已 gitignore），不入代码库、不暴露给前端或智能体。
- **开箱即用**：Release 包已预构建前端；Windows 普通包预置后端 wheels，Windows x64 包内置后端 exe。
- **可二次开发**：AGPL-3.0 开源，前后端按模块拆分，路由 / 服务一一对应，新增板块成本低。
- **一次登录，全部模块**：单点登录后侧边栏直达所有板块，统一主题（内置 10 套配色）。
- **智能体驱动**：浏览器内直接跑 Hermes / Claude / Codex 智能体，支持流式输出、会话续聊、工具可视化。

---

## 功能板块总览

> **每个板块怎么用、需要哪些配置，详见使用手册 [`docs/USAGE.md`](docs/USAGE.md)。**

侧边栏按四组组织，与下表一一对应：

### 工具
| 板块 | 说明 |
|---|---|
| **首页** | 工作台概览与常用入口 |
| **市场调研** | 关键词 / 竞品 / 类目洞察，结合数据源 + AI 综合分析 |
| **打法推荐** | 按品类给出运营策略建议 |
| **Listing 工作台** | ASIN → 标题 · 五点 · 描述；AI 文案与改写 |
| **分析工具（深度分析）** | 竞品速查 · 关键词竞争 · Listing 重写 · 评论聚类 · 流量诊断 |
| **领星 ERP** | 经官方 OpenAPI 接入领星：数据浏览 / 大盘 / 广告优化引擎 / 自动化建议 / 受控写操作 / 审计（见下文专章） |
| **Skill 中心 / 想法工坊** | 一句话生成 Skill（多阶段严谨生成 + 自检修复）、Tool Spec 可视化、执行历史、审核制一键修复 |

### AI & 系统
| 板块 | 说明 |
|---|---|
| **AI 问答** | 浏览器内通用 AI 问答助手 |
| **AI 生图** | Prompt → 批量主图 / 场景图（图像工作流） |
| **智能体会话** | 原生移植 claudecodeui 体验：Claude 走 stream-json 结构化输出、会话 resume、工具调用可视化、终端 |
| **GBrain 知识库** | 私有 Markdown 笔记 + 语义检索（embedding） |
| **服务器终端** | 浏览器内 PTY 多终端会话（仅 Linux） |
| **服务器监控** | CPU / 进程 / 日志等资源一屏掌握，含告警 |

### 小工具
| 板块 | 说明 |
|---|---|
| **头程比价** | 头程运费比价 |

### 管理
| 板块 | 说明 |
|---|---|
| **用户管理** | 多用户与权限 |
| **系统配置** | 集中式运行时配置（密钥 / 集成路径 / 阈值），首启向导引导 |
| **资讯** | 每日 AI 行业资讯摘要 |

> 还内置「审核制 AI 自动修复」（功能报错时用 hermes + git worktree 隔离生成修复、人工审核后应用，默认关闭）等运维能力。

---

## 快速开始

### Linux / macOS

**推荐（尤其小内存服务器）：下载预构建包，免装 Node、免构建、免 swap。** 前端打包峰值要
1.5–2 GB 内存，小机器常被 OOM 杀死、报错或卡死；预构建包已自带 `client/dist`，安装脚本检测到后会
**整段跳过 Node 安装与前端构建**，只配置 Python 后端：

```bash
# 1. 下载预构建包（IvyeaOps.zip 永远指向最新发行版）并解压
curl -L https://github.com/Hector-xue/IvyeaOps/releases/latest/download/IvyeaOps.zip -o IvyeaOps.zip
unzip IvyeaOps.zip && cd IvyeaOps
# 2. 一键安装（检测到自带前端 → 跳过 Node 与构建，只配后端）
bash scripts/install.sh
# 3. 启动
bash scripts/start.sh
```

> Hermes / GBrain / Ollama 是可选增强组件：安装脚本会询问是否顺便安装；也可稍后在首启向导或「系统配置 → 系统状态」里点「安装/修复」。

<details><summary>开发者：从源码构建（需 Node 18+ 与 ≥2G 内存）</summary>

```bash
git clone https://github.com/Hector-xue/IvyeaOps.git
cd IvyeaOps
bash scripts/install.sh   # 无 dist 时会自动装 Node、构建前端（内存不足会临时加 2G swap）
bash scripts/start.sh
```
</details>

浏览器打开 **http://127.0.0.1:8001**，首启向导会引导你完成智能体检测与 API 密钥设置。

> **远程服务器怎么访问？** 默认只监听 `127.0.0.1`（仅本机，安全）。无头 Linux 服务器上没有浏览器，三选一：
> - **最简单（临时试用）**：在你**本机**开 SSH 隧道 `ssh -L 8001:127.0.0.1:8001 用户@服务器`，然后本机浏览器开 `http://127.0.0.1:8001`。无需反代。
> - **正式 / 团队**：上 nginx 反代 + 域名 + HTTPS —— 见 [`docs/INSTALL.md`](docs/INSTALL.md)（内置 nginx / systemd / certbot 模板）。
> - **图省事（不推荐）**：`.env` 里设 `IVYEA_OPS_HOST=0.0.0.0` 并放行防火墙端口，直接 `http://服务器IP:8001`——等于裸暴露公网，务必配合强密码/防火墙。

> **国内网络加速**
> - **克隆慢**：推荐 **gh 代理**（零登录、Windows 也不弹凭据框）：
> `git clone https://gh-proxy.com/https://github.com/Hector-xue/IvyeaOps.git`
> 或 **Gitee 镜像**（最快，但 Windows 的 Git Credential Manager 可能弹 Gitee 登录——仓库是公开的、其实不用账号，可加 `-c credential.interactive=never` 强制匿名）：
> `git -c credential.interactive=never clone https://gitee.com/hectorxue/IvyeaOps.git`
> - **装依赖慢**：`install.sh` / `install.ps1` 会**自动检测**是否在大陆网络，自动把 pip、npm 切到清华 + 淘宝镜像（无需手动；可用 `IVYEA_CN=1` 强制开、`IVYEA_CN=0` 关）。
> - **hermes / GBrain 慢**：这两个是境外安装器，可选、可稍后单独重试；镜像环境变量会顺带加速它们内部的 pip/npm 步骤。

> macOS 与 Linux 命令完全一致（同为 Unix）：原生脚本和 `docker compose up -d` 都可用，
> PTY 终端也正常工作。需先有 Python 3.9+ 与 Node 18+（可用 Homebrew 安装）。

### Windows（双击即装、双击即用）

> **推荐给普通用户：下载 `IvyeaOps-Windows-x64.zip`，无需安装 Python / Node。** 到
> [Releases](https://github.com/Hector-xue/IvyeaOps/releases) 下载最新的
> `IvyeaOps-Windows-x64.zip` → 解压 → 双击 **`IvyeaOpsServer.exe`**。
> EXE 会自动生成配置、把登录信息保存到桌面和 `data\IvyeaOps 登录信息.txt`、创建桌面快捷方式并打开浏览器；以后双击桌面 `IvyeaOps` 或 `IvyeaOpsServer.exe` 即可启动。

如果你想保留 Python venv 方式，也可以下载普通 `IvyeaOps.zip`：它已含编译好的前端和 Windows Python 3.12 后端依赖 wheels，安装时优先离线装依赖，失败再回退在线 pip。

解压后：

1. **免 Python 包**：双击 `IvyeaOpsServer.exe`。
2. **普通包 / 源码包**：双击「安装 IvyeaOps.bat」—— 自动检测 Python、装后端依赖、生成配置、创建桌面快捷方式。
3. 以后**双击桌面「IvyeaOps」**—— 浏览器自动打开 **http://127.0.0.1:8001**。
4. x64 包如需更新，可在 IvyeaOps 侧边栏左下角点击 **更新**；也可双击 **「更新 IvyeaOps Windows x64.bat」**。如需停止，双击 **「停止 IvyeaOps.bat」**。

> Hermes / GBrain / Ollama 同样是可选组件：安装时可选，也可在首启向导或「系统配置 → 系统状态」里点「安装/修复」。

> **团队共享（同一局域网，其他电脑/手机零安装）**：双击 **「启动 IvyeaOps (局域网共享).bat」**。
> 它会自动探测本机局域网 IP、把服务绑到 `0.0.0.0`、将该 IP 加入 CSRF 白名单（否则跨机登录会
> 403）、放行防火墙 8001 端口，并在窗口里打印让同事访问的网址——对方浏览器打开即可，**无需安装任何东西**。
> IP 变了重跑一次即可；只在你信任的内网用，务必配强密码。详见
> [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) §11。

> 喜欢命令行也可以：`powershell -ExecutionPolicy Bypass -File scripts\install.ps1`。
>
> **注意**：Windows 不支持 PTY 多终端会话，其余功能正常。
>
> 从零开始的 Windows 图文步骤（含环境安装与常见问题排查）见
> [`docs/windows-install.md`](docs/windows-install.md)，适合直接转发给非开发的同事。

### 环境要求

| | Linux / macOS | Windows 普通包 | Windows x64 免 Python 包 |
|---|---|---|---|
| Python | 3.9+ | 3.9+（推荐 3.12，可用预置 wheels） | 不需要 |
| Node.js / npm | 预构建包不需要；源码构建需要 18+ | 预构建包不需要；源码构建需要 18+ | 不需要 |
| 后端依赖 | pip 在线安装 | 优先离线 wheels，失败回退在线 pip | 已打进 `IvyeaOpsServer.exe` |

> **装不上 / 起不来 / 打开是 404 / AI 报错？** 先看 [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) —— 国内克隆、依赖慢、内存不足、Windows 编码、端口占用、前端没构建、远程访问、AI 失败等高频问题都在里面，多数能自助解决。

> **实在装不动？交给 AI 智能体装。** 这本就是个给智能体用的工作台——把本仓库地址丢给 Claude Code / Codex / hermes 等编码智能体，让它读 `README` 与 `scripts/`、按你的系统自己完成克隆、装依赖、构建、配置。比对着报错一行行手动试省心得多。

---

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

**Windows x64 免 Python 包** —— 直接双击：

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

> 完整配置项参考 [`docs/CONFIG.md`](docs/CONFIG.md)。

---

## AI 智能体

IvyeaOps 支持在浏览器里直接调用本地智能体 CLI；这些都是**可选组件**，没装也可以用「全局兜底大模型」跑大部分 AI 功能。首启向导和「系统配置 → 系统状态」都提供安装 / 修复入口：

| 智能体 / 组件 | 说明 |
|---|---|
| **Hermes Agent** | 推荐主力智能体，支持 MCP / 工具调用 / Skill；可一键安装或稍后修复 |
| **GBrain** | 本地知识库 CLI，初始化 `~/brain`，用于私有笔记与检索 |
| **Claude Code** | 「智能体会话」原生移植 claudecodeui，走 stream-json 结构化输出，支持会话 resume、工具调用可视化 |
| **Codex** *(可选)* | OpenAI Codex CLI；如不使用可忽略 |

安装后 IvyeaOps 会从 `$PATH` 自动探测，多数情况下无需手动配置路径。

---

## 领星 ERP 接入

「领星 ERP」板块经领星**官方 OpenAPI**把店铺数据与广告操作接入工作台，覆盖三类需求，
并以**自建网关作唯一咽喉**做安全隔离：

- **浏览分析**：数据浏览 / 大盘 / 多店铺对比，只读拉取真实经营与广告数据。
- **优化引擎**：确定性**规则引擎**产出否词 / 出价 / 加词等建议（数据不足不动手），LLM 仅复核。
- **自动化建议**：定时分析产出优化建议（仅建议，不直接写入）。
- **受控写操作**：默认**双开关全关**；开启后写操作强制**三重复核 + 确定性护栏 + 人工确认**，
  执行前抓回滚快照、失败自动熔断、全程审计。

> 详细使用说明见 [`docs/lingxing-erp-guide.md`](docs/lingxing-erp-guide.md)，或板块内「帮助」tab。
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
完整指南见 [`docs/INSTALL.md`](docs/INSTALL.md)，可选集成见 [`docs/INTEGRATIONS.md`](docs/INTEGRATIONS.md)。

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
│       ├── agents/            「智能体会话」原生移植子应用
│       ├── components/        通用组件
│       ├── layouts/           侧边栏 / 主框架
│       └── api/               类型化 API 客户端
├── data/                  运行时数据（SQLite、hub_settings.json）— gitignore
├── deploy/                nginx / systemd / cron / docker 模板
├── scripts/
│   ├── install.sh         Linux / macOS 一键安装
│   ├── install.ps1        Windows 普通包一键安装
│   ├── install-components.ps1  Windows 可选安装 / 修复 Hermes + GBrain/Ollama/Codex/Claude
│   ├── update-exe.ps1     Windows x64 免 Python 包一键更新
│   ├── windows-action-gui.ps1  Windows x64 更新 / 停止图形窗口
│   ├── start.sh           Linux / macOS 启动
│   └── render-deploy.sh   渲染生产部署配置
└── docs/
    ├── CONFIG.md          完整配置参考
    ├── INSTALL.md         生产部署指南
    ├── INTEGRATIONS.md    可选集成
    ├── TROUBLESHOOTING.md 常见问题排查
    └── lingxing-erp-guide.md  领星 ERP 使用文档
```

---

## 安全须知

- `server/.env`、`data/`（含 `hub_settings.json`、领星凭证、SQLite）、`saved-images/` 均已 gitignore，**切勿**提交。
- 迁移 / 改前缀时务必原样保留 `IVYEA_OPS_SECRET` 与 `IVYEA_OPS_PASSWORD_HASH`，否则会登录 401。
- 领星等真实店铺写操作受双开关 + 三重复核 + 人工确认保护，请勿绕过。

---

## 致谢

IvyeaOps 站在不少优秀开源项目之上，特此致谢：

- **[claudecodeui](https://github.com/siteboon/claudecodeui)**（**AGPL-3.0**）—— 「智能体会话」板块的浏览器端交互体验移植自该项目。正因如此，整个 IvyeaOps 依 AGPL-3.0 的 copyleft 要求以 **AGPL-3.0** 发布。
- **Hermes Agent**（NousResearch）· **[GBrain](https://github.com/garrytan/gbrain)**（MIT）—— 可选集成的本地智能体与本地知识库（作为独立程序由 IvyeaOps 经子进程调用，并未内置其源码，故不影响本项目的许可证）。
- 基础设施：**FastAPI** · **React** · **Vite** · **xterm.js** 等众多开源库。

上述各上游项目的版权与许可证均归其原作者所有；如有疏漏，欢迎提 Issue 指正。

---

## 许可证

**AGPL-3.0**（GNU Affero General Public License v3.0）。完整条款见 [`LICENSE`](LICENSE)。

本项目的「智能体会话」板块移植自 AGPL-3.0 的 [claudecodeui](https://github.com/siteboon/claudecodeui)，按其 copyleft 要求，整个 IvyeaOps 以 AGPL-3.0 发布：你可自由使用、修改、自托管；若你**修改后作为网络服务提供给他人使用**，须向这些用户提供完整的对应源码（AGPL 第 13 条「网络条款」）。
