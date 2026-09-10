import { api } from "./client";

// 自定义 MCP 数据源的增删改 / 探测 / 试跑。
// 内置三家（Sorftime / 卖家精灵 / SIF）不走这里，它们的 key 仍在 settings.ts。

export type AuthMode = "none" | "query" | "header" | "bearer";

export type CapabilitySpec = {
  tool?: string;
  args?: Record<string, unknown>;
  record?: string;
  rows?: string;
  fields?: Record<string, string>;
  row_fields?: Record<string, string>;
  summary_fields?: Record<string, string>;
  steps?: { label: string; tool: string; args?: Record<string, unknown> }[];
};

export type CustomDataSource = {
  id: string;
  name: string;
  enabled: boolean;
  transport: "http" | "sse";
  url: string;
  auth: { mode: AuthMode; name: string; value: string; value_set?: boolean };
  headers: Record<string, string>;
  headers_set?: string[];
  handshake: boolean;
  envelope: string;
  timeout: number;
  surfaces: string[];
  capabilities: Record<string, CapabilitySpec>;
  note: string;
};

export type ProbeResult = {
  ok: boolean;
  error?: string;
  count: number;
  note?: string;
  tools: { name: string; description: string; params: string[]; required: string[] }[];
};

export type TestResult = {
  ok: boolean;
  errors: string[];
  filled_fields?: number;
  result: unknown;
};

export async function listDataSources(): Promise<{
  sources: CustomDataSource[];
  capabilities: string[];
  surfaces: string[];
  surface_requires: Record<string, string[]>;
}> {
  const r = await api.get("/data-sources");
  return r.data;
}

export async function saveDataSource(source: CustomDataSource): Promise<CustomDataSource> {
  const r = await api.post("/data-sources", { source });
  return r.data.source;
}

export async function deleteDataSource(id: string): Promise<void> {
  await api.delete(`/data-sources/${encodeURIComponent(id)}`);
}

// 探测和试跑都要真打一次外部服务器，30 秒的全局默认不够用。
const SLOW = { timeout: 120000 };

export async function probeDataSource(source: CustomDataSource): Promise<ProbeResult> {
  const r = await api.post("/data-sources/probe", { source }, SLOW);
  return r.data;
}

export async function testDataSource(
  source: CustomDataSource, capability: string, query: string, marketplace: string,
): Promise<TestResult> {
  const r = await api.post("/data-sources/test",
    { source, capability, query, marketplace }, SLOW);
  return r.data;
}
