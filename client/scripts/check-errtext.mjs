#!/usr/bin/env node
/**
 * 门禁：不许再把后端的 `detail` 直接当文案用。
 *
 * 起因是一次真实事故：能力市场传错了一个查询参数 → FastAPI 回 422，而 422 的
 * `detail` 是**对象数组**而不是字符串。那一段（以及当时全站 90 多处）都是
 * `setErr(e?.response?.data?.detail)` 之后 `{err}` 直接渲染 —— 对象不能作为
 * React 子节点，整页当场崩成「渲染失败」。一个参数写错本该只是一条提示，
 * 结果变成了白屏，而真正的原因一个字都没露出来。
 *
 * 统一改走 lib/errText.ts 之后，这个脚本负责挡住回潮。思路同 ruff 门禁：
 * **只收当前已经清零的规则** —— 任何一次变红，都必然是这次改动引入的。
 *
 * 豁免：
 * - `lib/errText.ts` 自己（它就是负责解析 detail 的那个）
 * - `api/client.ts` 的拦截器（它把 detail 传给自动修复，不渲染）
 * - `src/agents/`（上游 claudecodeui 子树，有自己的错误处理约定）
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const ROOT = new URL("../src", import.meta.url).pathname;
const SKIP_DIRS = new Set(["agents", "node_modules", "dist"]);
const ALLOW = new Set(["lib/errText.ts", "api/client.ts"]);
const PATTERN = /\.response\s*\??\.\s*data\s*\??\.\s*detail/;

/** @param {string} dir @param {string[]} out */
function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (!SKIP_DIRS.has(name)) walk(p, out);
    } else if (/\.tsx?$/.test(name) && !name.endsWith(".d.ts")) {
      out.push(p);
    }
  }
  return out;
}

const hits = [];
for (const file of walk(ROOT)) {
  const rel = relative(ROOT, file).replace(/\\/g, "/");
  if (ALLOW.has(rel)) continue;
  readFileSync(file, "utf8").split("\n").forEach((line, i) => {
    if (PATTERN.test(line)) hits.push(`${rel}:${i + 1}  ${line.trim().slice(0, 100)}`);
  });
}

if (hits.length) {
  console.error(
    `\n× 有 ${hits.length} 处直接读了后端的 detail：\n\n` + hits.join("\n") +
    `\n\n  改用 errText(err, "兜底文案")（src/lib/errText.ts）。\n` +
    `  原因：FastAPI 的 422 里 detail 是对象数组，直接渲染会让整页崩成「渲染失败」，\n` +
    `  而真正的原因（某个参数传错了）一个字都不会露出来。\n`);
  process.exit(1);
}
console.log("✓ 没有直接读 detail 的地方");
