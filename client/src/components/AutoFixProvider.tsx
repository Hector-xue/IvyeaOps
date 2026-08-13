import { useCallback, useEffect, useRef, useState } from "react";
import {
  autofixApply,
  autofixDiagnose,
  autofixGet,
  autofixReject,
  autofixRestart,
  autofixStatus,
  registerAutofixSink,
  setAutofixEnabled,
  type AutofixErrorCtx,
  type AutofixJob,
} from "../api/autofix";
import { errText } from "../lib/errText";

// Endpoint-prefix → human label, so the popup names the board that failed.
const FEATURE_MAP: [string, string][] = [
  ["/skill-tools", "可视化工具"],
  ["/skill/generate", "Skill 生成"],
  ["/skill", "Skill 中心"],
  ["/deep-analysis", "深度分析"],
  ["/listing", "Listing 工具"],
  ["/market", "市场调研"],
  ["/playbook", "上新打法"],
  ["/ad-audit", "广告诊断"],
  ["/amazon", "ASIN 工具"],
  ["/brain", "知识库工作台"],
  ["/news", "资讯"],
  ["/freight", "运费"],
  ["/agent", "智能体"],
];

function featureOf(endpoint: string): string {
  for (const [p, label] of FEATURE_MAP) if (endpoint.includes(p)) return label;
  return "某功能";
}

const COOLDOWN_MS = 5 * 60 * 1000; // same error won't re-trigger within 5 min
const RESTART_COUNTDOWN = 60; // seconds before auto-restart

type UiState =
  | "idle"
  | "prompt"
  | "diagnosing"
  | "review"
  | "failed"
  | "applying"
  | "restart_confirm"
  | "rebuild_done"
  | "restarting";

export default function AutoFixProvider({ children }: { children: React.ReactNode }) {
  const [ui, setUi] = useState<UiState>("idle");
  const [ctx, setCtx] = useState<AutofixErrorCtx | null>(null);
  const [job, setJob] = useState<AutofixJob | null>(null);
  const [err, setErr] = useState("");
  const [countdown, setCountdown] = useState(RESTART_COUNTDOWN);

  const uiRef = useRef<UiState>("idle");
  uiRef.current = ui;
  const lastSig = useRef<{ sig: string; at: number } | null>(null);
  const pollRef = useRef<number | null>(null);

  // ── enable + sink registration ───────────────────────────────────────────
  useEffect(() => {
    let alive = true;
    autofixStatus()
      .then((s) => {
        if (!alive) return;
        setAutofixEnabled(!!s.enabled);
        // Resume a job left mid-flight (e.g. page reload during review).
        if (s.enabled && s.job && s.job.status === "diagnosed") {
          setJob(s.job);
          setCtx(s.job.error);
          setUi("review");
        }
      })
      .catch(() => setAutofixEnabled(false)); // non-admin / off → disabled

    registerAutofixSink((c: AutofixErrorCtx) => {
      // Only one flow at a time; ignore while busy.
      if (uiRef.current !== "idle") return;
      const sig = `${c.method}:${c.endpoint}:${c.status}`;
      const prev = lastSig.current;
      if (prev && prev.sig === sig && Date.now() - prev.at < COOLDOWN_MS) return;
      lastSig.current = { sig, at: Date.now() };
      setCtx({ ...c, feature: featureOf(c.endpoint || "") });
      setErr("");
      setUi("prompt");
    });
    return () => {
      alive = false;
      registerAutofixSink(null);
    };
  }, []);

  const stopPoll = () => {
    if (pollRef.current) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const close = useCallback(() => {
    stopPoll();
    setUi("idle");
    setJob(null);
    setCtx(null);
    setErr("");
  }, []);

  // ── poll a running/applying job ────────────────────────────────────────────
  const poll = useCallback((id: string) => {
    stopPoll();
    pollRef.current = window.setInterval(async () => {
      try {
        const j = await autofixGet(id);
        setJob(j);
        if (j.status === "diagnosed") {
          stopPoll();
          setUi("review");
        } else if (j.status === "failed") {
          stopPoll();
          setErr(j.error_detail || "修复失败");
          setUi("failed");
        }
      } catch {
        stopPoll();
        setErr("无法获取修复进度");
        setUi("failed");
      }
    }, 2000);
  }, []);

  const startFix = useCallback(async () => {
    if (!ctx) return;
    setUi("diagnosing");
    try {
      const j = await autofixDiagnose(ctx);
      setJob(j);
      poll(j.id);
    } catch (e: any) {
      setErr(errText(e, "无法启动修复"));
      setUi("failed");
    }
  }, [ctx, poll]);

  const applyFix = useCallback(async () => {
    if (!job) return;
    setUi("applying");
    try {
      const j = await autofixApply(job.id);
      setJob(j);
      if (j.needs_restart) {
        setCountdown(RESTART_COUNTDOWN);
        setUi("restart_confirm");
      } else {
        setUi("rebuild_done"); // frontend-only fix: refresh suffices
      }
    } catch (e: any) {
      setErr(errText(e, "应用失败"));
      setUi("failed");
    }
  }, [job]);

  const doRestart = useCallback(async () => {
    if (!job) return;
    setUi("restarting");
    try {
      await autofixRestart(job.id);
    } catch {
      /* the request is killed by the restart itself — expected */
    }
    // Poll health until the backend is back, then reload.
    pollRef.current = window.setInterval(async () => {
      try {
        const r = await fetch("/api/health", { cache: "no-store" });
        if (r.ok) {
          stopPoll();
          window.location.reload();
        }
      } catch {
        /* still down */
      }
    }, 3000);
  }, [job]);

  const rejectFix = useCallback(async () => {
    if (job) await autofixReject(job.id).catch(() => {});
    close();
  }, [job, close]);

  // restart countdown
  useEffect(() => {
    if (ui !== "restart_confirm") return;
    if (countdown <= 0) {
      doRestart();
      return;
    }
    const t = window.setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => window.clearTimeout(t);
  }, [ui, countdown, doRestart]);

  useEffect(() => () => stopPoll(), []);

  return (
    <>
      {children}
      {ui !== "idle" && (
        <Modal>
          {ui === "prompt" && (
            <>
              <Title>检测到报错</Title>
              <Body>
                <b>{ctx?.feature}</b> 执行失败
                {ctx?.status ? `（HTTP ${ctx.status}）` : ""}。
                <br />
                是否启动 AI 自动排查并修复？排查在隔离副本中进行，不影响当前使用。
              </Body>
              {ctx?.detail && <Detail>{ctx.detail}</Detail>}
              <Row>
                <button className="tbtn" onClick={startFix}>
                  ⚡ 启动修复
                </button>
                <button className="tbtn" onClick={close}>
                  忽略
                </button>
              </Row>
            </>
          )}

          {ui === "diagnosing" && (
            <>
              <Title>AI 正在排查…</Title>
              <Body>
                <span className="spin" style={{ marginRight: 8 }} />
                正在隔离副本中定位 <b>{ctx?.feature}</b> 的根因，最长几分钟，请稍候。
              </Body>
              <Detail>后台进程运行，可继续使用其他功能。</Detail>
            </>
          )}

          {ui === "review" && job && (
            <>
              <Title>修复方案（请审核）</Title>
              <Detail>{job.summary || "（无说明）"}</Detail>
              {job.changed_files.length > 0 && (
                <div style={{ margin: "8px 0", fontSize: "var(--fs-11)", color: "var(--t2)" }}>
                  改动文件：{job.changed_files.join("、")}
                  {job.needs_restart ? " · 需重启后端" : ""}
                  {job.needs_rebuild ? " · 需重建前端" : ""}
                </div>
              )}
              <Diff text={job.diff} />
              <Row>
                <button className="tbtn" onClick={applyFix}>
                  ✓ 应用{job.needs_restart ? "并重启" : ""}
                </button>
                <button className="tbtn" onClick={rejectFix}>
                  放弃
                </button>
              </Row>
            </>
          )}

          {ui === "applying" && (
            <>
              <Title>正在应用…</Title>
              <Body>
                <span className="spin" style={{ marginRight: 8 }} />
                正在打补丁{job?.needs_rebuild ? "并重建前端" : ""}，请稍候。
              </Body>
            </>
          )}

          {ui === "restart_confirm" && (
            <>
              <Title>需要重启生效</Title>
              <Body>
                修复已应用。需要重启后端服务才能生效。
                <br />
                <b>{countdown}</b> 秒后将自动重启，期间服务会短暂中断。
              </Body>
              <Row>
                <button className="tbtn" onClick={doRestart}>
                  立即重启
                </button>
                <button className="tbtn" onClick={close}>
                  稍后手动
                </button>
              </Row>
            </>
          )}

          {ui === "rebuild_done" && (
            <>
              <Title>修复完成</Title>
              <Body>前端改动已生效，刷新页面即可。</Body>
              <Row>
                <button className="tbtn" onClick={() => window.location.reload()}>
                  ↻ 刷新
                </button>
                <button className="tbtn" onClick={close}>
                  关闭
                </button>
              </Row>
            </>
          )}

          {ui === "restarting" && (
            <>
              <Title>正在重启服务…</Title>
              <Body>
                <span className="spin" style={{ marginRight: 8 }} />
                服务恢复后将自动刷新页面，请稍候。
              </Body>
            </>
          )}

          {ui === "failed" && (
            <>
              <Title>修复未完成</Title>
              <Body style={{ color: "var(--amber)" }}>{err || "未能完成修复"}</Body>
              {job?.summary && <Detail>{job.summary}</Detail>}
              <Row>
                {job?.error?.endpoint && (
                  <button className="tbtn" onClick={startFix}>
                    ↻ 重试
                  </button>
                )}
                <button className="tbtn" onClick={close}>
                  关闭
                </button>
              </Row>
            </>
          )}
        </Modal>
      )}
    </>
  );
}

// ── presentational bits ──────────────────────────────────────────────────────
function Modal({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 9999,
        background: "rgba(0,0,0,.5)",
        display: "grid",
        placeItems: "center",
        padding: 16,
      }}
    >
      <div
        style={{
          width: "min(720px, 96vw)",
          maxHeight: "88vh",
          overflow: "auto",
          background: "var(--bg2)",
          border: "1px solid var(--b)",
          borderRadius: "var(--r)",
          padding: 18,
          fontSize: "var(--fs-12)",
          color: "var(--t2)",
          lineHeight: 1.7,
        }}
      >
        {children}
      </div>
    </div>
  );
}

const Title = ({ children }: { children: React.ReactNode }) => (
  <div style={{ fontSize: "var(--fs-14)", color: "var(--t)", marginBottom: 10, fontWeight: 600 }}>
    🛠 {children}
  </div>
);
const Body = ({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) => (
  <div style={{ marginBottom: 10, ...style }}>{children}</div>
);
const Detail = ({ children }: { children: React.ReactNode }) => (
  <pre
    style={{
      background: "var(--bg)",
      border: "1px solid var(--b)",
      borderRadius: "var(--r)",
      padding: 10,
      fontSize: "var(--fs-11)",
      color: "var(--t3)",
      whiteSpace: "pre-wrap",
      wordBreak: "break-word",
      maxHeight: 200,
      overflow: "auto",
      margin: "0 0 10px",
    }}
  >
    {children}
  </pre>
);
const Row = ({ children }: { children: React.ReactNode }) => (
  <div style={{ display: "flex", gap: 8, marginTop: 4 }}>{children}</div>
);

function Diff({ text }: { text: string }) {
  if (!text) return null;
  const lines = text.split("\n");
  return (
    <pre
      style={{
        background: "var(--bg)",
        border: "1px solid var(--b)",
        borderRadius: "var(--r)",
        padding: 10,
        fontSize: "var(--fs-105)",
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
        maxHeight: 320,
        overflow: "auto",
        margin: "0 0 10px",
        fontFamily: "var(--mono, monospace)",
      }}
    >
      {lines.map((l, i) => (
        <div
          key={i}
          style={{
            color: l.startsWith("+")
              ? "#4caf50"
              : l.startsWith("-")
              ? "var(--amber, #e57373)"
              : l.startsWith("@@")
              ? "var(--accent, #6cf)"
              : "var(--t3)",
          }}
        >
          {l || " "}
        </div>
      ))}
    </pre>
  );
}
