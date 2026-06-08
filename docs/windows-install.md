# IvyeaOps · Windows 一键安装

给 Windows 用户的「双击即装、双击即用」教程。拿到的是 IvyeaOps 源码（GitHub
Download ZIP，约 4 MB）。整套基本是双击 + 等待，约 5–10 分钟。

> IvyeaOps 是自托管的运营工作台，跑在你自己的电脑上，浏览器访问，数据都在本地。

---

## 一、双击安装

1. 把压缩包**解压**出来（得到 `IvyeaOps-main` 之类的文件夹），路径尽量用**英文、无空格**。
2. 进文件夹，**双击「安装 IvyeaOps.bat」**。

安装脚本会自动完成：

- **自动检测环境**：Python 3.9+ 和 Node 18+。**缺哪个就用 winget 自动装哪个**
  （Win10/11 自带 winget；装完可能需要按提示重开一次再双击）。
- 如果下载的是 `IvyeaOps-Windows-x64.zip`，后端已打成 `IvyeaOpsServer.exe`，不需要安装 Python；双击「安装 IvyeaOps Windows x64.bat」即可。
- 如果下载的是普通 `IvyeaOps.zip`，会创建独立虚拟环境、装后端依赖；Release 包已预置 Windows Python 3.12 依赖包，优先离线安装；源码包或版本不匹配时自动回退在线安装。
- 预构建 Release 包已自带前端，不需要 Node；只有源码包才会自动检测/安装 Node 并构建前端。
- 生成配置 `server\.env`：**管理员密码直接回车 = 自动随机生成并显示**（记下来即可）。
- 会问你**是否顺便安装本地 Hermes Agent + GBrain 知识库**（可选，输 `y` 即装；
  Hermes 是官方 Windows 原生安装器，GBrain 经 Bun 安装并初始化本地 PGLite 库）。
  不装也能用——首启向导填个「全局兜底大模型」即可跑全部 AI。
- 在**桌面创建「IvyeaOps」快捷方式**（默认后台启动，不常驻终端窗口）。
- 同时保留「启动 IvyeaOps.bat」（可见窗口排错）和「停止 IvyeaOps.bat」（停止后台服务）。
- 最后问你「现在就启动吗？」，回车即启动。

> 不想用自动装环境，也可以自己先装 [Python](https://www.python.org/downloads/)
> （勾选 *Add to PATH*）和 [Node.js LTS](https://nodejs.org/)，再双击安装。

---

## 二、日常使用：双击启动

以后**双击桌面的「IvyeaOps」快捷方式**即可：

- 服务在后台隐藏窗口中启动，几秒后**浏览器自动打开** **http://127.0.0.1:8001**。
- 任务栏不会常驻黑色终端窗口；关闭浏览器不会停止服务。
- 用安装时的管理员密码登录（用户名 `admin`）。
- 首次进入有「首启向导」：跟着走，**填一个「全局兜底大模型」**（任意 OpenAI 兼容
  模型的 Key 即可），全站 AI 功能立刻可用；领星 / 生图等的密钥填你自己申请的。

> **关闭服务**：双击文件夹里的「停止 IvyeaOps.bat」。
> **排错看日志**：双击「启动 IvyeaOps.bat」会用可见窗口启动；后台模式日志在 `logs\ivyeaops.out.log` 和 `logs\ivyeaops.err.log`。

---

## 三、常见问题

| 现象 | 解决 |
|---|---|
| winget 自动装完 Python/Node 后仍说找不到 | PATH 还没刷新。**关掉窗口，重新双击「安装 IvyeaOps.bat」**即可。 |
| 没有 winget / 自动安装失败 | 手动装 [Python 3.12](https://www.python.org/downloads/)（勾 Add to PATH）和 [Node.js LTS](https://nodejs.org/)，重开后再双击安装。 |
| 不想安装 Python | 下载 Release 里的 `IvyeaOps-Windows-x64.zip`，解压后双击「安装 IvyeaOps Windows x64.bat」。 |
| 后端依赖安装很慢 | 优先下载 Release 页面里的 `IvyeaOps.zip`，它已预置 Windows Python 3.12 依赖包；源码 ZIP 会走在线 pip。 |
| 看到“预置依赖包面向 Python 3.12”提示 | 说明你电脑已有 Python 3.9/3.10/3.11，安装器会自动回退在线 pip；想完全走预置依赖，可安装 Python 3.12 后重跑。 |
| 双击 .bat 一闪而过 | 右键 →「以管理员身份运行」，或在文件夹地址栏输 `cmd` 回车后手动运行，看报错信息。 |
| 端口 `8001` 被占用 | 改 `server\.env` 里的 `IVYEA_OPS_PORT`（同时把 `IVYEA_OPS_ALLOWED_ORIGINS` 的端口一并改），重新启动。 |
| 想重设管理员密码 | 删掉 `server\.env`，重新双击安装；或登录后在「系统配置 → 账号安全」里改。 |
| 桌面没出现快捷方式 | 不影响使用，直接双击文件夹里的「启动 IvyeaOps (后台).vbs」。 |
| 想看启动日志/报错 | 双击「启动 IvyeaOps.bat」用可见窗口模式启动，或查看 `logs\ivyeaops.err.log`。 |
| 想彻底停止后台服务 | 双击「停止 IvyeaOps.bat」。 |

---

## 四、说明

- **API 密钥都填你自己的**（全局兜底大模型 / 领星 / 生图等），只存在本地。
- **「服务器终端」板块在 Windows 上用不了**（PTY 限制），其余板块（智能体会话、
  市场调研、Listing、打法、生图、GBrain 知识库、资讯等）都正常。
- **可选 Docker**：装了 Docker Desktop 的话，也可 `copy .env.example .env` 后
  `docker compose up -d`，访问 http://localhost:8080。但上面的双击方式更省事。
