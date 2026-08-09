# 无署名 Skill 的作者取证

21 个内置 Skill 的 `SKILL.md` 里没有 `author` 字段。播种脚本不敢猜 —— 空作者当成
"我们的"，就会把别人的东西署上自己的名字发出去。这份文档把 21 个逐一取证，
把需要你亲自定夺的收窄到 **2 个**。

取证用的证据源，按可信度从高到低：

1. **引用了本项目内部实现** —— 提到 ops-hub 的 `ad_audit` 流水线、`html_report.py`
   的具体字段名、自家的部署拓扑。外人写不出来。
2. **与上游 Hermes 自带集逐字节比对**（`~/.hermes/hermes-agent/skills`）。
3. **自指标记密度** —— ivyea / ops-hub / 领星 / Sorftime / 门道 出现次数。
4. **写作形态** —— 英文优先 + `description_zh` 一行，是上游那批的统一形状。

> git 历史**不能**当作者证据：这批文件是同一个"开源就绪整改"提交批量 vendor 进来的，
> 而同批里 `amazon-ad-campaign-optimization-xlsx`、`amazon-asin-cosmo-rufus-audit`
> 两个是明确署名 Hermes Agent 的。同批出现只说明"同时搬进来"，不说明谁写的。

---

## A. 你的作品（证据充分，共 9 个）

| Skill | 决定性证据 |
|---|---|
| `amazon/zach-search-term-report-analyzer` | 引用 ops-hub `ad_audit` 流水线的 JSON 字段清单与 `html_report.py` 的 12 板块渲染；带实盘案例「FKPCAM FK50 trail camera 2026-05-12」 |
| `software-development/ops-hub-development` | 420 处自指；开头写明 based on github.com/Hector-xue/IvyeaOps |
| `software-development/reference-implementation-adoption` | 有一节标题就叫「For ops-hub style targets」；参考文档写明目标是「existing ops-hub web control panel with its own React/FastAPI live terminal stack」 |
| `devops/docker-webapp-deploy` | based on github.com/Hector-xue/ops-hub.git；18 处自指 |
| `devops/ttyd-mobile-toolbar` | 15 处自指，写的是本机 ttyd/tmux 那套主终端 |
| `media/ai-amazon-daily-digest` | 13 处自指 |
| `.archive/ivyea-landing-page` | 20 处自指，就是 ivyea.com 落地页 |
| `.archive/api-relay-platform-research` | 内容是本机排障记录（headless Linux、`libnspr4.so` 缺失），是你自己的调查笔记 |
| `.archive/feishu-doc-create` | 开头写「Discovered during handbook sync — existing Hermes feishu tools are read-only」，是你自己的发现记录 |

**处理**：补 `author` 为你，可作为「原创」上架。

## B. 第三方（证据充分，共 8 个）

与上游 Hermes 自带集比对，正文一字不差，本地只多了一行 `description_zh` 中文描述：

`.archive/heartmula` · `creative/ascii-video` · `creative/manim-video` ·
`creative/p5js` · `media/youtube-content` · `note-taking/obsidian`

另两个是**上游为底 + 你有实质改造**：

| Skill | 本地改动量 |
|---|---|
| `creative/songwriting-and-ai-music` | SKILL.md 21 行本地新增 |
| `devops/kanban-orchestrator` | SKILL.md 38 行差异，另整个 `references/`(24K) 与 `templates/`(8K) 是本地新加 |

**处理**：前 6 个以「分享」上架并保留 Hermes Agent 署名；后 2 个同样保留原署名，
在描述里注明「本地增补」。

## C. 需要你定（2 个）

### 1. `amazon/amazon-listing-creative`
- 自指 0 处，无任何 ops-hub 内部引用
- 131 行里只有 6 行中文，是英文优先 + `description_zh` 一行的**上游形状**
- 但上游两份拷贝里都找不到它

**问题**：这个是你写的，还是从别处拿的（比如某个已经不在本机的 Hermes 版本、
或某个技能包）？

### 2. `amazon/amazon-market-research`
- 79 行里 60 行中文，中文运营口吻，带 1 处具体案例痕迹 —— 像你写的
- 但通篇只提 Sorftime（第三方数据源），没有任何 ops-hub 内部引用可以坐实
- 上游两份拷贝里都没有

**问题**：这个是你自己整理的市场调研方法，还是从哪儿改的？

---

## 结论怎么用

A 组 9 个补 `author` 后可直接原创上架；B 组 8 个走「分享」保留原署名。
只有 C 组这 2 个需要你回一句，之后 21 个就全部可以进市场。
