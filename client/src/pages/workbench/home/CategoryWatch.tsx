import { useEffect, useRef, useState } from "react";
import { fetchCategory, fetchCategoryCached, type CategoryResult } from "../../../api/home";
import { amazonDp } from "../../../lib/marketplaces";
import type { DataSourceId } from "../../../lib/dataSource";

const STORAGE_CAT = "ivyea-ops-home-category-q";

function fmtVol(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(1) + "M";
  if (v >= 1_000) return (v / 1_000).toFixed(1) + "K";
  return String(Math.round(v));
}
const fmtPrice = (v: number | null | undefined) => (v == null ? "—" : "$" + v.toFixed(2));

type Status = "idle" | "loading" | "ok" | "err";

type Mode = "category" | "keyword";

export default function CategoryWatch({ marketplace, dataSource }: { marketplace: string; dataSource: DataSourceId }) {
  const [mode, setMode] = useState<Mode>(() => (localStorage.getItem(STORAGE_CAT + "-mode") as Mode) || "category");
  const [input, setInput] = useState(() => localStorage.getItem(STORAGE_CAT) || "");
  const [status, setStatus] = useState<Status>("idle");
  const [result, setResult] = useState<CategoryResult | null>(null);
  const [cachedTs, setCachedTs] = useState<number | null>(null);
  const [errMsg, setErrMsg] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const sourceName = dataSource === "sellersprite" ? "卖家精灵" : "Sorftime";

  // Live analysis (spends the selected provider quota) — explicit only.
  const run = async (q: string) => {
    const query = q.trim();
    if (!query) return;
    setStatus("loading");
    setErrMsg("");
    try {
      const res = await fetchCategory(query, marketplace, mode, dataSource);
      setResult(res);
      setCachedTs(Date.now());
      setStatus(res.error ? "err" : "ok");
      if (res.error) setErrMsg(res.error);
      localStorage.setItem(STORAGE_CAT, query);
    } catch (e: any) {
      setStatus("err");
      setErrMsg(e?.message || "请求失败");
    }
  };

  // Cache-first: cache is isolated by provider and costs no provider quota.
  const loadCache = async (q: string) => {
    const query = q.trim();
    if (!query) { setStatus("idle"); setResult(null); return; }
    try {
      const { cached, ts } = await fetchCategoryCached(query, marketplace, mode, dataSource);
      if (cached) {
        setResult(cached);
        setCachedTs(ts);
        setStatus("ok");
      } else {
        setStatus("idle");
        setResult(null);
      }
    } catch { /* ignore */ }
  };

  useEffect(() => { localStorage.setItem(STORAGE_CAT + "-mode", mode); }, [mode]);
  useEffect(() => { loadCache(input); /* eslint-disable-next-line */ }, [marketplace, mode, dataSource]);

  const maxBand = result ? Math.max(1, ...result.bands.map(b => b.count)) : 1;
  const changes = result?.changes;
  const hasChanges = !!changes && (changes.new_entrants.length > 0 || changes.movers.length > 0);

  return (
    <div className="pulse-page">
      <div className="pulse-header">
        <div className="market-mode-toggle">
          {(["category", "keyword"] as Mode[]).map(m => (
            <button key={m} className={"market-mode-btn" + (mode === m ? " active" : "")}
              disabled={status === "loading"} onClick={() => setMode(m)}>
              {m === "category" ? "类目榜" : "搜索排行"}
            </button>
          ))}
        </div>
        <div className="pulse-input-wrap" style={{ flex: 1 }}>
          <input
            ref={inputRef}
            className="pulse-input"
            style={{ width: "auto", flex: 1 }}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === "Enter" && run(input)}
            placeholder={mode === "category"
              ? "ASIN 反查类目(推荐) / nodeId / 类目词 + Enter"
              : "关键词(看真实搜索排行) + Enter"}
          />
          <button className="tbtn tbtn-acc" onClick={() => run(input)} disabled={status === "loading" || !input.trim()}
            title={`实时分析（消耗 ${sourceName} 调用）`}>
            {status === "loading"
              ? <><span className="spin" style={{ marginRight: 6 }} />分析中…</>
              : (result && status === "ok" ? "刷新分析" : "分析")}
          </button>
        </div>
      </div>

      {status === "idle" && (
        <div className="pulse-onboard">
          <div className="pulse-onboard-title">{mode === "category" ? "类目榜（真实类目排行）" : "关键词搜索排行"}</div>
          <div className="pulse-onboard-sub">
            {mode === "category"
              ? `推荐用该品类的真实 ASIN 反查（自动定位它所属类目）或粘贴 nodeId；类目词匹配 ${sourceName} 可能有偏差，会显示解析到的类目名供你核对。`
              : "显示该关键词在亚马逊的真实搜索结果排行（所见即所得，但含广告位与周边配件，天然偏杂）。"}
          </div>
        </div>
      )}

      {status === "err" && <div className="market-error">{errMsg || "分析失败"}</div>}

      {status === "ok" && result && (
        <div className="cat-dash">
          {/* Resolved-category banner so the user can verify it's the right category */}
          <div className="cat-resolved">
            {result.mode === "category" ? (
              <span>
                解析到类目：<b style={{ color: "var(--t)" }}>{result.category_name || "(未知)"}</b>
                {result.node_id && <span className="cat-hint"> · node {result.node_id}</span>}
                <span className="cat-hint"> · {result.source === "asin" ? "ASIN反查" : result.source === "nodeId" ? "nodeId" : "类目词匹配"}</span>
                {result.source === "name" && (
                  <div className="cat-hint" style={{ color: "var(--amber)" }}>
                    ⚠ 类目词匹配 {sourceName} 可能有偏差——若上面类目名不对，请用该品类真实 ASIN 反查或粘贴正确 nodeId
                  </div>
                )}
              </span>
            ) : (
              <span className="cat-hint">数据源：关键词「{result.query}」真实搜索排行（含广告位/周边配件，天然偏杂）</span>
            )}
            {cachedTs && (
              <span className="cat-hint" style={{ marginLeft: "auto" }}>
                数据 {new Date(cachedTs).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })} · 「刷新分析」更新
              </span>
            )}
          </div>
          {/* Summary */}
          <div className="cat-summary">
            <div className="cat-sum-item"><div className="cat-sum-val">{result.summary?.count ?? "—"}</div><div className="cat-sum-label">在榜产品</div></div>
            <div className="cat-sum-item"><div className="cat-sum-val">{fmtPrice(result.summary?.avg_price)}</div><div className="cat-sum-label">平均价</div></div>
            <div className="cat-sum-item"><div className="cat-sum-val">{fmtVol(result.summary?.total_sales)}</div><div className="cat-sum-label">{result.summary_kind === "keyword_purchases" ? "关键词月购买" : "合计月销"}</div></div>
            <div className="cat-sum-item"><div className="cat-sum-val cat-node">{result.node_id}</div><div className="cat-sum-label">节点</div></div>
          </div>

          {/* Price bands */}
          {result.bands.length > 0 && (
            <div className="cat-block">
              <div className="cat-block-title">价格带分布</div>
              <div className="cat-bands">
                {result.bands.map((b, i) => (
                  <div key={i} className="cat-band">
                    <span className="cat-band-label">{b.label}</span>
                    <div className="cat-band-bar"><div className="cat-band-fill" style={{ width: `${(b.count / maxBand) * 100}%` }} /></div>
                    <span className="cat-band-count">{b.count}款 · {fmtVol(b.sales)}销</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Movers / new entrants */}
          <div className="cat-block">
            <div className="cat-block-title">榜单异动 {!changes?.has_baseline && <span className="cat-hint">（首次分析，再次查看后显示变化）</span>}</div>
            {hasChanges ? (
              <div className="cat-changes">
                {changes!.new_entrants.map(a => (
                  <span key={"n" + a} className="cat-chip new" title="新上榜">🆕 {a}</span>
                ))}
                {changes!.movers.map(m => {
                  const climbed = m.diff < 0; // lower rank number = climbed
                  return (
                    <span key={"m" + m.asin} className={"cat-chip " + (climbed ? "up" : "down")} title={`#${m.from}→#${m.to}`}>
                      {climbed ? "▲" : "▼"} {m.asin} #{m.from}→#{m.to}
                    </span>
                  );
                })}
              </div>
            ) : changes?.has_baseline ? (
              <div className="cat-hint">榜单稳定，无显著排名变化</div>
            ) : null}
          </div>

          {/* TOP list */}
          <div className="cat-block">
            <div className="cat-block-title">TOP 榜（前 {result.top.length}）</div>
            <div className="cat-table-wrap">
              <table className="cat-table">
                <thead><tr><th>#</th><th>产品</th><th>价格</th><th>BSR</th><th>月销</th><th>评分</th><th>评论</th></tr></thead>
                <tbody>
                  {result.top.map(p => (
                    <tr key={p.asin || p.rank}>
                      <td>{p.rank}</td>
                      <td className="cat-td-prod">
                        <a href={amazonDp(p.asin, marketplace)} target="_blank" rel="noreferrer" className="asin-link">{p.asin || "—"}</a>
                        {p.title && <span className="cat-td-title" title={p.title}>{p.title}</span>}
                      </td>
                      <td>{fmtPrice(p.price)}</td>
                      <td>{p.bsr != null ? "#" + fmtVol(p.bsr) : "—"}</td>
                      <td>{fmtVol(p.est_sales)}</td>
                      <td>{p.rating != null ? p.rating.toFixed(1) : "—"}</td>
                      <td>{fmtVol(p.review_count)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
