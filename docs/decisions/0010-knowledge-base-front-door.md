# ADR-0010 · 知识库前门从 GBrain 切到 IvyeaAgent

- **日期**：2026-07-10
- **状态**：已采纳，2026-08-16 由 [ADR-0014](./0014-drop-gbrain.md) 完成收尾
- **依据**：提交 `feat(brain): route the knowledge workbench front door to the governed IvyeaAgent KB`

## 背景

知识库（`/brain`）最初完全建立在 GBrain 上。IvyeaAgent 做出治理知识库后，出现了一个荒谬的
局面：**每次对话要查两次知识库** —— 一次调外部 GBrain 二进制、一次 agent 自己查，而外部那次
的结果基本只用来在界面上显示「引用」。

## 决策

`/brain` 的前门统一走 IvyeaAgent 的治理知识库，GBrain 降级为回退。

## 理由

- 两套检索并存意味着两套索引、两种召回质量、两处要维护
- IvyeaAgent 的检索是**语义 + 关键词双路召回**，引用质量比 GBrain 的纯关键词检索更准
- 少一次外部进程启动，响应更快

## 后果

- 对话引用检索合并成一次，引用质量提升
- GBrain 从「必需」变成「可选回退」，为一个月后彻底移除铺路
- 注意：2026-08-16 才发现「代码依赖一直还在」—— 13 个函数里有 8 个仍要启动外部 gbrain
  二进制。**「前门切了」不等于「依赖没了」**，这是这次决策留下的教训
