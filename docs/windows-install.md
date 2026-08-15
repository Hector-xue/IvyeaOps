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
- 如果下载的是 `IvyeaOps-Windows-x64.zip`，后端已打成 `IvyeaOpsServer.exe`，不需要安装 Python；解压后直接双击 `IvyeaOpsServer.exe` 即可。它会自动生成登录信息文件、创建桌面快捷方式、打开控制窗口并启动浏览器。
- 如果下载的是普通 `IvyeaOps.zip`，会创建独立虚拟环境、装后端依赖；Release 包已预置 Windows Python 3.12 依赖包，优先离线安装；源码包或版本不匹配时自动回退在线安装。
- 预构建 Release 包已自带前端，不需要 Node；只有源码包才会自动检测/安装 Node 并构建前端。
- 生成配置 `server\.env`。免 Python x64 包会自动生成管理员密码，并保存到桌面和 `data\IvyeaOps 登录信息.txt`；普通包会在安装时让你设置或自动生成密码。
- 默认安装并启动 **IvyeaAgent**（Agent + 知识库 + 本地检索），右下角会出现常驻会话入口。
  Hermes / Ollama 仅作为旧兼容组件保留，后续可在「系统配置 → 系统状态」里手动安装 / 修复。
  首启向导再填一个「全局兜底大模型」，主要 AI 功能即可开箱使用。
- 在**桌面创建「IvyeaOps」快捷方式**。
- x64 免 Python 包会显示控制窗口；关闭控制窗口即停止服务，「停止 IvyeaOps.bat」仍作为备用入口。
- 同时保留「启动 IvyeaOps.bat」（普通包可见窗口排错）和「停止 IvyeaOps.bat」（备用停止服务）。
- 免 Python x64 包启动后会打开控制窗口和浏览器；普通包最后会问你「现在就启动吗？」。

> 不想用自动装环境，也可以自己先装 [Python](https://www.python.org/downloads/)
> （勾选 *Add to PATH*）和 [Node.js LTS](https://nodejs.org/)，再双击安装。

---

## 二、日常使用：双击启动

以后**双击桌面的「IvyeaOps」快捷方式**即可：

- x64 免 Python 包会显示 IvyeaOps 控制窗口，几秒后**浏览器自动打开** **http://127.0.0.1:8001**。
- 任务栏不会常驻黑色终端窗口；关闭浏览器不会停止服务。
- 用安装时的管理员密码登录（用户名 `admin`）。免 Python x64 包可查看桌面或 `data\IvyeaOps 登录信息.txt`。
- 首次进入有「首启向导」：跟着走，**填一个「全局兜底大模型」**（任意 OpenAI 兼容
  模型的 Key 即可），全站 AI 功能立刻可用；领星 / 生图等的密钥填你自己申请的。

> **关闭服务**：x64 免 Python 包直接关闭 IvyeaOps 控制窗口；「停止 IvyeaOps.bat」仍可作为备用入口。
> **排错看日志**：双击「启动 IvyeaOps.bat」会用可见窗口启动；后台模式日志在 `logs\ivyeaops.out.log` 和 `logs\ivyeaops.err.log`。

---

## 三、常见问题

| 现象 | 解决 |
|---|---|
| winget 自动装完 Python/Node 后仍说找不到 | PATH 还没刷新。**关掉窗口，重新双击「安装 IvyeaOps.bat」**即可。 |
| 没有 winget / 自动安装失败 | 手动装 [Python 3.12](https://www.python.org/downloads/)（勾 Add to PATH）和 [Node.js LTS](https://nodejs.org/)，重开后再双击安装。 |
| 不想安装 Python | 下载 Release 里的 `IvyeaOps-Windows-x64.zip`，解压后双击 `IvyeaOpsServer.exe`；启动后会显示控制窗口并自动打开浏览器。 |
| 后端依赖安装很慢 | 优先下载 Release 页面里的 `IvyeaOps.zip`，它已预置 Windows Python 3.12 依赖包；源码 ZIP 会走在线 pip。 |
| 看到“预置依赖包面向 Python 3.12”提示 | 说明你电脑已有 Python 3.9/3.10/3.11，安装器会自动回退在线 pip；想完全走预置依赖，可安装 Python 3.12 后重跑。 |
| 双击 .bat 一闪而过 | 右键 →「以管理员身份运行」，或在文件夹地址栏输 `cmd` 回车后手动运行，看报错信息。 |
| 端口 `8001` 被占用 | 改 `server\.env` 里的 `IVYEA_OPS_PORT`（同时把 `IVYEA_OPS_ALLOWED_ORIGINS` 的端口一并改），重新启动。 |
| 想重设管理员密码 | 删掉 `server\.env`，重新双击安装；或登录后在「系统配置 → 账号安全」里改。 |
| 不知道账号密码 | 免 Python x64 包看桌面或 `data\IvyeaOps 登录信息.txt`；普通包看安装窗口生成的密码。 |
| 桌面没出现快捷方式 | 不影响使用，x64 包直接双击 `IvyeaOpsServer.exe`；普通包可双击文件夹里的「启动 IvyeaOps (后台).vbs」。 |
| 想看启动日志/报错 | 双击「启动 IvyeaOps.bat」用可见窗口模式启动，或查看 `logs\ivyeaops.err.log`。 |
| 想彻底停止后台服务 | x64 包关闭控制窗口；也可以双击「停止 IvyeaOps.bat」。 |

---

## 四、说明

- **API 密钥都填你自己的**（全局兜底大模型 / 领星 / 生图等），只存在本地。
- **「服务器终端」板块在 Windows 上用不了**（PTY 限制），其余板块（IvyeaAgent、
  市场调研、Listing、打法、生图、知识库、资讯等）都正常。
- **可选 Docker**：装了 Docker Desktop 的话，也可 `copy .env.example .env` 后
  `docker compose up -d`，访问 http://localhost:8080。但上面的双击方式更省事。
