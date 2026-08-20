<!-- 发行包的固定安装说明。发版时由 release.yml 接在 CHANGELOG 本版那一节后面。
     版本相关的内容写进 CHANGELOG.md，别写在这里。 -->
**预构建发行包** —— 已包含编译好的前端 `client/dist` 和 Windows Python 3.12 后端依赖包 `server/vendor/wheels`。
Windows 安装脚本会优先从预置 wheelhouse 离线安装后端依赖；如果用户是源码包、Python 版本不匹配或离线包缺失，
会自动回退在线 pip 安装。安装脚本检测到已自带前端，会**跳过 Node 安装与前端构建**（含小内存机的 swap 步骤）。

### Windows 用户（无需安装 Node）
下载下方 `IvyeaOps.zip` → 解压 → 双击「安装 IvyeaOps.bat」。

### Linux / macOS（尤其小内存服务器，免构建免 swap）
```bash
curl -L https://github.com/Hector-xue/IvyeaOps/releases/latest/download/IvyeaOps.zip -o IvyeaOps.zip
unzip IvyeaOps.zip && cd IvyeaOps && bash scripts/install.sh
```

### Windows x64 免 Python 版
如果不想安装 Python，可下载 `IvyeaOps-Windows-x64.zip` → 解压 → 双击 `IvyeaOpsServer.exe`。启动后会出现控制窗口，关闭窗口即停止服务；更新可在侧边栏点击「更新」或双击「更新 IvyeaOps Windows x64.bat」。

### 开发者
也可 `git clone` 源码自行构建（需 Node 18+ 与 ≥2G 内存）。
