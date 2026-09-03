/**
 * 两个用户报的回归 —— src/lib/restatement.ts 与 src/lib/citationSource.ts 的用例。
 *
 * 不进浏览器：这两处都是纯函数。按本仓库既有做法（见 e2e/turn-stats.mjs），
 * 用 esbuild 把 TS 转成临时 esm 再 import —— 前端没有单测框架，为几十行纯函数
 * 引进一整套 runner 不划算。
 *
 * 跑：node e2e/citation-restatement.mjs
 */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const work = await mkdtemp(path.join(os.tmpdir(), "ivyea-cite-"));
async function load(rel, name) {
  const outfile = path.join(work, `${name}.mjs`);
  const build = spawnSync("npx", ["esbuild", rel, "--format=esm", `--outfile=${outfile}`],
                          { cwd: path.resolve("."), encoding: "utf8" });
  if (build.status !== 0) throw new Error(build.stderr || build.stdout || "bundle failed");
  return import(pathToFileURL(outfile).href);
}

const { restates, restatedIndexes } = await load("src/lib/restatement.ts", "restatement");
const { parseCitationSource, citationSourceLabel } = await load("src/lib/citationSource.ts", "citationSource");

// ── 表格重述：「新增一行又会重头把这个输出一遍」────────────────────────────
const T1 = `| 商品 | 售价 |
| ---- | ---- |
| 相机 | 199 |
| 支架 | 39 |`;

/* 加一行长内容 → Markdown 把**整张表的列宽重新对齐**，分隔行的短横跟着变长。
   判据原本是"归一化空白后逐字前缀"，而短横不是空白 —— 前缀在分隔行那里断掉，
   去重漏判，界面上两张表都留着。这就是用户报的那个回归。 */
const T2 = `| 商品         | 售价 |
| ------------ | ---- |
| 相机         | 199 |
| 支架         | 39 |
| 超长商品名称A | 1299 |`;

assert.equal(restates(T1, T2), true, "列宽重排后仍应判为重述（这条是回归用例）");
assert.equal(restates(T1, `${T1}\n| 灯 | 59 |`), true, "列宽不变时一直是对的，所以此前没暴露");
assert.equal(restates(T1, T1), true, "一字不差的完全重复");

// 判错的代价是吞掉一段真话 —— 下面几条必须是 false
const CHANGED = `| 商品 | 售价 |
| ---- | ---- |
| 相机 | 299 |
| 支架 | 39 |
| 灯 | 59 |`;
assert.equal(restates(T1, CHANGED), false, "数据被改过（199→299）就不是重述，绝不能吞真话");
assert.equal(
  restates("这是一段足够长的正文用于触发判重阈值，讲的是第一件事情的结论与依据。",
           "这是另一段足够长的正文用于触发判重阈值，讲的是第二件完全不同的事情。"),
  false, "两段不同的话不合并");

// 只归一**表格分隔行**，正文里的 --- 不受影响
const A = "前言足够长足够长足够长足够长足够长足够长足够长足够长足够长足够长足够长足够长\n---\n正文段落";
assert.equal(restates(A, `${A}\n补充一句`), true, "整段抄一遍再加内容仍是重述");
assert.equal(restates(A, A.replace("---", "***")), false, "正文分割线被改就不是重述");

assert.deepEqual([...restatedIndexes([T1, T2, `${T2}\n| 三脚架 | 89 |`])].sort(), [0, 1],
                 "多稿只留最后一份");

/* 第二种抄法（实测 glm-5.3-flash）：它重抄的是**手上正在写的那张表**，不是整篇 ——
   第二稿没带上第一稿的那个小标题，"以第一稿开头"因此不成立，前缀判据一条都收不住。
   但每一稿都一字不差地躺在后面那一稿里，所以判据从"前缀"放宽到"包含"。 */
const D1 = `### 1. 大厂具名薪资（全国范围公开招聘）

| 公司 | 岗位 | 月薪 | 薪数 |
|---|---|---|---|
| 字节 · 豆包 | FDE | 3.5–7 万 | 15 薪 |`;
const D2 = `| 公司 | 岗位 | 月薪 | 薪数 |
|---|---|---|---|
| 字节 · 豆包 | FDE | 3.5–7 万 | 15 薪 |
| 阿里云 | FDE | 2–5 万 | 16 薪 |`;
const FINAL = `${D1}
| 阿里云 | FDE | 2–5 万 | 16 薪 |
| 腾讯云 | FDE | 3.5–6.5 万 | 15 薪 |`;

assert.equal(restates(D1, D2), false, "第二稿没包含小标题，它作废不了第一稿");
assert.deepEqual([...restatedIndexes([D1, D2, FINAL])].sort(), [0, 1],
                 "但终稿把两份草稿都包含了 —— 三张表只留最后一张（这条是用户报的回归）");
assert.equal(restates(D2, D2.replace("2–5 万", "3–6 万")), false,
             "改了数字就是新版本，不是重述 —— 放宽到「包含」也不能吞真话");

// ── 引用来源：「不能直接点击跳转到原文」──────────────────────────────────
assert.deepEqual(parseCitationSource("ivyea://knowledge/governance.source_quality"),
                 { kind: "card", id: "governance.source_quality", raw: "ivyea://knowledge/governance.source_quality" });
assert.equal(parseCitationSource("ivyea://knowledge-governance/professional-standard").id,
             "professional-standard", "分类段不同也要认");
assert.equal(parseCitationSource("ivyea-upload://up-20260713-162722-b893258e/knowledge-20260713.md").path,
             "up-20260713-162722-b893258e/knowledge-20260713.md");

// 官方卡有真实外网原文，本来就走普通链接，不能被截胡
assert.equal(parseCitationSource("https://sell.amazon.com/fulfillment-by-amazon"), null);

// 伪协议防线不能因为多认了两个 scheme 就开口子
for (const bad of ["javascript:alert(1)", "data:text/html,<script>", "file:///etc/passwd"]) {
  assert.equal(parseCitationSource(bad), null, `伪协议必须不认：${bad}`);
}
for (const broken of ["", "ivyea://knowledge/", "ivyea-upload://"]) {
  assert.equal(parseCitationSource(broken), null, `残缺输入退回纯文本：${JSON.stringify(broken)}`);
}

const up = parseCitationSource("ivyea-upload://a/b.md");
assert.ok(!citationSourceLabel(up).includes("ivyea"), "标签不该暴露原始 URI");
assert.ok(citationSourceLabel(up).includes("原文"));

// ── 来源链条：从步骤里挑出真抓过的网页（不猜、不做模糊匹配）──────────────
const { answerSources } = await load("src/lib/answerSources.ts", "answerSources");
const steps = [
  { name: "web_search", args: { query: "FDE 薪资" } },
  { name: "web_fetch", args: { url: "https://ccaf101.com/fde/salary" } },
  { name: "web_fetch", args: { url: "https://ccaf101.com/fde/salary" } },   // 同一页抓两遍
  { name: "web_fetch", args: { url: "https://www.fdehub.cc/report?y=2026" } },
  { name: "web_fetch", args: { url: "ivyea://knowledge/x" } },             // 内部协议不是外链
  { name: "web_fetch", args: {} },
  { name: "read_file", args: { url: "https://not-a-fetch.example/" } },
];
assert.deepEqual(answerSources(steps).map((s) => [s.host, s.path]),
                 [["ccaf101.com", "/fde/salary"], ["fdehub.cc", "/report?y=2026"]],
                 "只要抓过的网页，去重、去 www、非 http 一律不摆");
assert.deepEqual(answerSources([]), [], "没抓过网页就没有这一条");

console.log("citation-restatement: 全部通过");
