/**
 * 能力市场 —— 把「Agent 到底能用什么」摊开在一页上。
 *
 * 对标 MyLevis 的能力市场 / WorkBuddy 的「专家·技能·连接器」：技能、MCP、
 * 智能体、授权四类能力各占一个 tab，看得见才谈得上组合。
 *
 * 一个长期被混淆的事实在这里被摆平：**技能和 MCP 都有两套注册表**。
 * 「Skill 中心」的技能库在工作台数据目录下（`{data_dir}/skills`，早年在
 * ~/.hermes/skills，已迁走），而 agent 跑一轮时能加载的是 ~/.ivyea/skills；
 * 同理 ops 原有的 MCP 管理面板管的是 Claude Code 的 ~/.claude.json，
 * 而决定工作台里 Agent 能连哪些数据源的是 ~/.ivyea/mcp.json。
 * 这一页把两边都标明来源分区列出，不再让人以为只有一套。
 */
import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../../App";
import { ToastProvider, useToast } from "../../components/toast";
import { useConfirm } from "../../components/ConfirmDialog";
import {
  consolePresetDelete,
  consolePresets,
  consolePresetSave,
  consoleWorkspaces,
  notifyConsolePresetsChanged,
  ivyeaMcpDelete,
  ivyeaMcpServers,
  ivyeaMcpUpsert,
  ivyeaModelProviders,
  ivyeaSkills,
  providerModelId,
  type AgentMcpServer,
  type ConsolePreset,
  type IvyeaSkillInfo,
} from "../../api/ivyeaAgent";
import { agentSyncRun, agentSyncStatus, type AgentSyncStatus } from "../../api/skill";
import { getSettings, patchSettings } from "../../api/settings";
import { marketBrowse, marketStatus, type MarketItem } from "../../api/client";
import { errText } from "../../lib/errText";
import CommunityMarket from "./CommunityMarket";

// Skill 中心那三块并进来了。**懒加载**：它们加起来 1500+ 行（还带代码编辑器和文件树），
// 直接 import 会把能力市场的首屏包一起拖大，而多数人打开这一页是去看社区市场的。
const SkillTools = lazy(() => import("./SkillTools"));
const IdeaSkill = lazy(() => import("./IdeaSkill"));
const SkillBrowse = lazy(() => import("../skill/SkillBrowse"));
const ImportGitHubDialog = lazy(() => import("../skill/ImportGitHubDialog"));

type Tab = "community" | "skills" | "mcp" | "agents" | "auth";

const TABS: { key: Tab; icon: string; label: string; hint: string }[] = [
  // **社区排第一，也是默认页。** 打开「能力市场」看到的第一屏就该是"能拿到什么
  // 新东西"，而不是"我本地已经有什么" —— 后者用户本来就知道。
  { key: "community", icon: "⬢", label: "社区市场", hint: "别人做好的方法，装过来就能用" },
  { key: "skills", icon: "✦", label: "技能", hint: "把好方法固化成可复用的流程" },
  { key: "mcp", icon: "⚑", label: "MCP", hint: "让 Agent 连得上工具和数据" },
  { key: "agents", icon: "◉", label: "智能体", hint: "预设打法与可选 provider" },
  { key: "auth", icon: "🔑", label: "授权", hint: "数据源密钥的接入状态" },
];

function Section({ title, sub, action, children }: {
  title: string; sub?: string; action?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <div className="cap-section">
      <div className="cap-section-head">
        <b>{title}</b>
        {sub && <span>{sub}</span>}
        {/* 区块级动作（比如社区市场的开关）靠右，与标题同一行 */}
        {action && <span style={{ marginLeft: "auto", alignSelf: "center" }}>{action}</span>}
      </div>
      {children}
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="cap-empty">{children}</div>;
}

/**
 * 技能库挂载状态 —— 把「哪些技能任务台真能自动匹配到」摆在明面上。
 *
 * 从 Skill 中心「管理」页搬过来的（那个板块已并入本页）。技能库是**原地挂**给
 * Agent 的，改完立即生效，这里只显示挂没挂上、挂了几个。
 */
function AgentSkillMountBar() {
  const [st, setSt] = useState<AgentSyncStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const load = useCallback(() => {
    agentSyncStatus().then(setSt).catch(() => setSt(null));
  }, []);
  useEffect(load, [load]);

  const remount = async () => {
    setBusy(true);
    setMsg("");
    try {
      const r = await agentSyncRun();
      setMsg(r.error ? `挂载失败：${r.error}` : `已挂上 ${r.count} 个技能`);
      load();
    } catch (e) {
      setMsg(errText(e, "挂载失败"));
    } finally {
      setBusy(false);
    }
  };

  if (!st) return null;
  return (
    <div className="card cap-mount">
      <span>
        <b>{st.count}</b> 个技能已挂给 Agent（改完即时生效）
        <span className="cap-dim">
          （只挂 {st.domains.join(" / ")} 分类；挂上的才能在任务台被自动匹配到，
          其余的在「运行」里手动跑）
        </span>
      </span>
      <button className="tbtn" onClick={() => void remount()} disabled={busy}>
        {busy ? "挂载中…" : "重新挂载"}
      </button>
      {msg && <span className="cap-dim">{msg}</span>}
    </div>
  );
}

// ── 技能 ─────────────────────────────────────────────────────────────────────
//
// **Skill 中心整个并进这里**（2026-08-17）。此前同一批技能被列了三遍：这一页的只读
// 卡片、Skill 中心「工具」页的可运行列表、Skill 中心「管理」页的文件浏览器 —— 还分在
// 两个板块里，用户看到的就是"功能重复"。
//
// 现在只有一份列表：运行 / 创建 / 管理 是同一批技能上的三个动作，用分段控件切。
// 顶级标签仍是 5 个，不因为并进来就膨胀。
const SEGMENTS = [
  { key: "run", label: "运行", hint: "填参数就能跑" },
  { key: "create", label: "创建", hint: "一句话生成 Skill" },
  { key: "manage", label: "管理", hint: "编辑文件 / 快照 / 回收站" },
] as const;
type Segment = (typeof SEGMENTS)[number]["key"];

const SEG_KEY = "ivyea-ops.capabilities.skill-seg";

function SkillsTab({ canRunSkillHub }: { canRunSkillHub: boolean }) {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [agentSkills, setAgentSkills] = useState<IvyeaSkillInfo[]>([]);
  const [agentErr, setAgentErr] = useState("");
  const [loading, setLoading] = useState(true);
  const [showGithubImport, setShowGithubImport] = useState(false);

  // 深链优先，其次记住上次停在哪一段。老的 /skill-hub?tab=create 会被重定向成
  // ?tab=skills&seg=create，所以这里认的是 seg。
  const [seg, setSeg] = useState<Segment>(() => {
    const fromUrl = searchParams.get("seg");
    if (SEGMENTS.some((x) => x.key === fromUrl)) return fromUrl as Segment;
    const stored = localStorage.getItem(SEG_KEY);
    return SEGMENTS.some((x) => x.key === stored) ? (stored as Segment) : "run";
  });

  useEffect(() => { localStorage.setItem(SEG_KEY, seg); }, [seg]);

  const switchSeg = (next: Segment) => {
    setSeg(next);
    setSearchParams((prev) => {
      const p = new URLSearchParams(prev);
      p.set("tab", "skills");
      p.set("seg", next);
      if (next !== "run") p.delete("tool");   // 工具深链只属于「运行」
      return p;
    }, { replace: true });
  };

  useEffect(() => {
    let alive = true;
    ivyeaSkills()
      .then((d) => { if (alive) setAgentSkills(d?.skills || []); })
      // 取不到要说清楚。Agent 服务刚重启时这里会 503，静默显示"0 个"会让人
      // 以为技能库真的空了，而不是"这会儿读不到"。
      .catch((e) => {
        if (!alive) return;
        setAgentErr(e?.response?.status === 503
          ? "IvyeaAgent 服务未就绪，稍候刷新即可。"
          : errText(e, "读取 Agent 技能库失败。"));
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  /** 带着技能回任务台：Console 读 ?skill= 预选 chip，用户直接说需求就行。 */
  const useSkill = (id: string) => navigate(`/console?skill=${encodeURIComponent(id)}`);

  // Agent 自带的那批不在 Skill 中心的库里，编辑不了、也不走参数表单 —— 它们的用法
  // 就是"在任务台说需求"。所以单独一段，只有一个动作。
  const agentSection = (
    <Section
      title="Agent 内置技能"
      sub={`${agentSkills.length} 个 · 随 IvyeaAgent 分发，在任务台说需求时自动匹配`}
    >
      {loading ? <div className="cap-grid">{[0, 1, 2].map((i) => <div key={i} className="skeleton line lg" />)}</div>
        : agentErr ? <Empty>{agentErr}</Empty>
        : agentSkills.length === 0 ? <Empty>没有内置技能。</Empty> : (
        <div className="cap-grid">
          {agentSkills.map((s) => (
            <div className="cap-card" key={s.id}>
              <div className="cap-card-head">
                <i>✦</i>
                <b>{s.title || s.id}</b>
                {s.domain && <span className="cap-tag">{s.domain}</span>}
              </div>
              <div className="cap-card-desc">{s.description || s.id}</div>
              <div className="cap-card-foot">
                <code>{s.id}</code>
                <button className="cs-btn" onClick={() => useSkill(s.id)}>在任务台用</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </Section>
  );

  // 没有 skill-hub 模块的人只看得到内置技能：运行/创建/管理背后的接口会 403，
  // 摆出来只是一排点不动的按钮。
  if (!canRunSkillHub) return agentSection;

  return (
    <>
      <div className="cap-seg">
        {SEGMENTS.map((x) => (
          <button
            key={x.key}
            className={"cap-seg-btn" + (seg === x.key ? " active" : "")}
            onClick={() => switchSeg(x.key)}
            title={x.hint}
          >
            {x.label}
          </button>
        ))}
        <span className="cap-seg-hint">{SEGMENTS.find((x) => x.key === seg)?.hint}</span>
      </div>

      <Suspense fallback={<div className="cap-grid">{[0, 1, 2].map((i) => <div key={i} className="skeleton line lg" />)}</div>}>
        {seg === "run" && (
          <>
            {agentSection}
            <SkillTools embedded />
          </>
        )}
        {seg === "create" && <IdeaSkill embedded />}
        {seg === "manage" && (
          <>
            <AgentSkillMountBar />
            <div style={{ marginBottom: 10 }}>
              <button className="tbtn" style={{ fontSize: "var(--fs-10)" }}
                      onClick={() => setShowGithubImport(true)}>
                ⬇ 从 GitHub 导入 Skill
              </button>
            </div>
            <SkillBrowse />
          </>
        )}
      </Suspense>

      {showGithubImport && (
        <Suspense fallback={null}>
          <ImportGitHubDialog onClose={() => setShowGithubImport(false)} />
        </Suspense>
      )}
    </>
  );
}

// ── MCP ──────────────────────────────────────────────────────────────────────
const BLANK_FORM = {
  name: "", transport: "http" as "http" | "sse" | "stdio",
  url: "", command: "", args: "", trusted: false,
};

function McpTab({ isAdmin }: { isAdmin: boolean }) {
  const notify = useToast();
  const confirm = useConfirm();
  const [rows, setRows] = useState<AgentMcpServer[]>([]);
  const [claude, setClaude] = useState<{ name: string; transport: string }[]>([]);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState({ ...BLANK_FORM });
  const [saving, setSaving] = useState(false);
  const [open, setOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await ivyeaMcpServers();
      setRows(d.servers || []);
      setClaude(d.claude_servers || []);
    } catch (e: any) {
      notify("error", errText(e, "读取 MCP 配置失败"));
    } finally {
      setLoading(false);
    }
  }, [notify]);

  useEffect(() => { void load(); }, [load]);

  const save = async () => {
    if (!form.name.trim()) { notify("warn", "请填服务器名"); return; }
    setSaving(true);
    try {
      await ivyeaMcpUpsert({
        name: form.name.trim(),
        transport: form.transport,
        url: form.url.trim() || undefined,
        command: form.command.trim() || undefined,
        args: form.args.trim() ? form.args.trim().split(/\s+/) : undefined,
        trusted: form.trusted,
      });
      notify("success", `已保存 ${form.name.trim()}`);
      setForm({ ...BLANK_FORM });
      setOpen(false);
      await load();
    } catch (e: any) {
      notify("error", errText(e, "保存失败"));
    } finally {
      setSaving(false);
    }
  };

  const remove = async (row: AgentMcpServer) => {
    const extra = row.managed
      ? "\n\n注意：这台是由「系统配置 → 数据源」的密钥自动同步的，删除后下次保存设置又会回来。要彻底移除请先清掉对应密钥。"
      : "";
    const ok = await confirm({
      title: `移除 MCP 服务器「${row.name}」？`,
      message: `Agent 之后将无法通过它取数。${extra}`,
      danger: true,
    });
    if (!ok) return;
    try {
      await ivyeaMcpDelete(row.name);
      notify("success", `已移除 ${row.name}`);
      await load();
    } catch (e: any) {
      notify("error", errText(e, "移除失败"));
    }
  };

  return (
    <>
      <Section
        title="Agent 的 MCP"
        sub="~/.ivyea/mcp.json · 决定工作台里的 Agent 能连哪些工具和数据"
      >
        {loading ? <div className="skeleton line lg" /> : rows.length === 0 ? (
          <Empty>还没有配置 MCP 服务器。在「系统配置 → 数据源」填好密钥会自动同步进来，也可以在这里手动加。</Empty>
        ) : (
          <table className="tbl cap-table">
            <thead>
              <tr><th>名称</th><th>传输</th><th>地址 / 命令</th><th>免审批</th><th>来源</th><th /></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.name}>
                  <td><b>{r.name}</b>{r.has_data_source && <span className="cap-tag">数据源映射</span>}</td>
                  <td>{r.transport}</td>
                  <td className="cap-mono">{r.spec.url || r.spec.command || "—"}</td>
                  <td>{r.trusted ? <span className="cs-ok">✓ 免审批</span> : <span style={{ color: "var(--t3)" }}>需确认</span>}</td>
                  <td>{r.managed ? <span className="cap-tag">系统同步</span> : <span style={{ color: "var(--t3)" }}>手动</span>}</td>
                  <td style={{ textAlign: "right" }}>
                    {isAdmin && <button className="cs-btn" onClick={() => void remove(r)}>移除</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {isAdmin && (
          open ? (
            <div className="cap-form">
              <div className="cap-form-row">
                <input className="inp" placeholder="服务器名（字母数字 _ . -）" value={form.name}
                       onChange={(e) => setForm({ ...form, name: e.target.value })} />
                <select className="inp" value={form.transport}
                        onChange={(e) => setForm({ ...form, transport: e.target.value as any })}>
                  <option value="http">http</option>
                  <option value="sse">sse</option>
                  <option value="stdio">stdio</option>
                </select>
              </div>
              {form.transport === "stdio" ? (
                <div className="cap-form-row">
                  <input className="inp" placeholder="command（可执行文件路径）" value={form.command}
                         onChange={(e) => setForm({ ...form, command: e.target.value })} />
                  <input className="inp" placeholder="args（空格分隔，可空）" value={form.args}
                         onChange={(e) => setForm({ ...form, args: e.target.value })} />
                </div>
              ) : (
                <input className="inp" placeholder="url" value={form.url}
                       onChange={(e) => setForm({ ...form, url: e.target.value })} />
              )}
              <label className="cap-check">
                <input type="checkbox" checked={form.trusted}
                       onChange={(e) => setForm({ ...form, trusted: e.target.checked })} />
                <span>免审批调用（只读数据源建议勾上；能写的服务器别勾，留着人工确认）</span>
              </label>
              <div className="cap-form-actions">
                <button className="cs-btn cs-btn-primary" disabled={saving} onClick={() => void save()}>
                  {saving ? "保存中…" : "保存"}
                </button>
                <button className="cs-btn" onClick={() => { setOpen(false); setForm({ ...BLANK_FORM }); }}>取消</button>
              </div>
              <div className="cap-note">
                填好即刻生效，不用重启 —— Agent 每次调用都重新读这份配置。
                这里不做连通性探测，显示的是「配了什么」，不是「连得上」。
              </div>
            </div>
          ) : (
            <button className="cs-btn" onClick={() => setOpen(true)}>+ 添加 MCP 服务器</button>
          )
        )}
      </Section>

      <Section title="Claude Code 的 MCP" sub="~/.claude.json · 只读展示，供对照；在「外部智能体」板块里管理">
        {claude.length === 0 ? <Empty>没有读到 Claude Code 的 MCP 配置。</Empty> : (
          <div className="cap-chips">
            {claude.map((c) => (
              <span className="cap-chip" key={c.name}>{c.name}<em>{c.transport}</em></span>
            ))}
          </div>
        )}
      </Section>
    </>
  );
}

// ── 智能体 ───────────────────────────────────────────────────────────────────
/**
 * 预设 = 一句"以后这类活按这套跑"：哪个技能、哪个审批档位、落在哪个工作区。
 *
 * 这里**故意不放主脑模型**。主脑是在系统配置里全局切的，预设里再存一份，
 * 两处就会打架 —— 而且 agent 目前也不支持按轮次覆盖模型，存了也是空头支票。
 */
function PresetsSection() {
  const toast = useToast();
  const confirm = useConfirm();
  const [rows, setRows] = useState<ConsolePreset[]>([]);
  const [skills, setSkills] = useState<IvyeaSkillInfo[]>([]);
  const [spaces, setSpaces] = useState<string[]>([]);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({
    name: "", skill: "", approval: "none" as "none" | "remote",
    workspace: "", system: "", note: "",
  });

  const load = useCallback(async () => {
    const [p, sk, ws] = await Promise.all([
      consolePresets().catch(() => []),
      ivyeaSkills().then((d) => d.skills || []).catch(() => [] as IvyeaSkillInfo[]),
      consoleWorkspaces().catch(() => [] as { name: string }[]),
    ]);
    setRows(p);
    setSkills(sk);
    setSpaces(ws.map((w) => w.name));
  }, []);

  useEffect(() => { void load(); }, [load]);

  const save = async () => {
    if (!draft.name.trim()) { toast("error", "预设名不能为空"); return; }
    try {
      await consolePresetSave(draft);
      setAdding(false);
      setDraft({ name: "", skill: "", approval: "none", workspace: "", system: "", note: "" });
      await load();
      notifyConsolePresetsChanged();
      toast("success", "预设已保存");
    } catch (e: any) {
      toast("error", errText(e, "保存失败"));
    }
  };

  const drop = async (name: string) => {
    if (!(await confirm({ title: `删除预设「${name}」？`, message: "只删这套设置，已经跑过的会话不受影响。", danger: true }))) return;
    try {
      await consolePresetDelete(name);
      await load();
      notifyConsolePresetsChanged();
    } catch { toast("error", "删除失败"); }
  };

  return (
    <Section title="预设打法" sub="技能 + 审批档位 + 工作区 + 人设，任务台一键套用 · 每个人的预设只有自己看得到">
      {rows.length === 0 && !adding && (
        <div className="cap-empty">还没有预设。把常跑的活存成一条，下次在任务台点一下就位。</div>
      )}
      {rows.length > 0 && (
        <table className="tbl cap-table">
          <thead><tr><th>名称</th><th>人设</th><th>技能</th><th>审批</th><th>工作区</th><th /></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.name}>
                <td><b>{r.name}</b>{r.note && <div className="cap-dim">{r.note}</div>}</td>
                <td className="cap-persona" title={r.system || undefined}>
                  {r.system || <span className="cap-dim">无</span>}
                </td>
                <td>{r.skill ? <code>{r.skill}</code> : <span className="cap-dim">不限定</span>}</td>
                <td>{r.approval === "remote" ? "逐项审批" : "只读建议"}</td>
                <td>{r.workspace || <span className="cap-dim">默认</span>}</td>
                <td><button className="tbtn danger" onClick={() => void drop(r.name)}>删除</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {adding ? (
        <div className="cap-preset-form">
          <input className="inp" placeholder="预设名，例如「广告周检」" value={draft.name}
                 onChange={(e) => setDraft({ ...draft, name: e.target.value })} autoFocus />
          <select className="inp" value={draft.skill} onChange={(e) => setDraft({ ...draft, skill: e.target.value })}>
            <option value="">不限定技能</option>
            {skills.map((k) => <option key={k.id} value={k.id}>{k.title || k.id}</option>)}
          </select>
          <select className="inp" value={draft.approval}
                  onChange={(e) => setDraft({ ...draft, approval: e.target.value as "none" | "remote" })}>
            <option value="none">只读建议</option>
            <option value="remote">逐项审批（可写）</option>
          </select>
          <select className="inp" value={draft.workspace} onChange={(e) => setDraft({ ...draft, workspace: e.target.value })}>
            <option value="">默认工作区</option>
            {spaces.map((w) => <option key={w} value={w}>{w}</option>)}
          </select>
          <input className="inp" placeholder="备注（可选）" value={draft.note}
                 onChange={(e) => setDraft({ ...draft, note: e.target.value })} />
          {/* 人设独占一整行：它是这里唯一需要写几句话的字段，挤在网格里会很难写。 */}
          <textarea
            className="inp cap-persona-input"
            rows={3}
            placeholder="人设 / 判断标准（可选）。例如：你是有十年经验的亚马逊广告优化师，先看否词和搜索词报告再谈出价，任何调整都要给出数据依据。"
            value={draft.system}
            onChange={(e) => setDraft({ ...draft, system: e.target.value })}
          />
          <div className="cap-preset-actions">
            <button className="tbtn tbtn-acc" onClick={() => void save()}>保存</button>
            <button className="tbtn" onClick={() => setAdding(false)}>取消</button>
          </div>
        </div>
      ) : (
        <button className="tbtn" onClick={() => setAdding(true)}>＋ 新建预设</button>
      )}
    </Section>
  );
}

function AgentsTab() {
  const [providers, setProviders] = useState<any[]>([]);
  const [active, setActive] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    ivyeaModelProviders()
      .then((d) => { if (alive) { setProviders(d?.providers || []); setActive(d?.active || null); } })
      .catch(() => void 0)
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, []);

  return (
    <>
      <PresetsSection />
      {loading ? <div className="skeleton line lg" /> : (
      <Section title="主脑与可选 provider" sub={`${providers.length} 个 provider · 在「系统配置」里切换和填密钥`}>
      {active && (
        <div className="cap-active">
          当前主脑：<b>{active.label || active.provider || "—"}</b>
          {active.model && <code>{active.model}</code>}
        </div>
      )}
      <div className="cap-grid">
        {providers.map((p: any) => {
          // models 是字符串数组，不是对象数组。
          const models = (p.models || []).map(providerModelId).filter(Boolean);
          const shown = models.slice(0, 3);
          const more = models.length - shown.length;
          return (
            <div className="cap-card" key={p.id}>
              <div className="cap-card-head">
                <i>◉</i>
                <b>{p.label || p.id}</b>
                {p.key_status === "configured" && <span className="cap-tag">已配置</span>}
              </div>
              <div className="cap-card-desc">
                {shown.length
                  ? shown.join(" · ") + (more > 0 ? ` … 等 ${models.length} 个` : "")
                  : "未列出模型"}
              </div>
              <div className="cap-card-foot">
                <code>{p.id}</code>
                {p.default_model && <span className="cap-tag">默认 {p.default_model}</span>}
              </div>
            </div>
          );
        })}
      </div>
      </Section>
      )}
    </>
  );
}

// ── 授权 ─────────────────────────────────────────────────────────────────────
const DATA_SOURCES: { key: string; label: string; note: string }[] = [
  { key: "sorftime_key", label: "Sorftime", note: "市场调研 / 关键词 / 类目数据" },
  { key: "sellersprite_key", label: "卖家精灵", note: "选品与流量词数据" },
  { key: "sif_key", label: "SIF", note: "补充数据源" },
  { key: "lingxing_mcp_key", label: "领星 ERP", note: "店铺与广告数据（写操作另有开关）" },
];

function AuthTab() {
  const navigate = useNavigate();
  const [settings, setSettings] = useState<Record<string, any> | null>(null);
  const [secretKeys, setSecretKeys] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    getSettings()
      .then((d) => { if (alive) { setSettings(d.settings as any); setSecretKeys(d.secret_keys || []); } })
      .catch(() => void 0)
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, []);

  if (loading) return <div className="skeleton line lg" />;

  return (
    <Section title="数据源授权" sub="只读状态。密钥统一在「系统配置 → 数据源」填写，避免两处都能改">
      <table className="tbl cap-table">
        <thead><tr><th>数据源</th><th>用途</th><th>状态</th></tr></thead>
        <tbody>
          {DATA_SOURCES.map((d) => {
            // 后端把已设置的密钥列在 secret_keys 里（值本身不回传）；
            // 老版本没有这个字段时退回看 settings 里有没有非空值。
            const configured = secretKeys.includes(d.key) || !!String((settings || {})[d.key] || "").trim();
            return (
              <tr key={d.key}>
                <td><b>{d.label}</b></td>
                <td style={{ color: "var(--t3)" }}>{d.note}</td>
                <td>{configured
                  ? <span className="cs-ok">✓ 已接入</span>
                  : <span style={{ color: "var(--t3)" }}>未配置</span>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <button className="cs-btn" onClick={() => navigate("/hub-settings")}>去系统配置填密钥</button>
    </Section>
  );
}

// ── 页面 ─────────────────────────────────────────────────────────────────────
function CapabilitiesInner() {
  const { role, permissions } = useAuth();
  const isAdmin = role === "admin";

  // 社区市场走 /api/skill-market/*，那个 router 挂在 require_module("skill-hub")
  // 后面。没拿到这个模块的注册用户点进来只会拿到 403，而页面把它显示成
  // 「连不上门道社区」—— 明明是权限不够，看着像社区挂了。
  // 能力市场本身对所有人开放（看看有什么能力是无害的），所以**不是整页拦**，
  // 只是把这一格连同它的入口收起来。
  const canMarket = isAdmin || permissions.includes("skill-hub");
  const tabs = useMemo(() => TABS.filter((t) => t.key !== "community" || canMarket), [canMarket]);

  const [tab, setTab] = useState<Tab>(() => {
    const t = new URLSearchParams(window.location.search).get("tab") as Tab | null;
    if (t && TABS.some((x) => x.key === t)) return t;
    return "community";
  });
  // 默认页是社区市场；没权限的人要落到下一格，否则第一屏还是空的。
  // 深链 ?tab=community 同理，不能因为地址栏里写着就放行。
  const active: Tab = tabs.some((t) => t.key === tab) ? tab : (tabs[0]?.key ?? "skills");
  const hint = useMemo(() => TABS.find((t) => t.key === active)?.hint || "", [active]);

  return (
    <div className="cap-page">
      <div className="home-topbar">
        <span className="home-title"><span style={{ color: "var(--acc)" }}>◈</span> 能力市场</span>
        <span style={{ fontSize: "var(--fs-11)", color: "var(--t3)" }}>{hint}</span>
      </div>

      <div className="home-tabs">
        {tabs.map((t) => (
          <button key={t.key} className={"home-tab" + (active === t.key ? " active" : "")}
                  onClick={() => setTab(t.key)}>
            <span className="home-tab-icon">{t.icon}</span>
            <span className="home-tab-label">{t.label}</span>
          </button>
        ))}
      </div>

      <div className="wb-enter" key={active}>
        {active === "community" && <CommunityMarket embedded />}
        {active === "skills" && <SkillsTab canRunSkillHub={canMarket} />}
        {active === "mcp" && <McpTab isAdmin={isAdmin} />}
        {active === "agents" && <AgentsTab />}
        {active === "auth" && <AuthTab />}
      </div>
    </div>
  );
}

export default function Capabilities() {
  return (
    <ToastProvider>
      <CapabilitiesInner />
    </ToastProvider>
  );
}
