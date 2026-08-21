# 架构决策记录（ADR）

每份文件记一个决策：当时的背景、决定了什么、为什么这么选、后来付出了什么代价。

git log 记得住「改了什么」，记不住「为什么不选另一条路」。这个目录补的就是后者。

## 什么时候写一份新的

- 选了 A 方案而否掉了 B 方案，且这个选择会长期影响后面的代码
- 引入或移除一个重量级依赖
- 改变了某个东西的边界（谁负责什么、数据从哪来）
- 踩了一个坑，而这个坑的根因值得让未来的自己记住

日常改 bug、加功能不用写 —— 那些看 [CHANGELOG](../../CHANGELOG.md) 和
维护者本机私有的开发时间线。

## 索引

| 编号 | 决策 | 日期 |
|---|---|---|
| [0001](./0001-self-hosted-web-workbench.md) | 做成自托管 Web 工作台，而不是桌面软件 | 2026-04-19 |
| [0002](./0002-own-the-frontend.md) | 放弃反向代理别人的 Dashboard，自己拥有整个前端 | 2026-05-03 |
| [0003](./0003-unify-under-one-workbench.md) | 所有 Web 能力收编到一个工作台 | 2026-05-09 |
| [0004](./0004-five-step-dev-flow.md) | 五步开发流：未经确认不得跳步写代码 | 2026-05-12 |
| [0005](./0005-agpl-license.md) | 许可证选 AGPL-3.0 | 2026-06-07 |
| [0006](./0006-rename-to-ivyeaops.md) | 更名 ops-hub → IvyeaOps | 2026-06-03 |
| [0007](./0007-lingxing-gateway.md) | 领星 ERP 走自建网关，写操作一律人工确认 | 2026-06-04 |
| [0008](./0008-lockfile-public-registry.md) | package-lock 必须指向公共 registry | 2026-06-08 |
| [0009](./0009-build-own-agent.md) | 自己做 agent，而不是一直依赖外部 CLI | 2026-06-16 |
| [0010](./0010-knowledge-base-front-door.md) | 知识库前门从 GBrain 切到 IvyeaAgent | 2026-07-10 |
| [0011](./0011-agent-chat-reliability.md) | 会话可靠性：断链不重发，改轮询落盘结果 | 2026-07-16 |
| [0012](./0012-sorftime-contract.md) | 外部数据源的真实契约要用测试钉住 | 2026-07-29 |
| [0013](./0013-drop-hermes-from-auto-paths.md) | 自动链路全面去 Hermes，统一走 IvyeaAgent | 2026-08-06 |
| [0014](./0014-drop-gbrain.md) | 彻底摘掉 GBrain | 2026-08-16 |
| [0015](./0015-fold-assistant-and-imagegen-into-console.md) | AI 问答与 AI 生图并入任务台 | 2026-08-17 |
| [0016](./0016-sync-hub-skills-into-agent.md) | Skill 中心的 amazon 技能注册进 IvyeaAgent 技能库 | 2026-08-17 |
| [0017](./0017-lucent-theme-shares-quiet-shape-layer.md) | 琉璃主题复用静谧的形状层，只加材质/高度/动效/排版 | 2026-08-21 |
| [0018](./0018-token-stats-count-cache-tokens.md) | Token 统计把缓存计入总量，并接入 IvyeaAgent / DeepSeek Harness | 2026-08-21 |
| [0019](./0019-bundle-webfonts.md) | 界面字体自带字库，直接放 public/fonts | 2026-08-21 |

## 模板

```markdown
# ADR-00XX · 一句话说清决定了什么

- **日期**：
- **状态**：已采纳 / 已废弃 / 被 ADR-00YY 取代
- **依据**：提交、PR 或会话日期

## 背景
当时遇到了什么问题。写清楚约束，不写方案。

## 决策
决定做什么。一两句话。

## 理由
为什么是这个而不是别的。把否掉的选项也写出来。

## 后果
这个决定带来了什么，包括代价和后来踩的坑。
```
