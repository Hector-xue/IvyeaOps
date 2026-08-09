import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import { Btn, LxTable, LxTableSkeleton, fmtTs, type LxCol } from "./lingxingUi";
import { errText } from "../../lib/errText";

const STATUS_COLOR: Record<string, string> = {
  ok: "var(--acc)", denied: "var(--t3)", blocked: "var(--red)", error: "var(--red)",
};

export default function LingXingAudit() {
  const [rows, setRows] = useState<any[]>([]);
  const [filter, setFilter] = useState<string>("");
  const [loaded, setLoaded] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => { void load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, []);
  async function load() {
    try { setRows((await api.get("/lingxing/audit?limit=300")).data.rows || []); setMsg(""); }
    catch (e: any) { setMsg(errText(e, "加载失败")); }
    finally { setLoaded(true); }
  }

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const r of rows) c[r.status] = (c[r.status] || 0) + 1;
    return c;
  }, [rows]);
  const shown = filter ? rows.filter((r) => r.status === filter) : rows;

  const cols: LxCol[] = [
    { key: "ts", label: "时间", render: (r) => fmtTs(r.ts), sortVal: (r) => r.ts },
    { key: "caller", label: "调用方" },
    { key: "tool", label: "工具/路由", maxWidth: 260 },
    { key: "kind", label: "类型" },
    { key: "status", label: "状态", render: (r) => <span style={{ color: STATUS_COLOR[r.status] || "var(--t2)", fontWeight: 600 }}>{r.status}</span> },
    { key: "latency_ms", label: "耗时", num: true, render: (r) => (r.latency_ms ? `${r.latency_ms}ms` : "—") },
    { key: "detail", label: "详情", maxWidth: 320, render: (r) => <span style={{ color: "var(--t3)", whiteSpace: "normal" }}>{r.detail || "—"}</span> },
  ];

  return (
    <div>
      <div className="card" style={{ padding: 12, marginBottom: 10, display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ fontSize: 11, color: "var(--t3)" }}>全部调用审计（读/写/探针）· 每 8 秒自动刷新</span>
        {["ok", "denied", "blocked", "error"].map((s) => (
          <span key={s} onClick={() => setFilter(filter === s ? "" : s)} style={{
            fontSize: 11, cursor: "pointer", padding: "2px 8px", borderRadius: 10,
            background: filter === s ? "var(--bg2)" : "transparent", border: "1px solid var(--b)",
            color: STATUS_COLOR[s] || "var(--t2)",
          }}>{s} {counts[s] || 0}</span>
        ))}
        <span style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
          {filter && <span style={{ fontSize: 10, color: "var(--t3)" }}>筛选: {filter}（点击取消）</span>}
          <Btn onClick={load}>刷新</Btn>
        </span>
      </div>

      <div className="card" style={{ padding: 0 }}>
        {!loaded ? <LxTableSkeleton lines={8} />
          : <LxTable rows={shown} cols={cols} empty={msg || "暂无审计记录"} initSort={{ key: "ts", dir: "desc" }} />}
      </div>
    </div>
  );
}
