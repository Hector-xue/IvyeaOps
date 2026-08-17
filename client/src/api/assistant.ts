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
