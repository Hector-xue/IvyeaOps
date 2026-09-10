// Shared market-data source selection, used by 首页 / 市场调研 / 打法推荐.
// Availability is surface-specific so unsupported providers can never fall
// through to a different backend silently.
//
// 内置三家写死在这里（它们各有专用的后端服务模块）；用户自建的 MCP 数据源
// 从 /api/data-sources 拉，id 一律带 `custom:` 前缀，与内置 id 永不撞名。
import { useEffect, useSyncExternalStore } from "react";

export type BuiltinDataSourceId = "sorftime" | "sif" | "sellersprite";
export type CustomDataSourceId = `custom:${string}`;
export type DataSourceId = BuiltinDataSourceId | CustomDataSourceId;
export type DataSourceSurface = "market" | "playbook" | "home";

export type DataSourceMeta = {
  id: DataSourceId;
  name: string;
  ready: boolean;
  note?: string;
  surfaces: DataSourceSurface[];
  custom?: boolean;
};

export const DATA_SOURCES: DataSourceMeta[] = [
  { id: "sorftime", name: "Sorftime", ready: true, surfaces: ["market", "playbook", "home"] },
  { id: "sellersprite", name: "卖家精灵", ready: true, surfaces: ["market", "playbook", "home"] },
  { id: "sif", name: "SIF", ready: false, surfaces: [], note: "即将支持" },
];

const KEY = "ivyea-ops-data-source";
const CACHE_KEY = "ivyea-ops-custom-data-sources";

// ── 自定义源的本地快照 ──────────────────────────────────────────────────────
//
// 缓存到 localStorage 的原因：刷新后第一帧就要能把 `custom:myerp` 显示成
// 「我的源」。不缓存的话，接口回来之前下拉框和标题会先闪一下裸 id，看起来像
// 出了错。缓存只用于显示，可用性仍以后端返回为准。

let customs: DataSourceMeta[] = readCache();
let loaded = false;
const listeners = new Set<() => void>();

function readCache(): DataSourceMeta[] {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((s) => s && typeof s.id === "string") : [];
  } catch {
    return [];
  }
}

function writeCache(list: DataSourceMeta[]): void {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(list));
  } catch {
    /* 隐私模式下写不了，纯显示优化，忽略 */
  }
}

function emit(): void {
  for (const fn of listeners) fn();
}

/** 后端返回的一条自定义源配置 → 选择器要的元数据。 */
export function toMeta(source: {
  id: string;
  name?: string;
  enabled?: boolean;
  surfaces?: string[];
  note?: string;
}): DataSourceMeta {
  const surfaces = (source.surfaces || []).filter(
    (s): s is DataSourceSurface => s === "market" || s === "playbook" || s === "home",
  );
  return {
    id: `custom:${source.id}` as CustomDataSourceId,
    name: source.name || source.id,
    ready: source.enabled !== false && surfaces.length > 0,
    surfaces,
    note: source.enabled === false ? "已停用" : surfaces.length ? source.note : "未启用任何板块",
    custom: true,
  };
}

export function setCustomDataSources(list: DataSourceMeta[]): void {
  customs = list;
  loaded = true;
  writeCache(list);
  emit();
}

let inflight: Promise<DataSourceMeta[]> | null = null;

/** 拉一次自定义源清单。失败时保留上一次的快照，不要把已配好的源从下拉里抹掉。 */
export function loadCustomDataSources(force = false): Promise<DataSourceMeta[]> {
  if (loaded && !force) return Promise.resolve(customs);
  // 同步的在途去重：StrictMode 下同一次挂载会渲染两遍，`loaded` 要等 fetch
  // 回来才置位，光靠它挡不住第二发请求。
  if (inflight && !force) return inflight;
  inflight = (async () => {
    try {
      const r = await fetch("/api/data-sources", { credentials: "include" });
      if (!r.ok) throw new Error(String(r.status));
      const body = await r.json();
      setCustomDataSources((body.sources || []).map(toMeta));
    } catch {
      loaded = true; // 拉失败也别每次渲染都重试，交给显式 force 刷新
    } finally {
      inflight = null;
    }
    return customs;
  })();
  return inflight;
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

let snapshot: DataSourceMeta[] = [...DATA_SOURCES, ...customs];
let snapshotFor = customs;

function getSnapshot(): DataSourceMeta[] {
  // useSyncExternalStore 要求快照引用稳定，每次新建数组会导致无限重渲染。
  if (snapshotFor !== customs) {
    snapshotFor = customs;
    snapshot = [...DATA_SOURCES, ...customs];
  }
  return snapshot;
}

export function allDataSources(): DataSourceMeta[] {
  return getSnapshot();
}

/** 选择器用：内置 + 自定义，且自定义源清单变化时会触发重渲染。 */
export function useDataSources(): DataSourceMeta[] {
  const list = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  useEffect(() => {
    void loadCustomDataSources();
  }, []);
  return list;
}

export function getDataSource(): DataSourceId {
  const v = (typeof localStorage !== "undefined" ? localStorage.getItem(KEY) : null) as DataSourceId | null;
  // 自定义源被删掉后，存在 localStorage 里的旧选择会变成一个不存在的 id ——
  // 落回 sorftime，而不是让页面一直请求一个后端会 400 的数据源。
  return v && allDataSources().some((s) => s.id === v) ? v : "sorftime";
}

export function setDataSource(id: DataSourceId): void {
  localStorage.setItem(KEY, id);
}

export function dataSourceMeta(id: DataSourceId, surface?: DataSourceSurface): DataSourceMeta {
  const source =
    allDataSources().find((s) => s.id === id) ??
    // 清单还没加载完时，至少把名字显示成 id 后半段而不是整串 `custom:xxx`
    (typeof id === "string" && id.startsWith("custom:")
      ? { id, name: id.slice(7), ready: false, surfaces: [] as DataSourceSurface[], custom: true }
      : DATA_SOURCES[0]);
  if (!surface) return source;
  return {
    ...source,
    ready: source.ready && source.surfaces.includes(surface),
    note: source.note,
  };
}
