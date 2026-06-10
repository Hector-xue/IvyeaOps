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

export async function submitImage(prompt: string, size: string, n: number, imageUrls?: string[]): Promise<string> {
  const body: Record<string, unknown> = { prompt, size, n };
  if (imageUrls && imageUrls.length > 0) body.image_urls = imageUrls;
  const r = await fetch("/api/assistant/image", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const d = await r.json().catch(() => ({} as any));
    throw new Error(d.detail || `HTTP ${r.status}`);
  }
  return (await r.json()).task_id as string;
}

export interface ImageStatus {
  status: string;       // submitted | pending | running | completed | failed
  progress: number;
  images: string[];     // URLs (when completed)
  error: string | null;
}

export async function imageStatus(taskId: string): Promise<ImageStatus> {
  const r = await fetch(`/api/assistant/image/status?task_id=${encodeURIComponent(taskId)}`, { credentials: "include" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function assistantStatus(): Promise<{ deepseek: boolean; apimart: boolean }> {
  const r = await fetch("/api/assistant/status", { credentials: "include" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}
