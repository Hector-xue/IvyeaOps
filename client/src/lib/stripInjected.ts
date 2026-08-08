/**
 * 剥掉每轮注入给模型的上下文（与后端 console_sessions.clean_preview 同一组标记）。
 *
 * 会话正文里除了用户真正打的字，还夹着技能说明书、知识检索结果、任务范围锁定这些
 * 运行时上下文 —— 它们是给模型看的，直接摆进气泡就是一大坨噪音。
 *
 * 从 Console.tsx 提到这里：AI 问答收编进同一个会话库之后，两个板块恢复历史会话时
 * 都得剥同一组标记，各写一份迟早会漂。
 */
const INJECTION_MARKERS = [
  "\n\n[Ivyea Skill：",
  "\n\n[Ivyea 本地知识检索",
  "\n\n[Ivyea 内置亚马逊知识库",
  "\n\n[任务范围锁定",
  "\n\n[工程上下文]",
];

export function stripInjected(text: string): string {
  let out = text;
  for (const marker of INJECTION_MARKERS) {
    const i = out.indexOf(marker);
    if (i >= 0) out = out.slice(0, i);
  }
  // 收尾是被截断的半截标记（服务端把消息砍短过）→ 一并切掉
  for (const marker of INJECTION_MARKERS) {
    for (let size = marker.length; size > 2; size -= 1) {
      if (out.endsWith(marker.slice(0, size))) { out = out.slice(0, -size); break; }
    }
  }
  return out.trim();
}
