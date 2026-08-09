import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { errText } from "../lib/errText";

/**
 * In-app update flow with real progress, styled like the workbench:
 *   下载(实时百分比) → 安装/重启(轮询健康) → 完成(新版本号) / 失败。
 * The install phase kills the backend, so progress there is driven by polling
 * /api/health until the NEW version answers — not by a backend stream.
 */

type Phase = "starting" | "downloading" | "installing" | "done" | "error";

type Progress = {
  phase: string;
  percent: number;
  downloaded: number;
  total: number;
  error: string;
  target: string;
};

function fmtMB(n: number): string {
  return n > 0 ? `${(n / 1024 / 1024).toFixed(1)}MB` : "—";
}

export default function UpdateModal({
  currentVersion,
  onClose,
}: {
  currentVersion: string;
  onClose: () => void;
}) {
  const [phase, setPhase] = useState<Phase>("starting");
  const [percent, setPercent] = useState(0);
  const [bytes, setBytes] = useState<[number, number]>([0, 0]);
  const [target, setTarget] = useState("");
  const [newVersion, setNewVersion] = useState("");
  const [error, setError] = useState("");
  const stopped = useRef(false);

  // Phase 2: after install is triggered the backend dies — poll health until the
  // NEW version answers (old version answering = restart not done yet).
  const pollRestart = useCallback(async () => {
    const deadline = Date.now() + 5 * 60 * 1000;
    while (!stopped.current && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 2500));
      try {
        const { data } = await api.get<{ version?: string }>("/health", { timeout: 2000 });
        const v = data?.version || "";
        if (v && v !== "dev" && v !== currentVersion) {
          setNewVersion(v);
          setPhase("done");
          return;
        }
      } catch {
        // backend down mid-update — keep waiting
      }
    }
    if (!stopped.current) {
      setError("等待服务重启超时。请查看安装目录 logs\\update.log,或手动重启 IvyeaOps。");
      setPhase("error");
    }
  }, [currentVersion]);

  // Phase 1: kick off download, poll progress, then trigger install.
  useEffect(() => {
    stopped.current = false;
    (async () => {
      try {
        const { data: dl } = await api.post<{ target?: string }>("/setup/update/download");
        if (dl?.target) setTarget(dl.target);
        setPhase("downloading");
        while (!stopped.current) {
          await new Promise((r) => setTimeout(r, 800));
          const { data: p } = await api.get<Progress>("/setup/update/progress");
          if (p.target && !target) setTarget(p.target);
          if (p.phase === "error") {
            setError(p.error || "下载失败");
            setPhase("error");
            return;
          }
          setPercent(p.percent || 0);
          setBytes([p.downloaded || 0, p.total || 0]);
          if (p.phase === "downloaded") break;
        }
        if (stopped.current) return;
        // 触发安装：更新器会立刻杀掉后端，很可能在 /install 的 HTTP 响应回到前端之前就断连。
        // 所以先切到"安装中"再发请求；网络错误（后端已死、无 response）是**预期**的，不当失败，
        // 直接进健康轮询等新版本起来。只有带 detail 的真实业务错误（如"安装包尚未下载完成"）才算失败。
        setPhase("installing");
        try {
          await api.post("/setup/update/install");
        } catch (e: any) {
          // 这里的判断是**服务端有没有明确报错** —— 有 detail 说明后端还活着
          // 并拒绝了这次更新；没有则多半是后端被更新器杀掉了（下面继续轮询健康）。
          // 所以存在性判断要留，只把取文案那一步换成 errText。
          if (e?.response?.data) {
            setError(errText(e, "更新失败"));
            setPhase("error");
            return;
          }
          // 否则是网络错误＝后端被更新器杀掉，符合预期 → 继续轮询健康
        }
        void pollRestart();
      } catch (e: any) {
        setError(errText(e, "更新失败"));
        setPhase("error");
      }
    })();
    return () => {
      stopped.current = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const busy = phase === "starting" || phase === "downloading" || phase === "installing";
  const barPct = phase === "downloading" ? percent : phase === "done" ? 100 : undefined;

  return (
    <div
      style={{
        position: "fixed", inset: 0, zIndex: 3000, display: "grid", placeItems: "center",
        background: "rgba(0,0,0,.55)", backdropFilter: "blur(2px)",
      }}
      onClick={busy ? undefined : onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 420, maxWidth: "92vw", background: "var(--bg1, #11161d)",
          border: "1px solid var(--b)", borderRadius: 10, padding: "20px 22px",
          boxShadow: "0 18px 60px rgba(0,0,0,.5)", color: "var(--t)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14 }}>
          <span style={{ fontSize: 14 }}>⟳</span>
          <span style={{ fontSize: 13, fontWeight: 600, letterSpacing: ".04em" }}>
            软件更新{target ? ` · ${target}` : ""}
          </span>
          {!busy && (
            <button className="tbtn" onClick={onClose}
              style={{ marginLeft: "auto", padding: "1px 8px", fontSize: 11 }}>✕</button>
          )}
        </div>

        {/* Stage line */}
        <div style={{ fontSize: 11, color: "var(--t2)", marginBottom: 10, lineHeight: 1.7 }}>
          {phase === "starting" && "正在准备更新…"}
          {phase === "downloading" && (
            <>正在下载安装包… <span style={{ color: "var(--t3)" }}>
              {fmtMB(bytes[0])} / {fmtMB(bytes[1])}</span></>
          )}
          {phase === "installing" && "正在安装并重启服务（约 20–60 秒,期间页面短暂无响应属正常）…"}
          {phase === "done" && (
            <span style={{ color: "var(--acc)" }}>✓ 已更新到 {newVersion},刷新页面即可使用新版本。</span>
          )}
          {phase === "error" && <span style={{ color: "var(--red, #f66)" }}>✗ {error}</span>}
        </div>

        {/* Progress bar */}
        <div style={{
          height: 8, borderRadius: 4, background: "var(--bg2, rgba(255,255,255,.06))",
          border: "1px solid var(--b)", overflow: "hidden", marginBottom: 16,
        }}>
          {barPct !== undefined ? (
            <div style={{
              height: "100%", width: `${barPct}%`, background: "var(--acc)",
              transition: "width .4s ease", borderRadius: 4,
            }} />
          ) : (
            <div className="upd-indeterminate" style={{
              height: "100%", width: "38%", background: "var(--acc)", borderRadius: 4,
              animation: phase === "installing" || phase === "starting"
                ? "upd-slide 1.2s ease-in-out infinite" : "none",
              opacity: phase === "error" ? 0 : 1,
            }} />
          )}
        </div>
        <style>{`@keyframes upd-slide{0%{margin-left:-38%}100%{margin-left:100%}}`}</style>

        {/* Footer actions */}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          {phase === "done" && (
            <button className="tbtn" onClick={() => window.location.reload()}
              style={{ padding: "4px 14px", fontSize: 11, color: "var(--acc)" }}>
              刷新页面
            </button>
          )}
          {phase === "error" && (
            <>
              <a className="tbtn" href="https://github.com/Hector-xue/IvyeaOps/releases/latest"
                target="_blank" rel="noreferrer"
                style={{ padding: "4px 12px", fontSize: 11, textDecoration: "none" }}>
                打开 Release 页面
              </a>
              <button className="tbtn" onClick={onClose} style={{ padding: "4px 14px", fontSize: 11 }}>
                关闭
              </button>
            </>
          )}
          {busy && (
            <span style={{ fontSize: 10, color: "var(--t3)", alignSelf: "center" }}>
              更新期间请勿关闭 IvyeaOps 窗口
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
