import { api } from "./client";

// 自定义 MCP 数据源的增删改 / 探测 / 试跑。
// 内置三家（Sorftime / 卖家精灵 / SIF）不走这里，它们的 key 仍在 settings.ts。

export type AuthMode = "auto" | "none" | "query" | "header" | "bearer";

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
// 自动配置会连着真调十几个工具（每个都可能几秒），120 秒不够；
// 这里放宽到 5 分钟，进度由界面上的忙态负责交代。
const AUTOCONFIG = { timeout: 300000 };

export async function probeDataSource(source: CustomDataSource): Promise<ProbeResult> {
  const r = await api.post("/data-sources/probe", { source }, SLOW);
  return r.data;
}

export type AutoReport = {
  ok: boolean;
  error?: string;
  tools: number;
  surfaces: string[];
  auth?: string;
  capabilities: CapabilityOutcome[];
};

export type CapabilityOutcome = {
  id: string; label: string; ok: boolean;
  tool?: string; matched?: number; missing?: string[]; error?: string;
  // 没配上时说清是哪一种：这台服务器真没有这类工具 / 有但必填参数认不出 /
  // 有而且参数没问题、只是名字认不出 / 调用失败 / 调通了但返回里没有需要的字段
  reason?: "no_tool" | "unfillable" | "not_recognized" | "call_failed" | "no_data";
  // reason 是 not_recognized 或试过仍不成时，还能用的工具 —— 让用户自己指一个
  candidates?: { tool: string; description: string; args: Record<string, unknown> }[];
};

export async function remapCapability(
  source: CustomDataSource, capability: string, tool: string,
  sampleKeyword: string, sampleAsin: string, marketplace = "US",
): Promise<{ ok: boolean; spec: CapabilitySpec | null; matched?: number;
             missing?: string[]; error?: string | null }> {
  const r = await api.post("/data-sources/remap", {
    source, capability, tool,
    sample_keyword: sampleKeyword, sample_asin: sampleAsin, marketplace,
  }, SLOW);
  return r.data;
}

export async function autoconfigDataSource(
  source: CustomDataSource, sampleKeyword: string, sampleAsin: string, marketplace: string,
): Promise<{ source: CustomDataSource | null; report: AutoReport }> {
  const r = await api.post("/data-sources/autoconfig", {
    source, sample_keyword: sampleKeyword, sample_asin: sampleAsin, marketplace,
  }, AUTOCONFIG);
  return r.data;
}

export async function testDataSource(
  source: CustomDataSource, capability: string, query: string, marketplace: string,
): Promise<TestResult> {
  const r = await api.post("/data-sources/test",
    { source, capability, query, marketplace }, SLOW);
  return r.data;
}
