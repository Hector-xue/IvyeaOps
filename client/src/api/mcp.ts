import { api } from "./client";

export interface McpToken {
  id: string;
  name: string;
  scopes: string;
  created_at: number;
  expires_at: number | null;
  last_used_at: number | null;
  last_used_ip: string;
  revoked: number;
}

export interface IssuedToken extends McpToken {
  /** 明文令牌。**只在生成的这一次返回里出现**，之后服务端只留哈希。 */
  token: string;
}

export interface McpClientConfig {
  endpoint: string;
  claude_desktop: unknown;
  cursor: unknown;
  note: string;
}

export async function listMcpTokens(): Promise<{ tokens: McpToken[]; scopes: string[] }> {
  return (await api.get("/mcp-admin/tokens")).data;
}

export async function issueMcpToken(
  name: string, scopes: string[], ttlDays: number,
): Promise<IssuedToken> {
  return (await api.post("/mcp-admin/tokens", { name, scopes, ttl_days: ttlDays })).data;
}

export async function revokeMcpToken(id: string): Promise<void> {
  await api.delete(`/mcp-admin/tokens/${id}`);
}

export async function getMcpClientConfig(token = ""): Promise<McpClientConfig> {
  return (await api.get("/mcp-admin/config", { params: { token } })).data;
}
