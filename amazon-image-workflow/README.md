> ## ⚠️ 已停用：IvyeaOps 不再依赖这个服务
>
> 从 2026-08-23 起，Listing 工作台的采集**完全在 IvyeaOps 进程内完成**：
> 本机 curl 直连 Amazon → 本机浏览器（Chrome/Edge/Chromium）无头渲染 → Sorftime 兜底。
> **用户不需要再安装 Docker**，安装脚本也不再询问是否启动本服务。
>
> 拆除的原因见 [ADR-0025](../docs/decisions/0025-scrape-without-docker.md)：这个容器的
> 免费采集路径本来就是同一份 `curl`，唯一独有的是 puppeteer 那层真浏览器，现已由本机
> 已装浏览器顶替（Windows 自带 Edge，等于零安装）。
>
> 目录保留只为历史与自建参考，IvyeaOps 里**没有任何代码再调用它**（`imgflow_url` 配置项
> 也已移除）。

# amazon-image-workflow（Listing 采集服务）

IvyeaOps「Listing 工作台」的**采集后端**:输入竞品 ASIN，抓取其标题/五点/图片，
供后续 AI 分析与文案/图片提示词生成。是 IvyeaOps 的**可选配套服务**——不部署它
也能用 Listing 的其余功能（手填产品信息 + 上传图片 + AI 分析/文案/提示词）。

- **免费、零密钥**:抓取默认走 `curl + cheerio`，失败回退 `puppeteer`，不需要任何
  付费 API。（Rainforest API 仅为可选加速项。）
- 技术栈:Express(后端 :3001) + Next.js(前端 :3000) + PostgreSQL(Prisma)。

## 一键启动（推荐，需 Docker）

```bash
cd amazon-image-workflow
docker compose up -d        # 起 postgres + backend(:3001) + frontend(:3000)
```

`docker-compose.yml` 自带 Postgres，且各项 env 都有内置默认值——**无需手动配置即可运行**。
IvyeaOps 默认就指向 `http://127.0.0.1:3001`（可在「系统配置」改 `imgflow_url`）。

> IvyeaOps 的一键安装脚本（`scripts/install.sh` / `安装 IvyeaOps.bat`）会在检测到
> Docker 时**询问是否顺便起这个采集服务**。

## 不用 Docker 裸跑

```bash
cd backend  && cp .env.example .env && npm install && npx prisma migrate deploy && npm run build && npm start
cd frontend && npm install && npm run build && npm start
```
（需自备一个 PostgreSQL，并把连接串填进 `backend/.env` 的 `DATABASE_URL`。）
