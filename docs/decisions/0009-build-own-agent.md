# ADR-0009 · 自己做 agent，而不是一直依赖外部 CLI

- **日期**：2026-06-16
- **状态**：已采纳
- **依据**：IvyeaAgent 仓库首批提交；相关会话见 2026-06-18 Codex 会话

## 背景

在此之前，IvyeaOps 的所有 AI 能力都靠外部 CLI：Hermes、Codex、Claude Code。这带来几个绕不过
去的问题：

- **不可控**：外部 CLI 的输出格式、退出行为、超时策略随版本变，工作台只能被动适配
- **装不上**：开源用户要用工作台的 AI 能力，得先自己装通一个外部 CLI，劝退率极高
- **不专业**：通用 CLI 不懂 Amazon 广告，每次都要靠长 prompt 现教
- **额度**：外部 CLI 的额度、登录态、风控都不在自己手里

## 决策

自建 IvyeaAgent —— 一个专做 Amazon 场景、可被 IvyeaOps 直接驱动的 agent，独立成仓库和
独立产品，同时打包进 IvyeaOps 一起分发。

## 理由

- 工作台需要的是**结构化、可编程驱动**的 agent（后来的 `--output-format stream-json` 就是
  为此），而不是给人看的终端界面
- Amazon 领域知识、审核制写操作、护栏，这些沉淀在自己的 agent 里才有复利
- 自托管产品的依赖越少，别人装起来的成功率越高

## 后果

- 两个项目从此互为主线，发版要联动（IvyeaOps 打包 IvyeaAgent 的 release tag）
- 2026-06-26 IvyeaAgent 被打包进 IvyeaOps，安装不再需要单独一步
- 2026-08-06 自动链路全面去 Hermes（[ADR-0013](./0013-drop-hermes-from-auto-paths.md)）
- 2026-08-16 知识库摘掉 GBrain（[ADR-0014](./0014-drop-gbrain.md)）
- 代价是要自己维护一个 agent 的全部能力：工具循环、上下文压缩、记忆、多 provider、
  权限审批 —— 这些在 2026 年 6–8 月占了大量工作量
