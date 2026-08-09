import { useEffect, useState } from "react";
import SheetSelect from "../../components/SheetSelect";
import {
  adminListUsers, adminSetUserStatus, adminResetUserPassword, adminDeleteUser,
  adminPermissionsCatalog, adminSetUserPermissions,
  type ManagedUser, type PermissionsCatalog,
} from "../../api/client";
import { useAuth } from "../../App";
import { errText } from "../../lib/errText";

const STATUS_LABEL: Record<string, string> = {
  pending: "待审批", active: "已启用", suspended: "已停用",
};
const STATUS_COLOR: Record<string, string> = {
  pending: "var(--amber)", active: "var(--acc)", suspended: "var(--red)",
};

export default function Users() {
  const { role } = useAuth();
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [catalog, setCatalog] = useState<PermissionsCatalog | null>(null);

  // Permission editor state
  const [editing, setEditing] = useState<ManagedUser | null>(null);
  const [editPos, setEditPos] = useState("");
  const [editPerms, setEditPerms] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  const load = async () => {
    setLoading(true);
    try { setUsers(await adminListUsers()); setErr(""); }
    catch (e: any) { setErr(errText(e, "加载失败")); }
    finally { setLoading(false); }
  };

  useEffect(() => {
    if (role !== "admin") { setLoading(false); return; }
    load();
    adminPermissionsCatalog().then(setCatalog).catch(() => {});
  }, [role]);

  if (role !== "admin") {
    return <div className="market-page"><div className="market-error">需要管理员权限</div></div>;
  }

  const setStatus = async (u: ManagedUser, status: "active" | "suspended") => {
    await adminSetUserStatus(u.id, status).catch(() => {});
    load();
  };
  const resetPw = async (u: ManagedUser) => {
    const pw = window.prompt(`为 ${u.email} 设置新密码（至少 8 位）`);
    if (!pw) return;
    try { await adminResetUserPassword(u.id, pw); alert("已重置"); }
    catch (e: any) { alert(errText(e, "重置失败")); }
  };
  const del = async (u: ManagedUser) => {
    if (!window.confirm(`删除用户 ${u.email}？其数据也将无法访问。`)) return;
    await adminDeleteUser(u.id).catch(() => {});
    load();
  };

  const openEdit = (u: ManagedUser) => {
    setEditing(u);
    setEditPos(u.position || "");
    setEditPerms(u.permissions || []);
  };
  const applyPreset = (pos: string) => {
    setEditPos(pos);
    if (catalog && catalog.positions[pos]) setEditPerms([...catalog.positions[pos]]);
  };
  const togglePerm = (key: string) => {
    setEditPerms((prev) => prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]);
  };
  const savePerms = async () => {
    if (!editing) return;
    setSaving(true);
    try {
      await adminSetUserPermissions(editing.id, editPos, editPerms);
      setEditing(null);
      load();
    } catch (e: any) {
      alert(errText(e, "保存失败"));
    } finally {
      setSaving(false);
    }
  };

  const labelOf = (key: string) => catalog?.modules.find((m) => m.key === key)?.label || key;
  const fmt = (ts: number | null) => ts ? new Date(ts).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";

  return (
    <div className="market-page">
      <div className="market-header">
        <span className="market-title"><span className="market-title-icon">⊙</span> 用户管理</span>
        <button className="tbtn" style={{ marginLeft: "auto" }} onClick={load}>↻ 刷新</button>
      </div>

      {err && <div className="market-error">{err}</div>}
      {loading ? (
        <div aria-busy="true" aria-live="polite" style={{ display: "grid", gap: 8, paddingTop: 6 }}>
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="card" style={{ padding: "10px 12px", display: "flex", alignItems: "center", gap: 10 }}>
              <div className="skeleton" style={{ width: 28, height: 28, borderRadius: "50%", flexShrink: 0 }} />
              <div style={{ flex: 1 }}><div className="skeleton line sm" /><div className="skeleton line md" /></div>
            </div>
          ))}
        </div>
      ) : users.length === 0 ? (
        <div className="market-empty"><div className="market-empty-icon">⊙</div><div className="market-empty-title">暂无注册用户</div></div>
      ) : (
        <div className="cat-table-wrap">
          <table className="cat-table">
            <thead><tr><th>邮箱</th><th>角色</th><th>状态</th><th>职位 / 已授权板块</th><th>注册时间</th><th>操作</th></tr></thead>
            <tbody>
              {users.map(u => (
                <tr key={u.id}>
                  <td>{u.email}</td>
                  <td>{u.role}</td>
                  <td><span style={{ color: STATUS_COLOR[u.status] }}>{STATUS_LABEL[u.status] || u.status}</span></td>
                  <td style={{ maxWidth: 280 }}>
                    {u.position && <span style={{ fontWeight: 600, marginRight: 6 }}>{u.position}</span>}
                    <span style={{ fontSize: 12, color: "var(--t3)" }}>
                      {(u.permissions && u.permissions.length)
                        ? u.permissions.map(labelOf).join("、")
                        : "（仅基础板块）"}
                    </span>
                  </td>
                  <td>{fmt(u.created_at)}</td>
                  <td style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    <button className="tbtn tbtn-acc" onClick={() => openEdit(u)}>授权</button>
                    {u.status !== "active" && <button className="tbtn tbtn-acc" onClick={() => setStatus(u, "active")}>启用</button>}
                    {u.status === "active" && <button className="tbtn" onClick={() => setStatus(u, "suspended")}>停用</button>}
                    <button className="tbtn" onClick={() => resetPw(u)}>重置密码</button>
                    <button className="tbtn danger" onClick={() => del(u)}>删除</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editing && catalog && (
        <div
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.5)", zIndex: 1000, display: "grid", placeItems: "center" }}
          onClick={() => !saving && setEditing(null)}
        >
          <div
            style={{ background: "var(--bg, #fff)", border: "1px solid var(--b, #ddd)", borderRadius: 8, padding: 24, width: 460, maxWidth: "92vw", maxHeight: "86vh", overflow: "auto" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>授权板块</div>
            <div style={{ fontSize: 12, color: "var(--t3)", marginBottom: 16 }}>{editing.email}</div>

            <div style={{ marginBottom: 14 }}>
              <label style={{ fontSize: 12, color: "var(--t2)", display: "block", marginBottom: 6 }}>按职位套用预设</label>
              <SheetSelect value={editPos} onChange={applyPreset} title="按职位套用预设"
                style={{ width: "100%", padding: "7px 10px", borderRadius: 4, border: "1px solid var(--b, #ccc)", background: "transparent", color: "inherit" }}
                options={[
                  { value: "", label: "自定义（不套用职位）" },
                  ...Object.keys(catalog.positions).map((p) => ({ value: p, label: p })),
                ]} />
            </div>

            <div style={{ fontSize: 12, color: "var(--t2)", marginBottom: 8 }}>可访问板块（基础板块默认全员可用，这里只授权更高权限板块）</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 18 }}>
              {catalog.modules.map((m) => (
                <label key={m.key} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer", padding: "6px 8px", border: "1px solid var(--b, #eee)", borderRadius: 4 }}>
                  <input type="checkbox" checked={editPerms.includes(m.key)} onChange={() => togglePerm(m.key)} />
                  <span>{m.label}</span>
                  {m.sensitive && <span title="敏感板块" style={{ fontSize: 10, color: "var(--red, #c0392b)", border: "1px solid currentColor", borderRadius: 3, padding: "0 4px" }}>敏感</span>}
                </label>
              ))}
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button className="tbtn" disabled={saving} onClick={() => setEditing(null)}>取消</button>
              <button className="tbtn tbtn-acc" disabled={saving} onClick={savePerms}>{saving ? "保存中…" : "保存授权"}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
