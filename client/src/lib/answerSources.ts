/**
 * 这一轮**真的抓过**哪些网页 —— 回答底下那条「原文」链条的数据来源。
 *
 * ── 在修什么 ──────────────────────────────────────────────────────────────
 * 用户读完一份网页调研的回答说："末尾的引用来源还是不能直接点击跳转。"那份来源
 * 清单长这样 ——「ccaf101.com《FDE 薪资：分档与数据》（2026）」—— 八条里一个 URL
 * 都没有。不是渲染器拦了链接，是**回答里压根没有链接**：模型把站名写进了清单，
 * 把地址留在了自己的工具调用里。
 *
 * 模型那一侧另有治法（web_fetch 的结果末尾会把地址和"要写成 markdown 链接"一起
 * 交回给它）。但那条要靠模型配合，这条不用：**哪些页面被抓过是运行时的事实**，
 * 步骤事件里一条不落地记着。
 *
 * 所以这里只摆确定的东西：不猜、不做模糊匹配。把「潮新闻」猜到某个域名上，猜错
 * 就是伪造出处 —— 那比没有链接坏得多。
 */
import type { ConsoleStep } from "./stepLabels";

export type AnswerSource = { url: string; host: string; path: string };

/** 从这一轮的步骤里挑出抓过的网页，按发生顺序去重。 */
export function answerSources(steps: ConsoleStep[] = []): AnswerSource[] {
  const out: AnswerSource[] = [];
  const seen = new Set<string>();
  for (const step of steps) {
    if (step.name !== "web_fetch") continue;
    const raw = String((step.args || {}).url || "").trim();
    if (!/^https?:\/\//i.test(raw) || seen.has(raw)) continue;
    let host = "";
    let path = "";
    try {
      const u = new URL(raw);
      host = u.host.replace(/^www\./, "");
      path = (u.pathname === "/" ? "" : u.pathname) + u.search;
    } catch {
      continue;                        // 解析不了的地址不摆出来，免得给个点不开的链接
    }
    seen.add(raw);
    out.push({ url: raw, host, path });
  }
  return out;
}
