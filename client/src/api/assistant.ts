export interface ChatMsg { role: "system" | "user" | "assistant"; content: string }

export type ChatEvent =
  | { type: "token"; text: string; provider: string }
  | { type: "done"; provider: string }
  | { type: "error"; detail: string };

export function streamChat(
  messages: ChatMsg[],
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return fetch("/api/assistant/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ messages }),
    signal,
  }).then(async (resp) => {
    if (!resp.ok) {
      const t = await resp.text().catch(() => "");
      throw new Error(`HTTP ${resp.status}: ${t}`);
    }
    const reader = resp.body!.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop() ?? "";
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data:")) continue;
        try { onEvent(JSON.parse(line.slice(5).trim()) as ChatEvent); } catch { /* ignore */ }
      }
    }
  });
}

/**
 * 把一张附图换成 `ivyea-ref://` 短句柄。
 *
 * 任务台在发送前调它：图片本体留在服务器上，只有句柄跟着这一轮进模型，agent 拿
 * 句柄填 image_generate 的 image_urls 就是图生图。data URL 有几百 KB，让它穿过
 * 工具调用参数是不可能的。
 */
export async function imageRef(dataUrl: string): Promise<{ ref: string; bytes: number }> {
  const r = await fetch("/api/assistant/image/ref", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ data_url: dataUrl }),
  });
  if (!r.ok) {
    const d = await r.json().catch(() => ({} as any));
    throw new Error(d.detail || `HTTP ${r.status}`);
  }
  return r.json();
}

/**
 * `ivyea-ref://<id>` → 能直接放进 `<img src>` 的地址。
 *
 * 历史会话是从 agent 的存档里恢复的，而存档里只有文字（图片从来不进模型），
 * 用户发过的那张图只剩这串句柄 —— 会话记录里的缩略图靠它取回原图。
 * 不是句柄（http 地址、data URL）就原样返回。
 */
export function imageRefUrl(ref: string): string {
  const id = String(ref || "").trim();
  if (!id.startsWith("ivyea-ref://")) return id;
  return "/api/assistant/image/ref/" + encodeURIComponent(id.slice("ivyea-ref://".length));
}
