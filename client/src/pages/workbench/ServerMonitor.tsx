import { useEffect, useRef, useState } from "react";
import { useConfirm } from "../../components/ConfirmDialog";
import {
  monitorLogs,
  monitorServices,
  monitorSnapshot,
  monitorProcesses,
  monitorTokenUsage,
  stopProcess,
  startProcess,
  MonitorSnapshot,
  ServiceStatus,
  ProcessInfo,
  TokenUsageData,
} from "../../api/client";
import { getAiLog, type AiCall } from "../../api/settings";
import { errText } from "../../lib/errText";

function color(v: number, warn: number, danger: number) {
  return v > danger ? "var(--red)" : v > warn ? "var(--amber)" : "var(--acc)";
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n.toFixed(0)} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

function fmtUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function fmtCpuTime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function categoryLabel(category: "critical" | "on-demand" | "optional") {
  if (category === "critical") return "核心";
  if (category === "on-demand") return "按需";
  return "可关闭";
}

function categoryClass(category: "critical" | "on-demand" | "optional") {
  if (category === "critical") return "tr";
  if (category === "on-demand") return "ta";
  return "tg";
}

function statusText(status: string) {
  const map: Record<string, string> = {
    running: "运行",
    sleeping: "等待事件",
    "disk-sleep": "磁盘等待",
    stopped: "停止",
    zombie: "僵尸",
    idle: "空闲",
  };
  return map[status] || status;
}

export default function ServerMonitor() {
  const confirm = useConfirm();
  const [snap, setSnap] = useState<MonitorSnapshot | null>(null);
  const [services, setServices] = useState<ServiceStatus[]>([]);
  const [logs, setLogs] = useState<string[]>([]);
  const [processes, setProcesses] = useState<ProcessInfo[]>([]);
  const [procFilter, setProcFilter] = useState("");
  const [procLoading, setProcLoading] = useState(false);
  const [tokenUsage, setTokenUsage] = useState<TokenUsageData | null>(null);
  const [aiLog, setAiLog] = useState<AiCall[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  // Poll snapshot every 3s, services every 10s, logs every 8s, processes every 10s.
  useEffect(() => {
    let snapCount = 0;
    const tick = async () => {
      try {
        const s = await monitorSnapshot();
        setSnap(s);
        setErr(null);
      } catch (e: any) {
        setErr(errText(e, "获取监控数据失败"));
      }
      if (snapCount % 3 === 0) {
        monitorServices().then(setServices).catch(() => {});
      }
      if (snapCount % 3 === 1) {
        monitorLogs(15).then((r) => setLogs(r.lines)).catch(() => {});
      }
      if (snapCount % 4 === 0) {
        monitorProcesses().then(setProcesses).catch(() => {});
      }
      if (snapCount % 10 === 0) {
        monitorTokenUsage().then(setTokenUsage).catch(() => {});
      }
      if (snapCount % 3 === 2) {
        getAiLog().then(setAiLog).catch(() => {});
      }
      snapCount += 1;
    };
    tick();
    timer.current = window.setInterval(tick, 3000);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, []);

  const handleStop = async (p: ProcessInfo) => {
    const msg = `确定要停止 "${p.name}" 吗？\n\n影响: ${p.impact}\n内存释放: ~${p.memory_mb.toFixed(0)} MB`;
    if (!await confirm({ title: "停止进程", message: msg, confirmText: "停止", danger: true })) return;
    setProcLoading(true);
    const res = p.service
      ? await stopProcess(undefined, p.service)
      : await stopProcess(p.pid);
    setProcLoading(false);
    if (!res.ok) alert("停止失败: " + (res.error || "未知错误"));
    else monitorProcesses().then(setProcesses).catch(() => {});
  };

  const handleStopService = async (s: ServiceStatus) => {
    if (s.category === "critical") return;
    const msg = `确定要停止 "${s.name}" 服务吗？\n\n服务: ${s.description}\n影响: ${s.impact}`;
    if (!await confirm({ title: "停止服务", message: msg, confirmText: "停止", danger: true })) return;
    setProcLoading(true);
    const res = await stopProcess(undefined, s.name);
    setProcLoading(false);
    if (!res.ok) {
      alert("停止失败: " + (res.error || "未知错误"));
      return;
    }
    monitorServices().then(setServices).catch(() => {});
    monitorProcesses().then(setProcesses).catch(() => {});
  };

  const handleStartService = async (name: string) => {
    setProcLoading(true);
    const res = await startProcess(name);
    setProcLoading(false);
    if (!res.ok) alert("启动失败: " + (res.error || "未知错误"));
    else {
      monitorServices().then(setServices).catch(() => {});
      monitorProcesses().then(setProcesses).catch(() => {});
    }
  };

  const filteredProcs = processes.filter(
    (p) =>
      !procFilter ||
      p.name.toLowerCase().includes(procFilter.toLowerCase()) ||
      p.description.includes(procFilter) ||
      p.impact.includes(procFilter) ||
      p.status.includes(procFilter) ||
      (p.service || "").includes(procFilter)
  );

  if (err && !snap) {
    return (
      <div>
        <div className="ptitle">/ 服务器监控</div>
        <div className="card" style={{ color: "var(--red)", fontSize: "var(--fs-11)" }}>
          ✗ {err}
        </div>
      </div>
    );
  }

  if (!snap) {
    return (
      <div>
        <div className="ptitle">/ 服务器监控</div>
        <div aria-busy="true" aria-live="polite" style={{ display: "grid", gap: 10 }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))", gap: 10 }}>
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="card" style={{ padding: 12 }}>
                <div className="skeleton line sm" />
                <div className="skeleton" style={{ height: 22, marginTop: 6, borderRadius: 4 }} />
              </div>
            ))}
          </div>
          <div className="card" style={{ padding: 12 }}>
            <div className="skeleton" style={{ height: 120, borderRadius: 6 }} />
          </div>
        </div>
      </div>
    );
  }

  const cpu = snap.cpu.percent;
  const ram = snap.memory.percent;
  const disk = snap.disk.percent_hardware;
  const diskFs = snap.disk.percent;
  const cpuC = color(cpu, 50, 80);
  const ramC = color(ram, 60, 85);
  const diskC = color(disk, 70, 90);

  const netInPct = Math.min(100, (snap.network.bytes_recv_rate / (10 * 1024 ** 2)) * 100);
  const netOutPct = Math.min(100, (snap.network.bytes_sent_rate / (10 * 1024 ** 2)) * 100);
  const netInStr = fmtBytes(snap.network.bytes_recv_rate) + "/s";
  const netOutStr = fmtBytes(snap.network.bytes_sent_rate) + "/s";

  return (
    <div>
      <div
        className="ptitle"
        style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
      >
        <span>/ 服务器监控 · localhost</span>
        <span style={{ fontSize: "var(--fs-9)", color: "var(--t3)", textTransform: "none" }}>
          ● live · 3s
        </span>
      </div>

      <div className="g4 mb14">
        <MetC label="CPU" value={`${cpu.toFixed(1)}%`} sub={`${snap.cpu.count}核 · load ${snap.cpu.load_1m.toFixed(2)}`} c={cpuC} />
        <MetC
          label="内存"
          value={`${ram.toFixed(1)}%`}
          sub={`${fmtBytes(snap.memory.used)} / ${fmtBytes(snap.memory.total)} · 含缓存 ${snap.memory.percent_used_raw.toFixed(1)}%`}
          c={ramC}
        />
        <MetC
          label="磁盘"
          value={`${disk.toFixed(1)}%`}
          sub={`${fmtBytes(snap.disk.used)} / ${fmtBytes(snap.disk.total_hardware)} · FS ${diskFs.toFixed(1)}%`}
          c={diskC}
        />
        <MetC
          label="运行时长"
          value={fmtUptime(snap.uptime_seconds)}
          sub="自上次重启"
          valFontSize={14}
        />
      </div>

      <div className="g2 mb14">
        <div style={{ display: "flex", flexDirection: "column", gap: 10, minWidth: 0 }}>
        <div className="card">
          <div className="ct">资源使用率</div>
          <Gauge label="CPU" pct={cpu} color={cpuC} />
          <Gauge label="RAM" pct={ram} color={ramC} />
          <Gauge label="磁盘" pct={disk} color={diskC} />
          <Gauge label="网络入" pct={netInPct} color="var(--acc)" display={netInStr} />
          <Gauge label="网络出" pct={netOutPct} color="var(--acc)" display={netOutStr} />
          <div
            style={{
              fontSize: "var(--fs-9)",
              color: "var(--t3)",
              marginTop: 6,
              textAlign: "right",
            }}
          >
            网卡: {snap.network.interface} · 已排除 lo/docker/veth
          </div>
        </div>

        {/* ─── AI 调用记录（从下方整行移入左栏，填补资源使用率下方空白）─── */}
        <div className="card" style={{ flex: 1, display: "flex", flexDirection: "column", marginBottom: 0, minHeight: 0 }}>
          <div className="ct" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span>最近 AI 调用</span>
            <span style={{ color: "var(--t3)", fontSize: "var(--fs-9)", textTransform: "none" }}>
              降级链 IvyeaAgent→全局兜底→DeepSeek→Codex/Claude
            </span>
          </div>
          {aiLog.length === 0 ? (
            <div style={{ color: "var(--t3)", fontSize: "var(--fs-10)", padding: "4px 0" }}>
              暂无记录（启动后还没有 AI 文本调用）
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 5, flex: 1, minHeight: 0, overflowY: "auto" }}>
              {aiLog.map((c, i) => (
                <div
                  key={i}
                  style={{ display: "flex", alignItems: "center", gap: 10, fontSize: "var(--fs-11)", flexWrap: "wrap" }}
                  title={c.failures?.length ? "之前失败：\n" + c.failures.join("\n") : ""}
                >
                  <span style={{ color: c.ok ? "var(--acc)" : "var(--red)", width: 12 }}>{c.ok ? "✓" : "✗"}</span>
                  <span style={{ color: "var(--t3)", minWidth: 120 }}>{c.ts.replace("T", " ")}</span>
                  <span style={{ fontWeight: 600, color: "var(--t)", minWidth: 80 }}>{c.provider}</span>
                  {c.failures && c.failures.length > 0 && (
                    <span style={{ color: "var(--amber)" }}>降级 {c.failures.length} 次</span>
                  )}
                  {c.chars > 0 && <span style={{ color: "var(--t3)" }}>{c.chars} 字</span>}
                </div>
              ))}
            </div>
          )}
        </div>
        </div>{/* /左列容器 */}

        <div className="card service-card">
          <div
            className="ct"
            style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
          >
            <span>服务状态</span>
            <span style={{ color: "var(--t3)", fontSize: "var(--fs-9)", textTransform: "none" }}>
              {services.length} 个监控
            </span>
          </div>
          {services.length === 0 ? (
            <div style={{ color: "var(--t3)", fontSize: "var(--fs-10)" }}>加载中...</div>
          ) : (
            <div className="service-list">
              {services.map((s) => (
                <div className="service-row" key={s.name}>
                  <div className="service-main">
                    <div className="service-name-line">
                      <span className="service-name">{s.name}</span>
                      <span className={`tag ${categoryClass(s.category)}`}>{categoryLabel(s.category)}</span>
                    </div>
                    <div className="service-desc">{s.description}</div>
                    <div className="service-impact">影响：{s.impact}</div>
                  </div>
                  <div className="service-state">
                    {s.active ? (
                      <span className="tag tg">● active</span>
                    ) : (
                      <span className="tag tr">{s.sub_state || "inactive"}</span>
                    )}
                  </div>
                  <div className="service-actions">
                    {s.active ? (
                      s.category === "critical" ? (
                        <button className="tbtn" disabled title="核心服务运行中，不建议从面板停止">运行中</button>
                      ) : (
                        <button
                          className="tbtn"
                          style={{ color: "var(--amber)", borderColor: "rgba(251,191,36,.35)" }}
                          onClick={() => handleStopService(s)}
                          disabled={procLoading}
                          title={`停止 ${s.name}`}
                        >
                          停止
                        </button>
                      )
                    ) : (
                      <button
                        className="tbtn"
                        style={{ color: "var(--blue)" }}
                        onClick={() => handleStartService(s.name)}
                        disabled={procLoading}
                      >
                        启动
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ─── 进程管理面板 ─── */}
      <div className="sl" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span>进程管理（{filteredProcs.length} / {processes.length}）</span>
        <input
          type="text"
          placeholder="搜索进程名或说明..."
          value={procFilter}
          onChange={(e) => setProcFilter(e.target.value)}
          style={{
            background: "var(--bg2)",
            border: "1px solid var(--b)",
            borderRadius: 4,
            padding: "3px 8px",
            fontSize: "var(--fs-10)",
            color: "var(--t)",
            width: 180,
          }}
        />
      </div>

      {/* 内存优化建议 */}
      {processes.length > 0 && (() => {
        const optional = processes.filter(p => p.category === "optional" && (p.status === "running" || p.status === "sleeping" || p.status === "disk-sleep"));
        const onDemand = processes.filter(p => p.category === "on-demand" && (p.status === "running" || p.status === "sleeping" || p.status === "disk-sleep"));
        const optionalMem = optional.reduce((s, p) => s + p.memory_mb, 0);
        const onDemandMem = onDemand.reduce((s, p) => s + p.memory_mb, 0);
        return (
          <div className="card" style={{ marginBottom: 10, padding: "10px 12px", borderLeft: "3px solid var(--acc)" }}>
            <div style={{ fontSize: "var(--fs-11)", fontWeight: 600, marginBottom: 6 }}>💡 内存优化建议</div>
            <div style={{ fontSize: "var(--fs-10)", color: "var(--t2)", lineHeight: 1.8 }}>
              <div>🟢 <b>可安全关闭</b>：{optional.length} 个进程，共占 <span style={{ color: "var(--acc)" }}>{optionalMem.toFixed(0)} MB</span> — 关闭无风险</div>
              <div>🟡 <b>按需运行</b>：{onDemand.length} 个进程，共占 <span style={{ color: "var(--amber)" }}>{onDemandMem.toFixed(0)} MB</span> — 不用时可关，用时再开</div>
              <div>🔴 <b>必须运行</b>：关闭会导致服务器/网站不可用，请勿操作</div>
              <div style={{ marginTop: 4, color: "var(--t3)" }}>
                总可释放: 最多 <b>{(optionalMem + onDemandMem).toFixed(0)} MB</b>（当前已用 {snap ? fmtBytes(snap.memory.used) : "—"}）
              </div>
            </div>
          </div>
        );
      })()}

      <div className="proc-note">
        状态说明：等待事件表示进程正常存活但当前空闲，正在等待网络、磁盘、定时器或用户输入；不是“已停止”。进程是否可用优先看服务状态、CPU/内存变化和停止影响。
      </div>

      <div className="card proc-card">
        {processes.length === 0 ? (
          <div style={{ color: "var(--t3)", fontSize: "var(--fs-10)" }}>
            <span className="spin" /> 加载进程列表...
          </div>
        ) : (
          <div className="proc-table-wrap">
            <table className="tbl proc-tbl">
              <colgroup>
                <col style={{ width: "9%" }} />
                <col style={{ width: "18%" }} />
                <col style={{ width: "23%" }} />
                <col style={{ width: "28%" }} />
                <col style={{ width: "12%" }} />
                <col style={{ width: "10%" }} />
              </colgroup>
              <thead>
                <tr>
                  <th>分类</th>
                  <th>进程</th>
                  <th>简介</th>
                  <th>进阶信息</th>
                  <th>资源</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {filteredProcs.map((p) => (
                  <tr key={p.pid} style={{ opacity: p.status === "stopped" || p.status === "zombie" ? 0.5 : 1 }}>
                    <td><span className={`tag ${categoryClass(p.category)}`}>{categoryLabel(p.category)}</span></td>
                    <td>
                      <div className="proc-name">{p.name}</div>
                      <div className="proc-sub">PID {p.pid}{p.service ? ` · ${p.service}` : ""}</div>
                    </td>
                    <td>
                      <div className="proc-desc" title={p.description}>{p.description}</div>
                      <div className="proc-impact" title={p.impact}>影响：{p.impact}</div>
                    </td>
                    <td>
                      <div className="proc-advanced">
                        <span>状态 {statusText(p.status)}</span>
                        <span>用户 {p.username || "-"}</span>
                        <span>CPU 时长 {fmtCpuTime(p.cpu_time)}</span>
                      </div>
                    </td>
                    <td>
                      <div className="proc-resource" style={{ color: p.memory_mb > 100 ? "var(--amber)" : "var(--t2)" }}>
                        <span>{p.memory_mb.toFixed(0)} MB</span>
                        <span>CPU {p.cpu_percent.toFixed(1)}%</span>
                      </div>
                    </td>
                    <td>
                      {p.can_stop && (p.status === "running" || p.status === "sleeping" || p.status === "disk-sleep") ? (
                        <button
                          className={`proc-action ${p.category === "optional" ? "safe" : "warn"}`}
                          onClick={() => handleStop(p)}
                          disabled={procLoading}
                        >
                          {p.service ? "停止" : "终止"}
                        </button>
                      ) : p.category === "critical" ? (
                        <span className="proc-locked">锁定</span>
                      ) : (
                        <span className="proc-locked">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="sl" style={{ marginTop: 14 }}>nginx 访问日志（最近 {logs.length} 行）</div>
      <div
        className="card"
        style={{
          maxHeight: 220,
          overflowY: "auto",
          fontFamily: "var(--font)",
          fontSize: "var(--fs-10)",
        }}
      >
        {logs.length === 0 ? (
          <span style={{ color: "var(--t3)" }}>暂无日志或无读取权限</span>
        ) : (
          logs.map((l, i) => (
            <div
              key={i}
              style={{
                color: "var(--t3)",
                padding: "2px 0",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
              title={l}
            >
              {l}
            </div>
          ))
        )}
      </div>

      {/* Token Usage */}
      {tokenUsage && <TokenUsagePanel data={tokenUsage} />}
    </div>
  );
}

function MetC({
  label,
  value,
  sub,
  c,
  valFontSize,
}: {
  label: string;
  value: string;
  sub: string;
  c?: string;
  valFontSize?: number;
}) {
  return (
    <div className="met">
      <div className="ml">{label}</div>
      <div className="mv" style={{ color: c, fontSize: valFontSize }}>
        {value}
      </div>
      <div className="ms neu">{sub}</div>
    </div>
  );
}

function Gauge({
  label,
  pct,
  color,
  display,
}: {
  label: string;
  pct: number;
  color: string;
  display?: string;
}) {
  return (
    <div className="gauge-row">
      <div className="gauge-label">{label}</div>
      <div className="gauge-bar">
        <div className="gauge-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <div className="gauge-val" style={{ color }}>
        {display || pct.toFixed(0) + "%"}
      </div>
    </div>
  );
}

function fmtTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return (n / 1000).toFixed(1) + "K";
  return (n / 1_000_000).toFixed(2) + "M";
}

function fmtCredits(n?: number): string {
  if (!n) return "-";
  return n >= 100 ? n.toFixed(1) : n.toFixed(3);
}

function TokenUsagePanel({ data }: { data: TokenUsageData }) {
  const [tab, setTab] = useState<"daily" | "weekly" | "monthly">("daily");
  const list: Array<Record<string, any>> = tab === "daily" ? data.daily : tab === "weekly" ? data.weekly : data.monthly;
  const labelKey = tab === "daily" ? "day" : tab === "weekly" ? "week" : "month";

  const today = data.daily[0];
  const thisWeek = data.weekly[0];
  const thisMonth = data.monthly[0];
  const todayTop = data.today_agents?.[0];
  const totals = data.totals;

  // Bar chart data (last 14 entries of current tab)
  const chartData = list.slice(0, 14).reverse();
  const maxTokens = Math.max(...chartData.map((r: any) => r.total_tokens || 0), 1);

  return (
    <>
      <div className="ptitle" style={{ marginTop: 20 }}>/ Token 使用量统计</div>

      {/* Grand totals — cumulative across all sources & full history */}
      {totals && (
        <div className="g4 mb14">
          <div className="met">
            <div className="ml">累计总 Token</div>
            <div className="mv" style={{ color: "var(--acc)" }}>{fmtTokens(totals.total_tokens)}</div>
            <div className="ms neu">{totals.sessions} 次会话（全部历史）</div>
          </div>
          <div className="met">
            <div className="ml">累计参考金额</div>
            <div className="mv" style={{ color: "var(--amber)" }}>${totals.cost_usd.toFixed(2)}</div>
            <div className="ms neu">含缓存折算</div>
          </div>
          <div className="met">
            <div className="ml">累计输入</div>
            <div className="mv" style={{ color: "var(--blue)" }}>{fmtTokens(totals.input_tokens)}</div>
            <div className="ms neu">缓存读取 {fmtTokens(totals.cache_read_tokens)}</div>
          </div>
          <div className="met">
            <div className="ml">累计输出</div>
            <div className="mv" style={{ color: "var(--purple)" }}>{fmtTokens(totals.output_tokens)}</div>
            <div className="ms neu">缓存写入 {fmtTokens(totals.cache_write_tokens)}</div>
          </div>
        </div>
      )}

      {/* Summary metrics */}
      <div className="g4 mb14">
        <div className="met">
          <div className="ml">今日 Token</div>
          <div className="mv" style={{ color: "var(--acc)" }}>{fmtTokens(today?.total_tokens || 0)}</div>
          <div className="ms neu">{today?.sessions || 0} 次会话</div>
        </div>
        <div className="met">
          <div className="ml">本周 Token</div>
          <div className="mv" style={{ color: "var(--blue)" }}>{fmtTokens(thisWeek?.total_tokens || 0)}</div>
          <div className="ms neu">{thisWeek?.sessions || 0} 次会话</div>
        </div>
        <div className="met">
          <div className="ml">本月 Token</div>
          <div className="mv" style={{ color: "var(--purple)" }}>{fmtTokens(thisMonth?.total_tokens || 0)}</div>
          <div className="ms neu">{thisMonth?.sessions || 0} 次会话</div>
        </div>
        <div className="met">
          <div className="ml">今日最高</div>
          <div className="mv" style={{ color: "var(--amber)" }}>{todayTop?.agent || "-"}</div>
          <div className="ms neu">{todayTop ? fmtTokens(todayTop.total_tokens) : "暂无"} Token</div>
        </div>
      </div>

      <div className="g2 mb14">
        {/* Chart + Table */}
        <div className="card">
          <div className="ct" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span>使用趋势</span>
            <div style={{ display: "flex", gap: 0 }}>
              {(["daily", "weekly", "monthly"] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={`tab${tab === t ? " active" : ""}`}
                  style={{ padding: "3px 10px", fontSize: "var(--fs-9)", borderBottom: "none" }}
                >
                  {t === "daily" ? "日" : t === "weekly" ? "周" : "月"}
                </button>
              ))}
            </div>
          </div>

          {/* Bar chart */}
          <div className="bchart" style={{ height: 56, marginBottom: 6 }}>
            {chartData.map((row: any, i: number) => (
              <div
                key={i}
                className="bar"
                style={{ height: `${Math.max(2, (row.total_tokens / maxTokens) * 56)}px` }}
                title={`${row[labelKey]}: ${fmtTokens(row.total_tokens)}`}
              />
            ))}
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: "var(--fs-9)", color: "var(--t3)" }}>
            <span>{chartData[0]?.[labelKey] || ""}</span>
            <span>{chartData[chartData.length - 1]?.[labelKey] || ""}</span>
          </div>

          {/* Table */}
          <table className="tbl" style={{ marginTop: 10 }}>
            <thead>
              <tr>
                <th>{tab === "daily" ? "日期" : tab === "weekly" ? "周" : "月份"}</th>
                <th>输入</th>
                <th>输出</th>
                <th>总计</th>
                <th>费用</th>
                <th>会话</th>
              </tr>
            </thead>
            <tbody>
              {list.slice(0, 10).map((row: any, i: number) => (
                <tr key={i}>
                  <td>{row[labelKey]}</td>
                  <td>{fmtTokens(row.input_tokens)}</td>
                  <td>{fmtTokens(row.output_tokens)}</td>
                  <td style={{ color: "var(--acc)", fontWeight: 500 }}>{fmtTokens(row.total_tokens)}</td>
                  <td style={{ color: "var(--amber)" }}>${(row.cost_usd || 0).toFixed(2)}</td>
                  <td>{row.sessions}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Model breakdown */}
        <div className="card">
          <div className="ct">模型分布 (30天)</div>
          {data.models.length > 0 ? (
            <>
              {data.models.slice(0, 8).map((m, i) => {
                const pct = Math.min(100, (m.total_tokens / (data.models[0]?.total_tokens || 1)) * 100);
                const colors = ["var(--acc)", "var(--blue)", "var(--purple)", "var(--amber)", "var(--cyan)", "var(--red)"];
                const c = colors[i % colors.length];
                return (
                  <div className="gauge-row" key={i}>
                    <div className="gauge-label" style={{ width: 100 }}>{m.model}</div>
                    <div className="gauge-bar">
                      <div className="gauge-fill" style={{ width: `${pct}%`, background: c }} />
                    </div>
                    <div className="gauge-val" style={{ color: c, width: 50 }}>{fmtTokens(m.total_tokens)}</div>
                  </div>
                );
              })}
              <div style={{ borderTop: "1px solid var(--b)", marginTop: 10, paddingTop: 8 }}>
                <table className="tbl">
                  <thead>
                    <tr><th>模型</th><th>会话</th><th>Token</th><th>费用</th></tr>
                  </thead>
                  <tbody>
                    {data.models.slice(0, 8).map((m, i) => (
                      <tr key={i}>
                        <td>{m.model}</td>
                        <td>{m.sessions}</td>
                        <td style={{ color: "var(--acc)" }}>{fmtTokens(m.total_tokens)}</td>
                        <td style={{ color: "var(--amber)" }}>${(m.cost_usd || 0).toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <div style={{ color: "var(--t3)", fontSize: "var(--fs-10)" }}>暂无数据</div>
          )}
        </div>
      </div>

      <div className="g2 mb14">
        <div className="card">
          <div className="ct">今日智能体 Token 排行</div>
          {data.today_agents && data.today_agents.length > 0 ? (
            data.today_agents.map((a, i) => {
              const pct = Math.min(100, (a.total_tokens / (data.today_agents[0]?.total_tokens || 1)) * 100);
              const colors = ["var(--acc)", "var(--blue)", "var(--purple)", "var(--amber)", "var(--cyan)"];
              const c = colors[i % colors.length];
              return (
                <div key={a.agent}>
                  <div className="gauge-row">
                    <div className="gauge-label" style={{ width: 90 }}>{a.agent}</div>
                    <div className="gauge-bar">
                      <div className="gauge-fill" style={{ width: `${pct}%`, background: c }} />
                    </div>
                    <div className="gauge-val" style={{ color: c, width: 72 }}>{fmtTokens(a.total_tokens)}</div>
                  </div>
                  {a.credits > 0 && (
                    <div style={{ color: "var(--t3)", fontSize: "var(--fs-10)", margin: "-2px 0 5px 90px" }}>
                      {fmtCredits(a.credits)} credits
                    </div>
                  )}
                </div>
              );
            })
          ) : (
            <div style={{ color: "var(--t3)", fontSize: "var(--fs-10)" }}>今日暂无可统计 token</div>
          )}
          <div style={{ marginTop: 8, color: "var(--t3)", fontSize: "var(--fs-10)" }}>
            按服务器本地日期统计，时区：{data.timezone || "Asia/Shanghai"}
          </div>
        </div>

        <div className="card">
          <div className="ct">智能体累计排行（全部历史）</div>
          {data.agents && data.agents.length > 0 ? (
            data.agents.map((a, i) => {
              const pct = Math.min(100, (a.total_tokens / (data.agents[0]?.total_tokens || 1)) * 100);
              const colors = ["var(--acc)", "var(--blue)", "var(--purple)", "var(--amber)", "var(--cyan)"];
              const c = colors[i % colors.length];
              return (
                <div key={a.agent}>
                  <div className="gauge-row">
                    <div className="gauge-label" style={{ width: 90 }}>{a.agent}</div>
                    <div className="gauge-bar">
                      <div className="gauge-fill" style={{ width: `${pct}%`, background: c }} />
                    </div>
                    <div className="gauge-val" style={{ color: c, width: 72 }}>{fmtTokens(a.total_tokens)}</div>
                  </div>
                  {a.credits > 0 && (
                    <div style={{ color: "var(--t3)", fontSize: "var(--fs-10)", margin: "-2px 0 5px 90px" }}>
                      {fmtCredits(a.credits)} credits
                    </div>
                  )}
                </div>
              );
            })
          ) : (
            <div style={{ color: "var(--t3)", fontSize: "var(--fs-10)" }}>暂无数据</div>
          )}
        </div>
      </div>

      {data.coverage && data.coverage.length > 0 && (
        <div className="card" style={{ marginTop: 10 }}>
          <div className="ct">统计数据源覆盖</div>
          <table className="tbl">
            <thead>
              <tr><th>来源</th><th>状态</th><th>会话</th><th>Token</th><th>Credits</th></tr>
            </thead>
            <tbody>
              {data.coverage.map((row) => (
                <tr key={`${row.source}-${row.path}`}>
                  <td title={row.path}>{row.source}</td>
                  <td>{row.status}</td>
                  <td>{row.sessions}</td>
                  <td style={{ color: row.total_tokens ? "var(--acc)" : "var(--t3)" }}>{fmtTokens(row.total_tokens || 0)}</td>
                  <td style={{ color: row.credits ? "var(--amber)" : "var(--t3)" }}>{fmtCredits(row.credits)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
