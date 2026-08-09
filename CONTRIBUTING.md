# 参与贡献

先说三件最省事的事：

- **报 bug**：在设置页点「导出诊断包」，把它附在 issue 里。里面有版本、平台、日志尾部、脱敏后的配置和各库的表行数 —— 你要来回问十轮的东西它一次给齐。**包里不含密钥，也不含任何店铺数据**，贴之前你可以自己打开扫一眼。
- **提 Skill**：Skill 不需要改这个仓库的代码，走 Skill 中心即可。
- **改代码**：往下看。

---

## 起开发环境

```bash
git clone https://github.com/Hector-xue/IvyeaOps.git
cd IvyeaOps

# 后端
pip install -r server/requirements.txt
pip install pytest pytest-asyncio anyio pytest-timeout ruff

# 前端
cd client && npm ci && cd ..

# 跑起来（前端 5174 / 后端 8001）
bash scripts/dev.sh
```

**Python 版本**：代码要在 **3.9** 上跑得起来。RHEL9 / Rocky / Debian 11 / Ubuntu 20.04 的系统 Python 就是 3.9，而自托管用户里这类机器占比不低。所以 `X | None` 这种 3.10 写法在**运行时求值**的位置不能用（注解位置有 `from __future__ import annotations` 兜着，没问题）。

**Node 版本**：22。`package-lock.json` 必须能在**公共 registry** 上装通 —— 如果你的 `~/.npmrc` 指着公司内网镜像，`npm ci` 会把 `resolved` 全写成内网地址，提交上去别人就装不了（这个坑踩过一次）。

---

## 提交前跑什么

```bash
cd server
ruff check .                    # lint 门禁
python -m pytest tests -q       # 约 25 秒，CI 的主要关卡
python -m pytest app/tests -q   # 约 5 分钟，CI 里是独立作业
```

前端：

```bash
cd client
npx tsc --noEmit -p tsconfig.json
npm run build
```

### 关于 lint 门禁

`server/ruff.toml` 里只收录了**当前全仓零违规**的规则。这是有意的：一上来就红几百条的门禁，第二天所有人就学会了无视它。所以任何一条变红，都必然是这次改动引入的。

存量债（`E501` 285、`BLE001` 455、`S110` 187、`UP006/UP045` 1000+）写在配置注释里记着。**清完一批就把那条规则挪进 `select`，从此不许回潮** —— 欢迎认领。

---

## 写代码时请注意

这几条不是风格洁癖，是这个仓库真的踩过的坑。

**编码必须显式给。** 任何 `open()` 都要带 `encoding="utf-8"`。中文版 Windows 默认 GBK，不带就是 `UnicodeDecodeError`，而 Windows 用户占比很高。

**别静默吞异常。** `except Exception: pass` 是"功能时好时坏说不清原因"的根。三种写法任选：窄类型 + 一行说明为什么可忽略；记录后走降级；`logger.exception` 后上抛。

**日志用惰性格式。** `logger.info("x=%s", x)` 而不是 f-string。

**子进程走 `app/core/proc.py`。** 直接 `subprocess.run` 会把整个环境递给子进程。凭据虽然已在启动时从 `os.environ` 摘走（见 `core/secret_env.py`），但 `proc.run` 还给你默认超时和审计留痕。

**改动别人会读的响应字段前，先找消费方。** 前端读的是 `err.response.data.detail`——错误响应加字段可以，删字段会让全站提示一起变哑，而且不会有任何测试报错。

**测试里碰 git 写操作，先硬隔离真实仓库。** 别用 `def f(cwd=REPO_ROOT)` 这种默认参数（绑定发生在定义时，测试重指路径根本盖不住）。

---

## 测试怎么放

| 目录 | 跑多久 | 放什么 |
|---|---|---|
| `server/tests/` | ~25 秒 | 绝大多数测试放这里。CI 在 Linux / Windows / macOS × py3.9/3.12 上都跑 |
| `server/app/tests/` | ~5 分钟 | 需要完整起 app 的集成测试。CI 里是独立作业，只跑 ubuntu |

隔离由 `server/conftest.py` 统一钉死（数据目录、日志落盘、自带技能播种、老家目录搬迁、后台任务）。**如果你的测试需要新的隔离，加在那里，别在自己文件里单独 patch** —— 只堵"想得起来的地方"是堵不住的，这个仓库为此吃过亏：一次给现有代码路径加落盘，就让一批本来干净的测试开始写生产数据，而且悄无声息。

---

## 提交与 PR

提交信息用中文，说清**为什么**而不只是做了什么。比如：

> `fix(auth): require_module 绕过了 dependency_overrides，害 app/tests 61 项常年红`

这个仓库的历史里，几乎每条有价值的提交信息都在回答"为什么当时是那样、为什么现在要改"。半年后回来读代码的人（很可能是你自己）需要的正是这个。

PR 请说明：改了什么、为什么、怎么验的。涉及 AI 提示词或模型链路的改动，要说明你怎么确认输出没变差 —— 这类改动光看 diff 判断不了好坏。

---

## 许可证

AGPL-3.0。提交即表示你同意以该许可证授权你的贡献。

选 AGPL 是有意的：任何人拿它去做云服务都必须开源改动。这跟这个产品的主张是同一件事 —— **你的数据待在你自己的机器上**。
