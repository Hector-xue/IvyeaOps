import { Suspense, useCallback, useEffect, useState } from "react";
import SkillBrowse from "../../skill/SkillBrowse";
import { agentSyncRun, agentSyncStatus, type AgentSyncStatus } from "../../../api/skill";
import { errText } from "../../../lib/errText";

/**
 * 挂载状态条 —— 把「哪些技能任务台真能自动匹配到」摆在明面上。
 *
 * 过去这件事完全不透明：在 Skill 中心建一个技能，去任务台却怎么问都匹配不到
 * （两个库格式不同、互不相通），而界面上没有任何地方解释。现在技能库是**原地挂**
 * 给 Agent 的，改完立即生效，这里只显示挂没挂上、挂了几个。
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
      setMsg(r.error ? `挂载失败：${r.error}`
                     : `已挂上 ${r.count} 个技能`);
      load();
    } catch (e) {
      setMsg(errText(e, "挂载失败"));
    } finally {
      setBusy(false);
    }
  };

  if (!st) return null;
  return (
    <div className="card" style={{ padding: "8px 12px", marginBottom: 10, display: "flex",
                                   alignItems: "center", gap: 10, flexWrap: "wrap" }}>
      <span style={{ fontSize: "var(--fs-10)", color: "var(--t2)" }}>
        <b style={{ color: "var(--acc)" }}>{st.count}</b> 个技能已挂给 Agent（改完即时生效）
        <span style={{ color: "var(--t3)" }}>
          （只挂 {st.domains.join(" / ")} 分类；挂上的才能在任务台被自动匹配到，
          其余的在「工具」页手动运行）
        </span>
      </span>
      <button className="tbtn" style={{ fontSize: "var(--fs-9)", marginLeft: "auto" }}
              onClick={() => void resync()} disabled={busy}>
        {busy ? "挂载中…" : "重新挂载"}
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
