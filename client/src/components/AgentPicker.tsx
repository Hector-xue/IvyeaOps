import { useEffect, useMemo, useState } from "react";
import { AgentInfo, fetchAgents, rediscoverAgents } from "../api/agents";
import { lockBodyScroll } from "../lib/scrollLock";

type Props = {
  open: boolean;
  onClose: () => void;
  onConfirm: (params: { agent_id: string; model: string; title: string; workdir?: string }) => void;
};

// A nicer model selector than a native <select>: tap to open a bottom sheet
// with a scrollable, highlight-on-select list. Looks consistent on phones.
function ModelPicker({ models, value, onChange }: {
  models: string[];
  value: string;
  onChange: (m: string) => void;
}) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const releaseScroll = lockBodyScroll();
    const onEsc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("keydown", onEsc);
    return () => {
      releaseScroll();
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);

  // Keep a "custom" entry visible if the current value isn't in the list.
  const list = value && !models.includes(value) ? [value, ...models] : models;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inp"
        style={{ display: "flex", alignItems: "center", justifyContent: "space-between", cursor: "pointer", textAlign: "left", width: "100%" }}
      >
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontFamily: "var(--font)" }}>
          {value || "选择模型"}
        </span>
        <span style={{ color: "var(--t3)", fontSize: 9, marginLeft: 8, flexShrink: 0 }}>▼</span>
      </button>

      {open && (
        <div
          onClick={() => setOpen(false)}
          style={{ position: "fixed", inset: 0, zIndex: 10000, background: "rgba(0,0,0,.5)", display: "flex", alignItems: "flex-end", justifyContent: "center" }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width: "100%", maxWidth: 520, maxHeight: "70vh", display: "flex", flexDirection: "column",
              background: "var(--bg1, var(--bg2))", border: "1px solid var(--b)",
              borderRadius: "16px 16px 0 0", boxShadow: "0 -8px 40px rgba(0,0,0,.5)", overflow: "hidden",
            }}
          >
            <div style={{ display: "flex", justifyContent: "center", padding: "10px 0 4px", flexShrink: 0 }}>
              <div style={{ width: 36, height: 4, borderRadius: 2, background: "var(--b2)" }} />
            </div>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "4px 16px 10px", borderBottom: "1px solid var(--b)", flexShrink: 0 }}>
              <span style={{ fontSize: 13, fontWeight: 600, color: "var(--t)" }}>选择模型</span>
              <span onClick={() => setOpen(false)} style={{ cursor: "pointer", color: "var(--t3)", fontSize: 16, lineHeight: 1 }}>✕</span>
            </div>
            <div style={{ overflowY: "auto", WebkitOverflowScrolling: "touch", padding: 6 }}>
              {list.map((m) => {
                const sel = m === value;
                return (
                  <div
                    key={m}
                    role="button"
                    onClick={() => { onChange(m); setOpen(false); }}
                    style={{
                      display: "flex", alignItems: "center", gap: 8, padding: "12px", borderRadius: 8, marginBottom: 2,
                      cursor: "pointer", userSelect: "none",
                      background: sel ? "color-mix(in srgb, var(--acc) 16%, transparent)" : "transparent",
                      color: sel ? "var(--acc)" : "var(--t)", fontSize: 13,
                    }}
                  >
                    <span style={{ flex: 1, fontFamily: "var(--font)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {m}{value && !models.includes(m) ? "（自定义）" : ""}
                    </span>
                    <span style={{ width: 12, textAlign: "center", color: "var(--acc)", flexShrink: 0 }}>{sel ? "✓" : ""}</span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// Modal that lets the user pick which agent to spawn for a new session.
//
// Step 1: pick an agent (cards show binary status & default model).
// Step 2: pick a model from that agent's catalog.
// Step 3: optionally tweak the title and the working directory.
//
// The whole flow lives in one modal — this is a personal hub, not a
// multi-step wizard.  ESC and backdrop click close.
export default function AgentPicker({ open, onClose, onConfirm }: Props) {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [model, setModel] = useState<string>("");
  const [title, setTitle] = useState<string>("");
  const [workdir, setWorkdir] = useState<string>("");

  const refresh = async (rediscover = false) => {
    setLoading(true);
    setError(null);
    try {
      const list = rediscover ? await rediscoverAgents() : await fetchAgents();
      setAgents(list);
      const firstEnabled = list.find((a) => a.enabled);
      if (firstEnabled) {
        setSelected(firstEnabled.id);
        setModel(firstEnabled.default_model || firstEnabled.models[0] || "");
      }
    } catch (e: any) {
      setError(e?.message || "加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) {
      refresh();
      setTitle("");
      setWorkdir("");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const selectedAgent = useMemo(
    () => agents.find((a) => a.id === selected) || null,
    [agents, selected],
  );

  if (!open) return null;

  return (
    <div className="modal-bd" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="m-head">
          <span className="m-title">◆ 新建智能体会话</span>
          <button
            className="tbtn"
            onClick={() => refresh(true)}
            disabled={loading}
            title="重新探测已安装的 agent"
          >
            {loading ? <span className="spin" /> : "↻"} 重新探测
          </button>
          <button className="tbtn" onClick={onClose} aria-label="关闭">
            ✕
          </button>
        </div>

        <div className="m-body">
          {error && (
            <div className="inline-err" style={{ margin: "0 0 12px 0" }}>
              <span>⚠ {error}</span>
            </div>
          )}

          {/* Step 1: agent cards */}
          <div className="fg">
            <label>选择 Agent</label>
            <div className="ap-grid">
              {agents.map((a) => {
                const active = a.id === selected;
                return (
                  <button
                    key={a.id}
                    onClick={() => {
                      if (!a.enabled) return;
                      setSelected(a.id);
                      setModel(a.default_model || a.models[0] || "");
                    }}
                    disabled={!a.enabled}
                    className={"ap-card" + (active ? " active" : "")}
                  >
                    <div className="apc-name">{a.display_name}</div>
                    <div className="apc-meta">
                      {a.enabled ? (
                        <>
                          <span className="dot-on">●</span>
                          已安装
                        </>
                      ) : (
                        <>
                          <span className="dot-off">○</span>
                          未检测到
                        </>
                      )}
                    </div>
                    <div className="apc-meta">{a.models.length} 个模型</div>
                  </button>
                );
              })}
              {!agents.length && !loading && (
                <div style={{ color: "var(--t3)", fontSize: 11, gridColumn: "1 / -1", padding: 8 }}>
                  没有发现已安装的 agent
                </div>
              )}
              {loading && !agents.length && (
                <div style={{ color: "var(--t3)", fontSize: 11, gridColumn: "1 / -1", padding: 8, display: "flex", alignItems: "center", gap: 8 }}>
                  <span className="spin" /> 正在探测...
                </div>
              )}
            </div>
          </div>

          {/* Step 2: model + title + workdir */}
          {selectedAgent && (
            <>
              {selectedAgent.id === "claude" && selectedAgent.caps?.authenticated === false && (
                <div style={{ background: "color-mix(in srgb, var(--amber) 10%, transparent)", border: "1px solid color-mix(in srgb, var(--amber) 40%, transparent)", borderRadius: 6, padding: "7px 10px", fontSize: 11, color: "var(--amber)", lineHeight: 1.6 }}>
                  ⚠ 未检测到 Claude Code 登录。请在服务器终端运行 <code style={{ fontFamily: "var(--font)", background: "rgba(0,0,0,.2)", padding: "1px 4px", borderRadius: 3 }}>claude auth login</code> 完成授权。
                </div>
              )}
              <div className="fg">
                <label>模型</label>
                <ModelPicker
                  models={selectedAgent.models}
                  value={model}
                  onChange={setModel}
                />
              </div>

              <div className="fg">
                <label>会话标题</label>
                <input
                  className="inp"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="例如：调试 listing.py"
                />
              </div>

              <div className="fg">
                <label>工作目录（可选，留空使用 home）</label>
                <input
                  className="inp"
                  value={workdir}
                  onChange={(e) => setWorkdir(e.target.value)}
                  placeholder="/path/to/your/project"
                />
              </div>
            </>
          )}
        </div>

        <div className="m-foot">
          <button className="tbtn" onClick={onClose}>
            取消
          </button>
          <button
            className="tbtn"
            style={{
              color: "var(--acc)",
              borderColor: "rgba(74,222,128,.4)",
              background: "rgba(74,222,128,.08)",
            }}
            onClick={() =>
              selectedAgent &&
              onConfirm({
                agent_id: selectedAgent.id,
                model: model || selectedAgent.default_model || "",
                title: title.trim() || `${selectedAgent.display_name} 会话`,
                workdir: workdir.trim() || undefined,
              })
            }
            disabled={!selectedAgent || !selectedAgent.enabled}
          >
            创建并打开
          </button>
        </div>
      </div>
    </div>
  );
}
