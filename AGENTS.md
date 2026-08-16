# IvyeaOps 协作规约

面向在这个仓库里干活的**任何** AI 编码助手（Claude Code / Codex / Hermes / Cursor / Kiro …），
以及未来的自己。

> 各家工具读的文件名不同，这份是唯一的正本。`CLAUDE.md` 只是指向这里的入口。
> 改规约改这份，不要改入口文件。

## 这是什么

自托管的 Amazon 运营工作台。用户在自己的服务器 / Windows / macOS 上部署，浏览器访问。
AGPL-3.0。前端 React + Vite + Tailwind（`client/`），后端 FastAPI（`server/`）。

AI 能力统一由 [IvyeaAgent](https://github.com/Hector-xue/ivyea-agent) 提供，它随本项目一起
打包分发。

## 常用命令

```bash
cd client && npm run build      # 前端构建（改前端后必须跑，生产直接吃 dist）
cd client && npm run typecheck  # 类型检查
cd server && python3 -m pytest  # 后端测试
systemctl restart ivyea-ops     # 改后端后重启生效
```

改前端**只需要 build**，改后端**必须重启服务**。

## 工作方式：五步开发流

收到开发任务后按顺序执行，回复里显式标出步骤：

1. **理解需求边界** —— 确认要做什么、不做什么
2. **制定技术方案**
3. **复核方案并优化**
4. **执行**（零省略）
5. **校验**

**未经确认不得跳步直接写代码。** 这条规矩定于 2026-05-12，理由见
[ADR-0004](./docs/decisions/0004-five-step-dev-flow.md)。

配套要求：

- **动手前必须核实**消费方契约、真实数据来源、运行环境（库版本 / CLI 行为）。第一步如果建立
  在猜测上，后面四步全是错的。
- **声明完成前必须自检消费方** —— 改了接口/契约就要检查所有调用它的地方。
- **验收看真实运行**。改 UI 要在真实组件树里验证（`client/harness`），不要手写一个 demo 页面
  自欺欺人；跑 headless 浏览器读 computed 值，不要靠截图猜。
- 局部改动也给完整代码。

## 落档纪律（重要，每次会话都要做）

**做完事必须把结果写进文档。对话会消失，文档不会。** 不管你是哪个工具，这条都适用。

### 一、进仓库（会随开源发布出去，面向使用者和贡献者）

| 做了什么 | 落到哪 |
|---|---|
| 对使用者有影响的改动 | [CHANGELOG.md](./CHANGELOG.md) 的 Unreleased 段，写人话，讲清对用户的影响 |
| 方向性取舍、引入/移除依赖、踩到值得记住的坑 | 新建 [docs/decisions/](./docs/decisions/) 下的 ADR，编号顺延，并更新该目录 README 的索引表 |

纯重构、内部整理不进 CHANGELOG —— 那些看 git log 就够了。

### 二、留本机（作者的私人记录，**不要提交进仓库**）

位置：`/root/dev-history/ivyea-ops/`

| 做了什么 | 落到哪 |
|---|---|
| 发版了、或做完一件成规模的事 | `timeline.md` 追加当天条目，标注日期与所用工具 |
| 需求状态变化（新需求 / 开工 / 完成 / 否决） | `roadmap.md` |

同目录下的 `journal.md` 由 git 钩子自动追加，**不要手工编辑**，它是写 `timeline.md` 时的素材。

`/root/dev-history/` 同时存着多个 AI CLI 的原始会话与逐日摘要，2026-04 至今的历史就是从那里
还原的。**那些原始记录含密钥、密码、客户信息和真实 ASIN，连同上面两份私人记录，都不能进这个
公开仓库。**

## 发布纪律

- **未经明确批准不要 push / 开 PR / 合并 / 打 tag 发版。** 改完本地 commit 后停下汇报，
  问「要推 / 发版吗」。
- 本机改 + 构建 + 重启自测 + 本地 commit 可以不问。
- `main` 受 ruleset 保护：禁删、禁 force push、须走 PR（审批数 0，可自合）。自动化一律
  建分支 + PR，不要直推。
- 发版要带上 IvyeaAgent 的 release tag，解析不到就让构建失败，不能悄悄回退 main。

## 几条容易再犯的坑

- **改代码里的默认值不等于改了行为** —— 很多链路的实际选择存在数据库配置里。
- **同一个参数不要在前后端各写一份默认值**，实际生效的是小的那个。
- `package-lock.json` 的 `resolved` 必须指向公共 registry，不能是内网镜像
  （[ADR-0008](./docs/decisions/0008-lockfile-public-registry.md)）。
- 环境变量前缀是 `IVYEA_OPS_*`，迁移老部署时 `SECRET` 和 `PASSWORD_HASH` 的值要原样保留。
- 生产机上**严禁宽泛的 `pkill -f`**，只杀精确 PID。
- 新加的服务绑 `127.0.0.1`，通过主入口鉴权后代理。

## 想了解这个项目怎么走到今天

- [docs/decisions/](./docs/decisions/) —— 14 份 ADR，为什么这么选（在仓库里）
- `/root/dev-history/ivyea-ops/timeline.md` —— 逐日时间线，从 2026-04-19 第一句需求起（本机私有）
- `/root/dev-history/ivyea-ops/milestones.md` —— 十个转折点（本机私有）
