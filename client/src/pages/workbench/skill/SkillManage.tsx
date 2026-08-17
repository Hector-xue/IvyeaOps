import { Suspense, useCallback, useEffect, useState } from "react";
import SkillBrowse from "../../skill/SkillBrowse";
import { agentSyncRun, agentSyncStatus, type AgentSyncStatus } from "../../../api/skill";
import { errText } from "../../../lib/errText";

/**
 * 同步状态条 —— 把「哪些技能任务台真能自动匹配到」摆在明面上。
 *
 * 这个库和 Agent 的技能库是两套（格式都不一样），过去完全不透明：在 Skill 中心
 * 建一个技能，去任务台却怎么问都匹配不到，而界面上没有任何地方解释这件事。
 */
function AgentSyncBar() {
  const [st, setSt] = useState<AgentSyncStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const load = useCallback(() => {
    agentSyncStatus().then(setSt).catch(() => setSt(null));
  }, []);
  useEffect(load, [load]);

  const resync = async () => {
    setBusy(true);
    setMsg("");
    try {
      const r = await agentSyncRun();
      setMsg(r.errors.length
        ? `同步 ${r.synced} 个，${r.errors.length} 个失败：${r.errors[0]}`
        : `已同步 ${r.synced} 个${r.removed ? `，清理 ${r.removed} 个` : ""}`);
      load();
    } catch (e) {
      setMsg(errText(e, "同步失败"));
    } finally {
      setBusy(false);
    }
  };

  if (!st) return null;
  return (
    <div className="card" style={{ padding: "8px 12px", marginBottom: 10, display: "flex",
                                   alignItems: "center", gap: 10, flexWrap: "wrap" }}>
      <span style={{ fontSize: "var(--fs-10)", color: "var(--t2)" }}>
        <b style={{ color: "var(--acc)" }}>{st.count}</b> 个技能已注册到 Agent 技能库
        <span style={{ color: "var(--t3)" }}>
          （只同步 {st.domains.join(" / ")} 分类；同步过去的才能在任务台被自动匹配到，
          其余的在「工具」页手动运行）
        </span>
      </span>
      <button className="tbtn" style={{ fontSize: "var(--fs-9)", marginLeft: "auto" }}
              onClick={() => void resync()} disabled={busy}>
        {busy ? "同步中…" : "重新同步"}
      </button>
      {msg && <span style={{ fontSize: "var(--fs-9)", color: "var(--t3)" }}>{msg}</span>}
    </div>
  );
}

export default function SkillManage() {
  return (
    <div>
      <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)", marginBottom: 10 }}>
        浏览、搜索、编辑 Skill 文件。点击 Skill 进入编辑器。
      </div>
      <AgentSyncBar />
      <Suspense fallback={
        <div aria-busy="true" style={{ display: "grid", gap: 8, padding: "10px 0" }}>
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="card" style={{ padding: "10px 12px" }}>
              <div className="skeleton line md" />
              <div className="skeleton line sm" />
            </div>
          ))}
        </div>
      }>
        <SkillBrowse />
      </Suspense>
    </div>
  );
}
