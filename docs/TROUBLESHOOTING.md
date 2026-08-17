# 常见问题排查（Troubleshooting）

按安装到使用的顺序排列。先用症状对照表定位，再看对应小节。
> 大多数问题安装脚本已自动处理（国内镜像、内存不足加 swap、端口探测等）；本表用于仍然卡住时自助排查。

| 症状 | 跳到 |
|---|---|
| `git clone` 很慢 / 弹 Gitee 登录框 / `git pull` 报 not a git repository | [1. 克隆与网络](#1-克隆与网络国内) |
| 装依赖很慢 | [2. 依赖与镜像](#2-依赖与镜像) |
| 提示 Python 版本不够 | [3. 运行环境](#3-运行环境) |
| 打印 `Building frontend...` 后没下文就退出 | [4. 前端构建失败（多为内存不足）](#4-前端构建失败多为内存不足) |
| Windows 双击安装报一堆 `Unexpected token` / 中文乱码 | [5. Windows 脚本乱码](#5-windows-脚本乱码) |
| Windows 启动后黑窗一闪而过 | [6. 启动后立刻退出](#6-启动后立刻退出) |
| `WinError 10048` / `address already in use` | [7. 端口被占用](#7-端口被占用) |
| 打开页面显示 `{"detail":"Not Found"}` | [8. 根路径 404前端没构建](#8-根路径-404前端没构建) |
| 远程服务器上 `127.0.0.1:8001` 打不开 | [9. 远程无头服务器怎么访问](#9-远程无头服务器怎么访问) |
| Listing 生成提示词「Network Error」/ AI 失败 | [10. AI 生成失败](#10-ai-生成失败) |

---

## 1. 克隆与网络（国内）

**`git clone github.com` 很慢或失败**
推荐 gh 代理（零登录，Windows 也不弹凭据框）：
```bash
git clone https://gh-proxy.com/https://github.com/Hector-xue/IvyeaOps.git
```

**Gitee 镜像弹「Git Credential Manager 登录」框**
仓库是公开的、本不需要账号，这是 Windows 凭据管理器 + Gitee 匿名限制所致。强制匿名即可：
```bash
git -c credential.interactive=never clone https://gitee.com/hectorxue/IvyeaOps.git
```
弹出来的框直接关掉也行。

**`git pull` 报 `not a git repository`**
你是下载的 ZIP（解压成 `IvyeaOps-main/`），不带 `.git`，所以用不了 git 命令。要么重新下载 ZIP 覆盖，要么改用上面的 `git clone`（以后 `git pull` 一条命令更新）。

---

## 2. 依赖与镜像

`install.sh` / `install.ps1` 会**自动检测大陆网络**（ping 不通 google 即判定），自动把 pip 切清华、npm 切淘宝、uv（Hermes 用）也切清华镜像。

- 想强制开启：`IVYEA_CN=1 bash scripts/install.sh`
- 想关闭（国际网络误判时）：`IVYEA_CN=0 bash scripts/install.sh`
- **Hermes 慢**：这俩是境外官方安装器（下载 uv/Node 等），可选、可稍后单独重试；镜像只能加速它们内部的 Python 依赖，二进制下载绕不开。不想等就先跳过，配个「全局兜底大模型」先用起来。

---

## 3. 运行环境

- **Python ≥ 3.9**（RHEL9/CentOS9/Rocky/Debian11/Ubuntu20.04 默认满足；脚本会检测）。
- **Node ≥ 18**。
- Windows 上若缺，安装器会用 winget 自动装；缺 winget 时按提示手动装 Python/Node 后重开终端重试。

---

## 4. 前端构建失败（多为内存不足）

**现象**：脚本打印 `Building frontend...` 后**没有任何报错就退回命令行**，没有 `Frontend built`。

**原因**：前端生产包较大，`vite` 构建峰值要 1.5–2G 内存。**小内存云服务器会被系统 OOM 静默杀掉**（exit 137，无干净报错）。

**解决**：
- 最新的 `install.sh` 会在内存 < 1.9G 且无 swap 且为 root 时**自动临时创建 2G swap**，直接重跑即可：
  ```bash
  git pull && bash scripts/install.sh
  ```
- 确认是不是 OOM：`free -m`（看内存）、`dmesg | grep -i oom`（有 `Killed process ... node` 即实锤）。
- 也可手动加 swap 后重试，或换 ≥2G 内存的机器。
- 单独构建前端：
  ```bash
  cd client && npm install && npm run build
  ```

---

## 5. Windows 脚本乱码

**现象**：双击安装报一堆 PowerShell `Unexpected token`，或 `'锘緻echo' 不是内部或外部命令`，中文显示成 `鍚姩涓` 之类乱码。

**原因**：脚本编码不对。Windows PowerShell 5.1 读**无 BOM 的 UTF-8** `.ps1` 时按系统 GBK 解码 → 中文乱 → 解析崩；而 `.bat` 反过来**不能有 BOM**（cmd 会让 `@echo off` 失效）。

**解决**：用最新版脚本即可（`install.ps1` 已带 UTF-8 BOM、两个 `.bat` 已改纯 ASCII）。你若是旧 ZIP，重新拉一份最新代码再装：
```powershell
git clone https://gh-proxy.com/https://github.com/Hector-xue/IvyeaOps.git
```

---

## 6. 启动后立刻退出

**现象**：双击启动，黑窗一闪而过，页面打不开。

**原因**：服务启动后立刻退出，旧启动器用最小化窗口跑、崩了就关，看不到报错。最常见的是**端口被占用**（见下条）。

**解决**：用最新启动器（前台运行、报错保留窗口、且会先探测是否已在运行）。要手动看报错，在 PowerShell 里前台跑一次：
```powershell
cd <你的目录>\server
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```
看末尾打印的是 `Application startup complete`（正常）还是 `Traceback`/`ERROR`（按报错处理）。

---

## 7. 端口被占用

**现象**：`[Errno 10048] ... bind ... 127.0.0.1:8001 ... 每个套接字地址只允许使用一次` / `address already in use`。

**原因**：8001 已被**另一个实例**占用（多半是你之前启动的还在跑）。应用本身没问题。

**解决**：
- 多数情况它已经在跑——**直接浏览器开 http://127.0.0.1:8001** 就行。
- 想干净重启（Windows）：
  ```powershell
  netstat -ano | findstr :8001        # 看最后一列 PID
  taskkill /PID <那个PID> /F          # 杀掉
  ```
  （Linux：`lsof -i:8001` 或 `ss -ltnp | grep :8001` 找进程，`kill <pid>`。）然后只启动**一个**实例。
- 或换端口：`.env` 里改 `IVYEA_OPS_PORT=8011` 后重启。

> 别同时开多个副本/多个实例，容易自己绕晕。

---

## 8. 根路径 404（前端没构建）

**现象**：能访问 8001，但页面显示 `{"detail":"Not Found"}`。

**原因**：后端在跑，但 `client/dist` 不存在（前端没构建成功），根路径无人伺服 → FastAPI 返回 404。

**解决**：在**正在跑的那个副本**里构建前端，然后**重启服务**（dist 在启动时检测，必须重启才加载）：
```bash
cd <你的目录>/client
npm install
npm run build
# 回到服务窗口 Ctrl+C 停掉，再重新启动
```
或直接重跑安装器（它会把依赖+构建+.env 一并搞定）。访问后应是登录页。

---

## 9. 远程无头服务器怎么访问

默认只监听 `127.0.0.1`（仅本机，安全）。无头 Linux 服务器没浏览器，三选一：

- **最简单（临时试用）**：在你**本机**开 SSH 隧道，再用本机浏览器访问：
  ```bash
  ssh -L 8001:127.0.0.1:8001 用户@服务器
  # 然后本机打开 http://127.0.0.1:8001
  ```
- **正式 / 团队**：nginx 反代 + 域名 + HTTPS（见 [INSTALL.md](INSTALL.md)，内置 nginx/systemd/certbot 模板）。
- **图省事（不推荐）**：`.env` 设 `IVYEA_OPS_HOST=0.0.0.0` 并放行防火墙端口，`http://服务器IP:8001`——等于裸暴露公网，务必配强密码/防火墙。

---

## 10. AI 生成失败

**Listing 生成提示词 / 文案「Network Error」**
长 AI 请求被提前掐断。最新版已把前端超时拉到 15 分钟、nginx 对应接口放宽到 900s，慢的 hermes 也能跑完。仍失败时：
- 确认配了**至少一个可用文本模型**：hermes（已装），或在「系统配置 → 应用模型 → 全局兜底大模型」配一个 OpenAI 兼容模型（推荐 **DeepSeek**：快、便宜、国内直连）。
- 看后端日志定位：`journalctl -u ivyea-ops -n 50`（systemd）或服务窗口输出。

**`vision provider apimart failed: 403` / 图片分析失败**
**apimart 只有图片生成功能，没有文本/视觉**（调 `/v1/messages` 必 403，这是预期、不是 key 失效）。要用「AI 图片分析（识别卖点）」需配 **openai key** 或一个**支持视觉的全局兜底模型**（如 GPT‑4o 系）。apimart 仅用于生图（任务台作图 / Listing 主图）。

**所有 AI 板块都失败**
配一个「全局兜底大模型」即可全站可用——这是开箱即用的关键。首启向导有这一步。

---

## 11. 局域网共享：一台装好，其他电脑/手机不装也能用（Windows）

默认只监听 `127.0.0.1`，**别的设备访问不到**。想让**同一 Wi‑Fi / 路由器**下的其他电脑、手机也能用同一台已装好的服务：

- **一键**：双击 **「启动 IvyeaOps (局域网共享).bat」**。它会自动：
  1. 探测本机局域网 IP；
  2. 把服务绑到 `0.0.0.0`（所有网卡）；
  3. 把该 IP 加进 CSRF 白名单（**否则别的设备会登录 403**）；
  4. 放行 Windows 防火墙 8001 端口（首次会弹一次 UAC，点"是"）；
  5. 在窗口里直接显示让别人访问的网址。
- 其他设备：浏览器打开窗口里显示的 `http://<本机IP>:8001`，**什么都不用装**。

**注意事项**
- **IP 会变**：家用路由器多为 DHCP 动态分配，本机重启/重连后 IP 可能变，白名单和访问地址随之失效——**重新运行这个 .bat 即可**（每次都会重新探测）。想固定，在路由器里给这台电脑设**静态 IP / DHCP 保留**。
- **登录还是 403**：说明访问用的地址不在白名单。确认你用的是 .bat 窗口里打印的那个 IP；若手动改了 `.env`，注意进程环境变量优先于 `.env`。
- **别的设备连不上**：多半是防火墙没放行（UAC 被取消了）。手动加一条入站规则放行 TCP 8001，或右键 .bat「以管理员身份运行」一次。
- **手动版（不想用脚本）**：`server\.env` 里设 `IVYEA_OPS_HOST=0.0.0.0`，并把 `IVYEA_OPS_ALLOWED_ORIGINS` 改成 `http://127.0.0.1:8001,http://<本机IP>:8001`，再放行防火墙端口。
- **安全**：开了局域网共享 = 把控制台暴露给同网段所有人，**只在你信任的内网用**；务必设强管理员密码。不要直接把 8001 端口映射到公网（要对外请走 nginx 反代 + HTTPS，见第 9 节）。

---

> 没覆盖到的问题：带上**完整报错**（前台运行服务/查看日志拿到的那几行）去 [GitHub Issues](https://github.com/Hector-xue/IvyeaOps/issues) 反馈。
