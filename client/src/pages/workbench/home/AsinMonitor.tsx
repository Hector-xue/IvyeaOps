import { useEffect, useRef, useState } from "react";
import {
  listWatch, addWatch, deleteWatch, pulseAsin, fetchWatchSnapshots,
  type WatchItem, type WatchKind, type AsinPulse,
} from "../../../api/home";
import AsinCard, { type AsinState } from "./AsinCard";
import type { DataSourceId } from "../../../lib/dataSource";
import { runPool } from "../../../lib/pool";
import { useToast } from "../../../components/toast";

const ASIN_RE = /^[A-Z0-9]{10}$/;

function metricsToPulse(asin: string, marketplace: string, m: Record<string, any>): AsinPulse {
  return {
    asin, marketplace, error: null,
    title: m.title ?? null, brand: m.brand ?? null, image: m.image ?? null,
    price: m.price ?? null, bsr: m.bsr ?? null, bsr_category: m.bsr_category ?? null,
    sub_rank: m.sub_rank ?? null, sub_category: m.sub_category ?? null,
    est_sales: m.est_sales ?? null, rating: m.rating ?? null, review_count: m.review_count ?? null,
    variations: m.variations ?? null, coupon: m.coupon ?? null, deal: m.deal ?? null,
    inventory: m.inventory ?? null,
  };
}

export default function AsinMonitor({ kind, marketplace, dataSource, onChanged }: {
  kind: WatchKind;
  marketplace: string;
  dataSource: DataSourceId;
  onChanged?: () => void;
}) {
  const [items, setItems] = useState<WatchItem[]>([]);
  const [states, setStates] = useState<Record<string, AsinState>>({});
  const [input, setInput] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const notify = useToast();

  const sourceName = dataSource === "sellersprite" ? "卖家精灵" : "Sorftime";

  // Cache-first load: source-isolated snapshots, no provider call.
  const loadCache = async () => {
    try {
      const [all, snaps] = await Promise.all([listWatch(dataSource), fetchWatchSnapshots(dataSource)]);
      const mine = all.filter(w => w.kind === kind && w.marketplace === marketplace);
      setItems(mine);
      const snapByAsin = new Map(
        snaps.filter(s => s.kind === kind && s.marketplace === marketplace).map(s => [s.asin, s]),
      );
      const next: Record<string, AsinState> = {};
      for (const it of mine) {
        const sn = snapByAsin.get(it.asin);
        if (sn && sn.ts && sn.metrics && Object.keys(sn.metrics).length > 0) {
          next[it.asin] = { kind: "ok", pulse: metricsToPulse(it.asin, marketplace, sn.metrics), delta: {}, ts: sn.ts, cached: true };
        } else {
          next[it.asin] = { kind: "idle" };
        }
      }
      setStates(next);
    } catch { /* ignore */ }
  };

  useEffect(() => { loadCache(); /* eslint-disable-next-line */ }, [kind, marketplace, dataSource]);

  // Live pulse one ASIN (spends quota) — explicit user action only.
  const fetchOne = async (asin: string) => {
    setStates(p => ({ ...p, [asin]: { kind: "loading" } }));
    try {
      const res = await pulseAsin(asin, marketplace, dataSource);
      setStates(p => ({ ...p, [asin]: { kind: "ok", pulse: res.current, delta: res.delta, ts: Date.now(), cached: false } }));
    } catch (e: any) {
      setStates(p => ({ ...p, [asin]: { kind: "err", msg: e?.message || "请求失败" } }));
    }
  };

  const refreshAll = async () => {
    if (items.length === 0) return;
    setRefreshing(true);
    await runPool(items, it => fetchOne(it.asin));
    setRefreshing(false);
    onChanged?.();
  };

  const handleAdd = async () => {
    const asin = input.trim().toUpperCase();
    if (!ASIN_RE.test(asin)) return;
    if (items.some(it => it.asin === asin)) { setInput(""); return; }
    try {
      const created = await addWatch({ asin, marketplace, data_source: dataSource, kind });
      setItems(p => [...p, { id: created.id, asin, marketplace, data_source: dataSource, kind, label: "", ts: Date.now() }]);
      setInput("");
      inputRef.current?.focus();
      fetchOne(asin); // one live fetch for the newly added ASIN
    } catch (e: any) {
      notify("error", `添加 ${asin} 失败：${e?.message || "请求失败"}`);
    }
  };

  const handleRemove = async (item: WatchItem) => {
    await deleteWatch(item.id).catch(() => notify("warn", `${item.asin} 服务端删除失败，刷新后可能回来`));
    setItems(p => p.filter(i => i.id !== item.id));
    setStates(p => { const n = { ...p }; delete n[item.asin]; return n; });
  };

  const placeholder = kind === "own" ? "添加自有 ASIN + Enter" : "添加竞品 ASIN + Enter";
  const emptyTitle = kind === "own" ? "自有 ASIN 健康监控" : "竞品 ASIN 监控";
  const emptySub = kind === "own"
    ? "添加你自己的 listing，跟踪价格 / BSR / 评分 / 评论增速，与竞品并排观察"
    : "添加目标竞品 ASIN，自动拉取价格 / BSR / 估算月销 / 评分 / 评论 / 变体，并显示自上次的变动";

  return (
    <div className="pulse-page">
      <div className="pulse-header">
        <span className="pulse-header-title">
          <span style={{ color: "var(--acc)" }}>{kind === "own" ? "★" : "⊞"}</span>
          {kind === "own" ? "自有 ASIN" : "竞品监控"}
        </span>
        <div className="pulse-input-wrap">
          <input
            ref={inputRef}
            className="pulse-input"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === "Enter" && handleAdd()}
            placeholder={placeholder}
          />
          <button className="tbtn" onClick={handleAdd} disabled={!ASIN_RE.test(input.trim().toUpperCase())}>+ 添加</button>
        </div>
        <button className="tbtn tbtn-acc"
          onClick={refreshAll}
          disabled={refreshing || items.length === 0}
          title={`实时刷新全部（每个 ASIN 消耗 ${sourceName} 调用）`}>
          {refreshing ? <><span className="spin" style={{ marginRight: 6 }} />刷新中…</> : "↻ 全部刷新"}
        </button>
      </div>

      {items.length === 0 ? (
        <div className="pulse-onboard">
          <div className="pulse-onboard-icon">{kind === "own" ? "★" : "⊞"}</div>
          <div className="pulse-onboard-title">{emptyTitle}</div>
          <div className="pulse-onboard-sub">{emptySub}</div>
          <div className="pulse-onboard-sub" style={{ marginTop: 8, fontSize: "var(--fs-11)", color: "var(--t3)" }}>
            提示：打开页面显示的是缓存数据（不耗配额）；点「刷新」才实时拉取
          </div>
        </div>
      ) : (
        <div className="asin-grid">
          {items.map(it => (
            <AsinCard
              key={it.id}
              asin={it.asin}
              label={it.label}
              marketplace={marketplace}
              state={states[it.asin] ?? { kind: "idle" }}
              onRemove={() => handleRemove(it)}
              onRefresh={() => fetchOne(it.asin)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
