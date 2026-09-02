/**
 * 引用来源里的**内部协议**。
 *
 * ── 在修什么 ──────────────────────────────────────────────────────────────
 * 回答结尾的「引用知识」是 agent 生成的，每条形如：
 *
 *   - [K1] 标题 — https://sell.amazon.com/…        (官方卡：有真实外网原文)
 *   - [K2] 标题 — ivyea://knowledge/<id>            (治理卡：没有外网原文)
 *   - [K3] 标题 — ivyea-upload://<uid>/<file>.md    (用户上传的文档：同上)
 *
 * 渲染器出于安全只放行 http(s)/data:image/站内相对路径 —— `javascript:` 这类伪协议
 * 必须挡在外面。于是后两类被渲染成**点不动的死文本**，而且屏幕上还杵着一串
 * `ivyea-upload://up-20260713-…` 的乱码（用户原话："引用来源怎么不能直接点击
 * 跳转到原文了"）。
 *
 * 之所以是"又"出现：以前召回以官方卡为主，条条都有 https；知识注入改精准之后，
 * 治理卡和用户上传文档在召回里占了大头，这一类从来就没有外网地址。
 *
 * 关键认识：**这些来源的"原文"并没有消失，它在系统里。** 所以不是去放行内部协议
 * 跳外网（那既不安全也无处可跳），而是把它们变成"在站内打开原文"。
 */

export type CitationSource =
  /** 内置/治理知识卡，按 id 取原文 */
  | { kind: "card"; id: string; raw: string }
  /** 用户上传的文档，按知识库内相对路径取原文 */
  | { kind: "upload"; path: string; raw: string };

/**
 * 认得出来就返回结构，认不出返回 null（调用方照旧当普通文本渲染）。
 *
 * 只认这两个前缀。将来 agent 加了新的内部协议，这里认不出时的表现是"退回纯文本"，
 * 和改动前一样 —— 不会因为多了个没见过的 scheme 就把整行渲染坏。
 */
export function parseCitationSource(url: string): CitationSource | null {
  const u = (url || "").trim();
  if (!u) return null;

  // ivyea://knowledge/<id>、ivyea://knowledge-governance/<id> …
  // 一律按"卡片 id"处理：id 就是最后一段之后的全部内容（id 本身可能含 `.`，如
  // `governance.source_quality`），前面那段是分类，取原文时用不上。
  const card = u.match(/^ivyea:\/\/[a-z0-9-]+\/(.+)$/i);
  if (card) {
    const id = card[1].trim();
    return id ? { kind: "card", id, raw: u } : null;
  }

  // ivyea-upload://<upload_id>/<相对路径>
  // 后端 /knowledge/file 按知识库内相对路径读，路径就是 `<upload_id>/<file>`。
  const up = u.match(/^ivyea-upload:\/\/(.+)$/i);
  if (up) {
    const path = up[1].trim();
    return path ? { kind: "upload", path, raw: u } : null;
  }
  return null;
}

/**
 * 给人看的短标签。
 *
 * 不显示原始 URI —— `ivyea-upload://up-20260713-162722-b893258e/knowledge-…md`
 * 对用户没有任何意义，它出现在屏幕上本身就是这个 bug 的一部分。
 */
export function citationSourceLabel(src: CitationSource): string {
  return src.kind === "upload" ? "查看原文（上传文档）" : "查看原文（知识库）";
}

/** 打开站内原文的事件名。渲染器是纯展示的，不该自己去发请求。 */
export const OPEN_SOURCE_EVENT = "ivyea-open-citation-source";

export function requestOpenSource(src: CitationSource): void {
  window.dispatchEvent(new CustomEvent(OPEN_SOURCE_EVENT, { detail: src }));
}
