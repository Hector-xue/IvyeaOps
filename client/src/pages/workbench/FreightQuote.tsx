import { useCallback, useEffect, useRef, useState } from "react";

interface QuoteRecord {
  company: string;
  channel: string;
  warehouse_code: string;
  warehouse_full: string;
  origin: string;
  tier: string;
  unit: string;
  tax_type: string;
  price: string;
  price_value: number | null;
  transit: string;
  note: string;
  effective_date: string;
  source_file: string;
  product_code?: string;
  sheet?: string;
}

interface IndexStatus {
  built_at: string;
  record_count: number;
  warehouse_count: number;
  companies: string[];
}

interface FileEntry {
  name: string;
  size: number;
  company: string;
  market?: string;
  records: number;
  disabled: boolean;
  error?: string;
}

type Tab = "search" | "admin";

export default function FreightQuote() {
  const [tab, setTab] = useState<Tab>("search");
  const [status, setStatus] = useState<IndexStatus | null>(null);
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [warehouseCode, setWarehouseCode] = useState("");
  const [weightKg, setWeightKg] = useState("");
  const [companyFilter, setCompanyFilter] = useState("");
  const [results, setResults] = useState<QuoteRecord[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState("");
  const [rebuilding, setRebuilding] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [uploadCompany, setUploadCompany] = useState("");
  const [uploadMarket, setUploadMarket] = useState("");

  const loadStatus = useCallback(async () => {
    try {
      const r = await fetch("/api/freight/status", { credentials: "include" });
      if (r.ok) setStatus(await r.json());
    } catch { /* ignore */ }
  }, []);

  const loadFiles = useCallback(async () => {
    try {
      const r = await fetch("/api/freight/files", { credentials: "include" });
      if (r.ok) {
        const d = await r.json();
        setFiles(d.files || []);
      }
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  useEffect(() => {
    if (tab === "admin") loadFiles();
  }, [tab, loadFiles]);

  const doSearch = async () => {
    const code = warehouseCode.trim().toUpperCase();
    if (!code) return;
    setSearching(true);
    setSearchError("");
    setResults([]);
    try {
      const r = await fetch("/api/freight/search", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          warehouse_code: code,
          weight_kg: weightKg ? parseFloat(weightKg) : null,
          company: companyFilter || null,
        }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "搜索失败");
      setResults(d.records || []);
      if ((d.records || []).length === 0) setSearchError("未找到该仓库报价。请确认仓库代码或重新上传报价文件。");
    } catch (e: any) {
      setSearchError(e.message || "搜索失败");
    } finally {
      setSearching(false);
    }
  };

  const doUpload = async (fileList: FileList | File[]) => {
    const arr = Array.from(fileList).filter(f => /\.(xls|xlsx)$/i.test(f.name));
    if (!arr.length) { setUploadMsg("请选择 .xls 或 .xlsx 文件"); return; }
    setUploading(true);
    setUploadMsg("");
    const fd = new FormData();
    arr.forEach(f => fd.append("files", f));
    fd.append("company", uploadCompany);
    fd.append("market", uploadMarket);
    try {
      const r = await fetch("/api/freight/upload", { method: "POST", credentials: "include", body: fd });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "上传失败");
      setUploadMsg(`已上传 ${d.saved?.length || 0} 个文件，索引记录 ${d.record_count || 0} 条（${d.warehouse_count || 0} 个仓库）`);
      loadStatus();
      loadFiles();
    } catch (e: any) {
      setUploadMsg(e.message || "上传失败");
    } finally {
      setUploading(false);
    }
  };

  const doRebuild = async () => {
    setRebuilding(true);
    try {
      const r = await fetch("/api/freight/rebuild", { method: "POST", credentials: "include" });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "重建失败");
      loadStatus();
      loadFiles();
    } catch (e: any) {
      alert(e.message);
    } finally {
      setRebuilding(false);
    }
  };

  const doToggle = async (name: string) => {
    await fetch(`/api/freight/files/${encodeURIComponent(name)}/toggle`, { method: "POST", credentials: "include" });
    loadFiles();
    loadStatus();
  };

  const doDelete = async (name: string) => {
    if (!confirm(`确认删除 ${name}？`)) return;
    await fetch(`/api/freight/files/${encodeURIComponent(name)}`, { method: "DELETE", credentials: "include" });
    loadFiles();
    loadStatus();
  };

  return (
    <div style={{ padding: "16px 20px", height: "100%", overflow: "auto", boxSizing: "border-box" }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
        <span style={{ fontSize: 18, fontWeight: 700, color: "var(--t)", fontFamily: "var(--font)" }}>
          FBA 头程报价比价
        </span>
        {status && (
          <span style={{ fontSize: "var(--fs-11)", color: "var(--t3)", fontFamily: "var(--font)" }}>
            {status.record_count} 条 · {status.warehouse_count} 个仓库
            {status.built_at && ` · 更新于 ${status.built_at}`}
          </span>
        )}
        <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          <button
            className="tbtn"
            style={{ background: tab === "search" ? "var(--acc)" : undefined, color: tab === "search" ? "#000" : undefined }}
            onClick={() => setTab("search")}
          >查价</button>
          <button
            className="tbtn"
            style={{ background: tab === "admin" ? "var(--acc)" : undefined, color: tab === "admin" ? "#000" : undefined }}
            onClick={() => setTab("admin")}
          >管理报价</button>
        </div>
      </div>

      {/* ── Search Tab ── */}
      {tab === "search" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {/* Input row */}
          <div style={{
            background: "var(--bg1)", border: "1px solid var(--b)", borderRadius: 8, padding: "16px",
          }}>
            <div className="fq-form">
              <div className="fq-field">
                <label>FBA 仓库代码</label>
                <input
                  className="fq-input"
                  style={{ textTransform: "uppercase", letterSpacing: "0.06em" }}
                  placeholder="如 ONT8"
                  value={warehouseCode}
                  onChange={e => setWarehouseCode(e.target.value)}
                  onKeyDown={e => e.key === "Enter" && doSearch()}
                />
              </div>
              <div className="fq-field">
                <label>重量 (KG)<span style={{ color: "var(--t3)", marginLeft: 4 }}>可选</span></label>
                <input
                  className="fq-input"
                  type="number"
                  placeholder="如 25"
                  min="0"
                  value={weightKg}
                  onChange={e => setWeightKg(e.target.value)}
                  onKeyDown={e => e.key === "Enter" && doSearch()}
                />
              </div>
              <div className="fq-field">
                <label>货代公司<span style={{ color: "var(--t3)", marginLeft: 4 }}>可选</span></label>
                <input
                  className="fq-input"
                  placeholder="筛选公司名"
                  value={companyFilter}
                  onChange={e => setCompanyFilter(e.target.value)}
                  onKeyDown={e => e.key === "Enter" && doSearch()}
                />
              </div>
              <div className="fq-field">
                <button
                  className="tbtn acc"
                  onClick={doSearch}
                  disabled={searching || !warehouseCode.trim()}
                  style={{ width: "100%" }}
                >
                  {searching ? "查询中…" : "查价"}
                </button>
              </div>
            </div>
          </div>

          {/* Error */}
          {searchError && (
            <div style={{ background: "rgba(248,113,113,.08)", border: "1px solid rgba(248,113,113,.3)",
              borderRadius: 6, padding: "10px 14px", fontSize: "var(--fs-12)", color: "var(--red)" }}>
              {searchError}
            </div>
          )}

          {/* Results */}
          {results.length > 0 && (
            <div style={{ background: "var(--bg1)", border: "1px solid var(--b)", borderRadius: 8, overflow: "hidden" }}>
              <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--b)", display: "flex", gap: 10, alignItems: "center" }}>
                <span style={{ fontSize: "var(--fs-12)", fontWeight: 600, color: "var(--t)", fontFamily: "var(--font)" }}>
                  {warehouseCode.toUpperCase()} — {results.length} 条报价
                </span>
                {results.length > 0 && (
                  <span style={{ fontSize: "var(--fs-11)", color: "var(--t3)", fontFamily: "var(--font)" }}>
                    最低 {results[0]?.price || "—"}
                  </span>
                )}
              </div>
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "var(--fs-12)", fontFamily: "var(--font)" }}>
                  <thead>
                    <tr style={{ background: "var(--bg2)" }}>
                      {["货代公司", "渠道", "税别", "起运地", "档位", "单位", "价格", "时效", "生效日期", "备注"].map(h => (
                        <th key={h} style={{ padding: "6px 10px", textAlign: "left", color: "var(--t3)",
                          fontWeight: 500, whiteSpace: "nowrap", borderBottom: "1px solid var(--b)" }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {results.map((r, i) => (
                      <tr key={i} style={{ borderBottom: "1px solid var(--b)",
                        background: i === 0 ? "rgba(74,222,128,.04)" : undefined }}>
                        <td style={{ padding: "7px 10px", color: "var(--t)", fontWeight: i === 0 ? 600 : 400 }}>
                          {i === 0 && <span style={{ color: "var(--acc)", marginRight: 4 }}>★</span>}
                          {r.company}
                        </td>
                        <td style={{ padding: "7px 10px", color: "var(--t2)" }}>{r.channel || "—"}</td>
                        <td style={{ padding: "7px 10px", color: "var(--t2)" }}>{r.tax_type || "—"}</td>
                        <td style={{ padding: "7px 10px", color: "var(--t2)" }}>{r.origin || "—"}</td>
                        <td style={{ padding: "7px 10px", color: "var(--t2)" }}>{r.tier || "—"}</td>
                        <td style={{ padding: "7px 10px", color: "var(--t2)" }}>{r.unit || "—"}</td>
                        <td style={{ padding: "7px 10px", fontWeight: 600,
                          color: i === 0 ? "var(--acc)" : "var(--t)" }}>{r.price || "—"}</td>
                        <td style={{ padding: "7px 10px", color: "var(--t2)" }}>{r.transit || "—"}</td>
                        <td style={{ padding: "7px 10px", color: "var(--t3)" }}>{r.effective_date || "—"}</td>
                        <td style={{ padding: "7px 10px", color: "var(--t3)", maxWidth: 200, overflow: "hidden",
                          textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.note || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* No records + empty state */}
          {!status?.record_count && !searching && results.length === 0 && !searchError && (
            <div style={{ textAlign: "center", padding: "40px 20px", color: "var(--t3)", fontSize: "var(--fs-12)",
              fontFamily: "var(--font)" }}>
              暂无报价数据。请前往「管理报价」上传货代 Excel 报价文件。
            </div>
          )}
        </div>
      )}

      {/* ── Admin Tab ── */}
      {tab === "admin" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {/* Upload */}
          <div
            onDragOver={e => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={e => { e.preventDefault(); setDragOver(false); doUpload(e.dataTransfer.files); }}
            onClick={() => fileRef.current?.click()}
            style={{
              background: dragOver ? "rgba(74,222,128,.08)" : "var(--bg1)",
              border: `2px dashed ${dragOver ? "var(--acc)" : "var(--b)"}`,
              borderRadius: 8, padding: "24px", cursor: "pointer",
              textAlign: "center", transition: "all .2s",
            }}
          >
            <div style={{ fontSize: 24, marginBottom: 8 }}>📂</div>
            <div style={{ fontSize: "var(--fs-13)", color: "var(--t)", fontFamily: "var(--font)" }}>
              拖拽或点击上传报价文件
            </div>
            <div style={{ fontSize: "var(--fs-11)", color: "var(--t3)", marginTop: 4 }}>支持 .xls / .xlsx，可多选</div>
            <input ref={fileRef} type="file" accept=".xls,.xlsx" multiple style={{ display: "none" }}
              onChange={e => e.target.files && doUpload(e.target.files)} />
          </div>

          {/* Upload options */}
          <div className="fq-form">
            <div className="fq-field">
              <label>默认货代公司<span style={{ color: "var(--t3)", marginLeft: 4 }}>可选</span></label>
              <input className="fq-input" placeholder="如：万路达" value={uploadCompany}
                onChange={e => setUploadCompany(e.target.value)} />
            </div>
            <div className="fq-field">
              <label>市场<span style={{ color: "var(--t3)", marginLeft: 4 }}>可选</span></label>
              <input className="fq-input" placeholder="如：美国" value={uploadMarket}
                onChange={e => setUploadMarket(e.target.value)} />
            </div>
            <div className="fq-field">
              <button className="tbtn" onClick={doRebuild} disabled={rebuilding} style={{ width: "100%" }}>
                {rebuilding ? "重建中…" : "🔄 重建索引"}
              </button>
            </div>
          </div>

          {uploading && (
            <div style={{ fontSize: "var(--fs-12)", color: "var(--t3)", fontFamily: "var(--font)" }}>正在上传并解析…</div>
          )}
          {uploadMsg && (
            <div style={{ fontSize: "var(--fs-12)", color: "var(--acc)", fontFamily: "var(--font)", background: "rgba(74,222,128,.06)",
              border: "1px solid rgba(74,222,128,.2)", borderRadius: 6, padding: "8px 12px" }}>
              {uploadMsg}
            </div>
          )}

          {/* File list */}
          {files.length > 0 && (
            <div style={{ background: "var(--bg1)", border: "1px solid var(--b)", borderRadius: 8, overflow: "hidden" }}>
              <div style={{ padding: "8px 14px", borderBottom: "1px solid var(--b)",
                fontSize: "var(--fs-11)", color: "var(--t3)", fontFamily: "var(--font)" }}>
                已上传 {files.length} 个文件
              </div>
              {files.map((f, i) => (
                <div key={i} style={{
                  padding: "10px 14px", borderBottom: i < files.length - 1 ? "1px solid var(--b)" : undefined,
                  display: "flex", gap: 12, alignItems: "center",
                  opacity: f.disabled ? 0.45 : 1,
                }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: "var(--fs-12)", color: "var(--t)", fontFamily: "var(--font)",
                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {f.disabled && <span style={{ color: "var(--t3)", marginRight: 6 }}>[已禁用]</span>}
                      {f.name}
                    </div>
                    <div style={{ fontSize: "var(--fs-11)", color: "var(--t3)", marginTop: 2, fontFamily: "var(--font)" }}>
                      {f.company && <span style={{ marginRight: 8 }}>{f.company}</span>}
                      {f.market && <span style={{ marginRight: 8 }}>{f.market}</span>}
                      <span style={{ marginRight: 8 }}>{f.records} 条</span>
                      <span>{(f.size / 1024).toFixed(0)} KB</span>
                      {f.error && <span style={{ color: "var(--red)", marginLeft: 8 }}>{f.error}</span>}
                    </div>
                  </div>
                  <button className="tbtn" style={{ fontSize: "var(--fs-10)", padding: "2px 8px" }}
                    onClick={() => doToggle(f.name)}>
                    {f.disabled ? "启用" : "禁用"}
                  </button>
                  <button className="tbtn" style={{ fontSize: "var(--fs-10)", padding: "2px 8px", color: "var(--red)" }}
                    onClick={() => doDelete(f.name)}>删除</button>
                </div>
              ))}
            </div>
          )}

          {files.length === 0 && (
            <div style={{ textAlign: "center", padding: "30px", color: "var(--t3)", fontSize: "var(--fs-12)",
              fontFamily: "var(--font)" }}>
              暂无已上传文件
            </div>
          )}
        </div>
      )}
    </div>
  );
}
