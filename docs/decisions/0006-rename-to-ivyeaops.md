# ADR-0006 · 更名 ops-hub → IvyeaOps

- **日期**：2026-06-03
- **状态**：已采纳
- **依据**：提交 `refactor: 全局品牌改名 ops-hub → IvyeaOps`

## 背景

项目名一路从 `web_panel` → `control_panel` → `ops-hub` 演化而来，都是描述性的临时名字。
开源前需要一个能作为品牌的名字，与门户站 `ivyea.com` 统一。

## 决策

全局改名 IvyeaOps，同期把 `cloudcli` / `ccui` 这些遗留字眼统一改成 `agents`。

**环境变量前缀同步改动**：`OPSHUB_*` → `IVYEA_OPS_*`。

## 理由

- 品牌统一：门户 ivyea.com、工作台 IvyeaOps、agent IvyeaAgent、翻译 Ivyea Translate
- `cloudcli` 是移植来源的名字，留着会让人以为还依赖那个项目

## 后果

**这次改名留下一个反复咬人的坑**：配置读取只认 `IVYEA_OPS_*`。迁移时如果 `.env` 还是旧前缀，
`PASSWORD_HASH` 和 `SECRET` 读不到，表现为登录 401 —— 而报错信息完全不提前缀这回事。

迁移老部署时必须**原样保留 SECRET 和 PASSWORD_HASH 的值**，只改前缀名。
