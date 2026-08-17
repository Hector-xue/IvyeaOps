# 配置项参考

> **不必逐项手动配。** 一键安装（`install.sh` / 双击「安装 IvyeaOps.bat」）会自动
> 生成必需的 `server/.env`；登录后的「首启向导」再引导你填一个「全局兜底大模型」
> 就能用全部 AI 功能。**本文是参考手册**——下面大多数项要么可选、要么有合理默认值，
> 只在你要深度定制时才需要看。

IvyeaOps 有两层配置。本文未列出的都是代码常量——没有其他配置文件。

## 第 1 层 · 启动环境变量（`server/.env`）

服务启动时读取一次，是 IvyeaOps 能跑起来的前提。改完文件后需重启服务才生效。

| 键 | 用途 | 说明 |
|---|---|---|
| `IVYEA_OPS_HOST` | 监听地址（默认 `127.0.0.1`） | 不要直接对外暴露，由 nginx 反代。 |
| `IVYEA_OPS_PORT` | 监听端口（默认 `8001`） | 必须与 nginx + systemd 模板一致。 |
| `IVYEA_OPS_DEV` | `1` 时允许 Vite 开发服务器跨域并去掉 `Secure` cookie | 生产环境填 `0`。 |
| `IVYEA_OPS_SECRET` | 会话 cookie 的 itsdangerous 签名密钥 | 必须稳定；重新生成会让所有人退出登录。 |
| `IVYEA_OPS_USER` | 管理员用户名 | 单管理员，通常 `admin`。 |
| `IVYEA_OPS_PASSWORD_HASH` | 管理员密码的 bcrypt 哈希 | 用 `python -m app.core.hashpw` 生成。 |
| `IVYEA_OPS_ALLOWED_ORIGINS` | 允许发起 POST 的来源（逗号分隔） | 必填；CSRF 防护会拒绝其他来源。 |
| `IVYEA_OPS_COOKIE_DOMAIN` | 会话 cookie 的域 | 留空 = 仅当前主机（最安全）；填 `.example.com` 可子域共享。 |
| `IVYEA_OPS_DATA_DIR` | SQLite、上传、hub_settings.json 的存放目录 | 默认 `<repo>/data/`。 |
| `IVYEA_OPS_TERMINAL_AUTOCAPTURE` | `1` 时持续快照活动 tmux 面板 | 默认开。 |
| `IVYEA_OPS_TERMINAL_AUTOCAPTURE_INTERVAL` | 两次快照间隔秒数 | 默认 `300`。 |

`.env.example` 里列出的其他项（`IVYEA_OPS_HERMES_BIN`、`APIMART_KEY`…）都是
**可选**的，仅当对应的 hub_settings.json 条目为空时作为兜底使用。

## 第 2 层 · 运行时设置（`data/hub_settings.json`）

在网页「系统配置」里修改，以 JSON 持久化。读取时若某个键为空，进程内的辅助逻辑
会回退到环境变量（再不行用内置默认值）。

UI 里的分区：

- **数据源** —— Sorftime / SIF / 卖家精灵 的 Key（带连通性测试）
- **Hermes 兼容模型** —— 仅旧 Hermes 链路使用的主模型 + Fallback 模型
- **应用模型** —— 全局兜底大模型（兼任务台纯聊兜底）+ 图片生成服务
- **智能体** —— IvyeaAgent 状态、可选外部 CLI 路径、AI 提供商顺序、自动修复
- **飞书 / Lark 通知** —— webhook 或自建应用（app_id + app_secret + chat_id）
- **高级 / 运维** —— CPU 报警阈值、内嵌服务地址、资讯 RSS 源等
- **账号安全** —— 修改密码（把新哈希写入 hub_settings.json 的 `password_hash`，
  优先级高于 `IVYEA_OPS_PASSWORD_HASH` 环境变量）

### 推荐 AI 配置阶梯（开源用户从这里开始）

不需要 Claude / Codex 账号。按预算三档，全部在「系统配置 → AI 服务」里填：

| 档位 | 配置 | 能力 |
|---|---|---|
| 零 Key | 什么都不填 | 各板块用内置规则兜底；Listing 套图走内置精品叙事库策划 + 人工复核，流程完整可跑 |
| **最低配（推荐）** | 一个 [DeepSeek](https://platform.deepseek.com) key（`deepseek_api_key`，也可直接设为 IvyeaAgent 主脑） | AI 分析 / 文案 / 套图策划 / 各板块报告全部满血 |
| 视觉增强 | 再加一个[硅基流动](https://siliconflow.cn) key：在独立的「**视觉复核模型**」区块选「硅基流动」，模型填 `Qwen/Qwen3-VL-30B-A3B-Instruct`（免费档即可用，无需充值） | Listing 成图质检 + 按质检意见自动重画闭环、图片视觉分析全部启用 |

说明：
- 文本任务的调用顺序是 **IvyeaAgent（主脑可配 DeepSeek）→ Hermes → DeepSeek → 全局兜底 → Codex → Claude**，配到哪层用到哪层；
- 硅基流动 / 阿里云百炼 / 智谱在 Provider 下拉里有预设，任何 OpenAI 兼容端点也可用「自定义」接入。

#### 视觉能力的三档降级

视觉不是"配了才有、没配就没有"。IvyeaAgent 内部按三档自动降级，
「系统配置 → 系统状态 → AI · 视觉识别」会直接显示当前在哪一档：

| 档 | 触发条件 | Listing 能做什么 |
|---|---|---|
| **T1 主脑直读** | IvyeaAgent 主脑自带视觉（`gpt-4o` / `claude` / `gemini` / `*-VL` 等） | 全部 |
| **T2 视觉旁路** | 主脑不支持图片，但配了「视觉复核模型」 | 全部 |
| **T3 本地量化** | 两者都没配 | 合规/比例/主体占比/配色/图上文字**照常分析**；版式逆向与审美判断跳过并明确告知 |

- **T3 不是"坏了"**：图片在本机被 CV + OCR 量化成读数（尺寸、白底合规、主体占比与
  偏移、主色板、OCR 文字及其版面分布、套图查重），再交给文本模型判断。图片不出网。
  主图促销文字、白底不合规、主体过小这类最常见的问题，T3 都能查出来。
- **T3 查不了的**：这是什么产品、竞品套图版式逆向、审美好坏。这些会在方案里
  标注「本项已跳过」并说明原因，不会给你一个编出来的假结果。
- 想从 T3 升到 T2：在「**视觉复核模型**」区块配一个视觉模型即可（硅基流动免费档
  的 `Qwen/Qwen3-VL-30B-A3B-Instruct` 就够）。**这个配置会自动下推给 IvyeaAgent**，
  网页和命令行共用同一个视觉模型，不用配两遍。
- 若 IvyeaAgent 服务未连接，视觉链退到 **OpenAI → 视觉复核模型** 两档直连；
  都没有时成图质检降级为人工复核（勾选「已核对」即可交付），不会卡死流程。
- 文本和视觉可以用不同平台，互不影响。

### IvyeaAgent 知识库

新知识库由 IvyeaAgent 管理，默认文件夹是 `~/.ivyea/knowledge`。右下角 IvyeaAgent 面板支持上传文档、预览导入草稿、确认入库、搜索知识卡和查看最近上传。安装脚本会创建目录并启动本地服务；如果服务暂时没起来，IvyeaOps 的状态检查会自动重试拉起。

### 语义检索（embedding）

知识库的语义检索归 IvyeaAgent 自己管，IvyeaOps 这边**不需要配置任何 embedding**。
以前这里有一组「知识库语义检索」的服务商/模型/Key 设置，它写的是 GBrain 的
`~/.gbrain/config.json`，对 IvyeaAgent 毫无作用——配了也不生效，纯误导，已移除。

要查看或调整 agent 的检索后端，用它自己的命令：

```bash
ivyea retrieval embeddings          # 看当前后端与是否真的生效
ivyea retrieval embeddings --probe  # 实际编码一次做验证
```

### 取值优先级

对任意运行时取值 `X`：

1. `hub_settings.json["X"]` 非空 → 用它
2. 否则用 `_ENV_MAP` 里列出的环境变量（`IVYEA_OPS_X` 或其别名）
3. 再否则用 `_DEFAULTS` 里的内置默认值

也就是说：在 `.env` 里设好合理默认值让全新安装就能跑，再到 UI 里细调。

## 跨进程：cpu_alert cron

`scripts/cpu_alert.py` 通过 `/etc/cron.d/` 在进程外运行。它 import
`app.core.hub_settings` 读取与在线服务相同的值，所以在 UI 里改阈值/通道后，
下一分钟即生效，无需重启任何东西。

`cpu_alert.py` 的兜底链：

1. hub_settings.json
2. `IVYEA_OPS_ALERT_*` 环境变量
3. `/root/.hermes/.env`（Hermes 同机安装时的便利——`FEISHU_APP_ID` 等）。
   若 Hermes 装在别处或想完全禁用此兜底，用 `HERMES_ENV=` 环境变量覆盖。

## 部署期模板

`deploy/install.conf`（已 gitignore）喂给 `scripts/render-deploy.sh`，后者把
`${VAR}` 占位替换进：

- `deploy/nginx/ivyea-ops.conf.template`
- `deploy/systemd/ivyea-ops.service.template`
- `deploy/cron.d/ivyea-ops-cpu-alert.template`

渲染产物落在 `deploy/dist/`。脚本会打印把它们安装到 `/etc/` 的 sudo 命令。

部署配置这一层刻意与 `.env`、hub_settings 分开——后两者由**应用**读取，
而 `install.conf` 由**安装器**读取。

## 速查：改 X 去哪改？

| 想改… | 在哪改 | 要重启吗？ |
|---|---|---|
| 管理员密码 | UI → 账号安全（或重新哈希 + `.env`） | 否 |
| Apimart / Sorftime / OpenAI 等 key | UI → 数据源 / 应用模型 | 否 |
| CPU 报警阈值 | UI → 高级/运维 | 否（下一次 cron） |
| 飞书报警通道 | UI → 飞书 / Lark 通知 | 否（下一次 cron） |
| 内嵌 iframe 地址 | UI → 高级/运维 | 仅刷新页面 |
| 外部工具路径 | UI → 智能体（外部集成路径） | 否 |
| 监听端口 | `server/.env` `IVYEA_OPS_PORT` + render-deploy.sh | 是（systemd + nginx reload） |
| 公网域名 | `deploy/install.conf` SERVER_NAME + render-deploy.sh | nginx reload |
| 会话密钥 | `server/.env` `IVYEA_OPS_SECRET` | 是（强制重新登录） |
