# ADR-0025 · Listing 采集去掉 Docker，兜底改用本机浏览器

- **日期**：2026-08-23
- **状态**：已采纳
- **依据**：Listing 工作台采集链重做（见 CHANGELOG Unreleased）

## 背景

Listing 工作台的采集要拿到竞品的**完整主图组**（轮播 7 张）。当时的链路是：

1. 本机 `curl` 直连 Amazon（进程内，主路径）
2. `amazon-image-workflow` —— 一套 docker-compose 应用（Express + Next.js + PostgreSQL），
   兜底
3. Sorftime —— 只有 1 张白底主图

于是安装脚本会检测 Docker 并**劝用户装 Docker Desktop**（Linux 那条还是默认 Y），
Listing 界面在只采到 1 张图时也会弹一个「启动 Docker 采集服务」按钮。

对一个「双击就能用」的自托管工具来说，为了一个采集兜底让用户先装 Docker Desktop（几个 GB、
要开虚拟化、还得等镜像构建几分钟），是安装流程里最贵的一步。

## 决策

删掉 Listing 对 `amazon-image-workflow` 的全部依赖，兜底改成**本机已装的浏览器**
（Chrome / Edge / Chromium）无头渲染。新链路：

1. 本机 `curl` 直连（不变）
2. 本机浏览器 `--headless --dump-dom`（新）
3. Sorftime 单图（不变）

配套移除：`imgflow_url` 配置项、`/imgflow/status`、`/imgflow/start`、建项目时向 :3001
注册、AI 分析里的 imgflow 深度分析、两个安装脚本里的 Docker 劝装段落。

## 理由

- **看了那个容器的实现再做的决定**：它的免费采集路径就是同一份 `curl + cheerio`，
  唯一独有的是失败后的 puppeteer。也就是说，我们为了一层「真浏览器」，让用户装了整个 Docker。
- 那一层不必用 Docker 换：Windows 10/11 自带 Edge，Linux/macOS 上 Chrome/Chromium 极普遍，
  `--dump-dom` 是浏览器自带的能力，不需要 node、不需要 puppeteer、不需要装任何东西。
  拿不到浏览器时这一档自动跳过，不报错。
- 顺带修掉一个一直在的体验 bug：建项目时会先去 POST `http://127.0.0.1:3001`，没装 Docker 的
  用户每次新建都要干等一次连接超时。
- 否掉的选项：**保留 Docker 兜底但默认不劝装**。不选它是因为那会留下一条谁都没验过的路径 ——
  文档里写着、代码里挂着、实际没人跑，坏了也没人知道。

## 后果

- 采集不再有任何外部服务依赖，安装流程少一步。
- 反爬全档命中时仍会退到 Sorftime 的 1 张白底图 —— 提示语改成引导「重新采集」
  （反爬多是临时的），而不是引导装东西；本机没有浏览器时会明说少了哪一档。
- `amazon-image-workflow/` 目录保留但已停用（README 顶部加了停用横幅），IvyeaOps 里没有
  任何代码再调用它。要不要整个删掉是后续的独立决定。
- 采集能力从此**只由我们自己的代码决定**，坏了能自己修 —— 这比多一条兜底更重要。
