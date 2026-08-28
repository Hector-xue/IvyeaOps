/**
 * 记忆管理面板。
 *
 * 在此之前，agent 的记忆只能从命令行看（`ivyea memory list/show/pending`）。
 * 而记忆里装的正是"我是谁、我定过什么规矩、它从我身上推断出了什么" ——
 * **看不见就不敢信，不敢信就不会用；推断错了也没有地方去改。**
 *
 * 四个区按"你来这一页要干什么"排：
 *   待确认  = agent 猜了点什么，等你点头（最该先看，放最前，有数量角标）
 *   核心记忆 = 每轮都在它上下文里的那两份，改一个字影响所有对话
 *   分类记忆 = 一事一条的中期记忆，可搜可改可删
 *   统计     = 三层记忆各自的健康状况 + 手动整理
 */
import { useCallback, useEffect, useState } from "react";
import { Brain, CheckCircle2, Clock3, Layers, Loader2, RefreshCw, Sparkles, Trash2, XCircle } from "lucide-react";
import { useAuth } from "../../App";
import { useConfirm } from "../../components/ConfirmDialog";
import {
  ivyeaMemoryCore,
  ivyeaMemoryCoreWrite,
  ivyeaMemoryDecide,
  ivyeaMemoryGet,
  ivyeaMemoryList,
  ivyeaMemoryPending,
  ivyeaMemoryReflect,
  ivyeaMemoryStats,
  ivyeaMemoryWrite,
  type MemoryEntry,
  type MemoryStats,
} from "../../api/ivyeaAgent";
import { errText } from "../../lib/errText";
import "../../styles/memory-panel.css";

type View = "pending" | "core" | "entries" | "stats";

const SOURCE_LABEL: Record<string, string> = {
  user: "你说过的",
  manual: "手写的",
  reflection: "它推断的",
};

const CATEGORY_LABEL: Record<string, string> = {
  user: "关于你",
  feedback: "你的要求",
  project: "在做的事",
  domain: "运营打法",
  reference: "资料指针",
};

function SourceTag({ entry }: { entry: MemoryEntry }) {
  // 推断和"你亲口说过"必须一眼区分得开 —— 混为一谈正是错误信念固化的路径。
  const inferred = entry.source === "reflection" || entry.uncertain;
  return (
    <span
      className="mem-tag"
      style={{
        background: inferred ? "var(--warn-bg, #fff7ed)" : "var(--ok-bg, #f0fdf4)",
        color: inferred ? "var(--warn, #b45309)" : "var(--ok, #15803d)",
      }}
      title={entry.evidence || ""}
    >
      {inferred ? "⚠ " : ""}
      {SOURCE_LABEL[entry.source || "user"] || entry.source}
      {typeof entry.confidence === "number" ? ` ${Math.round(entry.confidence * 100)}%` : ""}
    </span>
  );
}

export default function MemoryPanel() {
  const { role } = useAuth();
  const isAdmin = role === "admin";
  const confirm = useConfirm();

  const [view, setView] = useState<View>("pending");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [pending, setPending] = useState<MemoryEntry[]>([]);
  const [entries, setEntries] = useState<MemoryEntry[]>([]);
  const [stats, setStats] = useState<MemoryStats | null>(null);
  const [core, setCore] = useState<{ block: string; file: string; hint: string; text: string }[]>([]);
  const [coreDraft, setCoreDraft] = useState<Record<string, string>>({});
  const [detail, setDetail] = useState<MemoryEntry | null>(null);
  const [q, setQ] = useState("");

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const [p, l, s, c] = await Promise.all([
        ivyeaMemoryPending(), ivyeaMemoryList(), ivyeaMemoryStats(), ivyeaMemoryCore(),
      ]);
      setPending(p.pending || []);
      setEntries(l.entries || []);
      setStats(s);
      setCore(c.blocks || []);
      setCoreDraft(Object.fromEntries((c.blocks || []).map((b) => [b.block, b.text])));
    } catch (e: any) {
      setMsg(errText(e, "读取失败"));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const decide = async (name: string, action: "confirm" | "reject") => {
    if (action === "reject" && !(await confirm({
      title: "驳回这条推断？",
      message: `「${name}」会从待确认区删掉。如果 agent 以后再次观察到同样的规律，它还会重新提出来。`,
      confirmText: "驳回",
      danger: true,
    }))) return;
    setBusy(true);
    try {
      const r = await ivyeaMemoryDecide(name, action);
      setMsg(r.message || (action === "confirm" ? "已确认" : "已驳回"));
      await load();
    } catch (e: any) {
      setMsg(errText(e, "操作失败"));
    } finally { setBusy(false); }
  };

  const removeEntry = async (e: MemoryEntry) => {
    if (!(await confirm({
      title: "删掉这条记忆？",
      message: `「${e.name}」删掉后 agent 不会再想起它。历史版本仍保留在磁盘上。`,
      confirmText: "删除",
      danger: true,
    }))) return;
    setBusy(true);
    try {
      const r = await ivyeaMemoryWrite({ operation: "delete", name: e.name, category: e.category });
      setMsg(r.message || "已删除");
      setDetail(null);
      await load();
    } catch (err: any) {
      setMsg(errText(err, "删除失败"));
    } finally { setBusy(false); }
  };

  const saveCore = async (block: string) => {
    const next = coreDraft[block] ?? "";
    const cur = core.find((b) => b.block === block)?.text ?? "";
    if (next === cur) { setMsg("没有改动"); return; }
    setBusy(true);
    try {
      // 整块替换走 replace(old=旧全文)：这样 agent 侧的漂移保护仍然生效 ——
      // 如果这中间它自己改过这个文件，写入会被拒而不是把它的改动抹掉。
      const r = await ivyeaMemoryCoreWrite({ block, operation: "replace", old: cur, content: next });
      setMsg(r.message || (r.ok ? "已保存" : "保存失败"));
      await load();
    } catch (e: any) {
      setMsg(errText(e, "保存失败"));
    } finally { setBusy(false); }
  };

  const runReflect = async () => {
    setBusy(true);
    try {
      const r = await ivyeaMemoryReflect();
      setMsg(r.message || "已开始整理");
    } catch (e: any) {
      setMsg(errText(e, "整理失败"));
    } finally { setBusy(false); }
  };

  const openDetail = async (e: MemoryEntry) => {
    setBusy(true);
    try {
      const r = await ivyeaMemoryGet(e.name, e.category);
      setDetail(r.entry || null);
    } finally { setBusy(false); }
  };

  const shown = entries.filter((e) => {
    if (!q.trim()) return true;
    const hay = `${e.name} ${e.description || ""} ${e.keywords || ""} ${e.category}`.toLowerCase();
    return hay.includes(q.trim().toLowerCase());
  });

  const TABS: { key: View; label: string; badge?: number }[] = [
    { key: "pending", label: "待确认", badge: pending.length },
    { key: "core", label: "核心记忆" },
    { key: "entries", label: "分类记忆", badge: entries.length },
    { key: "stats", label: "统计" },
  ];

  return (
    <div className="mem-panel">
      <div className="mem-head">
        <Brain size={16} />
        <strong>记忆</strong>
        <span className="mem-hint">
          这里是 agent 长期记住的东西。标着「⚠ 它推断的」的条目<b>没经过你确认</b>，可以改或删。
        </span>
        <button className="tbtn" onClick={() => void load()} disabled={busy} style={{ marginLeft: "auto" }}>
          {busy ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />} 刷新
        </button>
      </div>

      <div className="mem-tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={`mem-tab${view === t.key ? " on" : ""}`}
            onClick={() => setView(t.key)}
          >
            {t.label}
            {t.badge ? <span className="mem-badge">{t.badge}</span> : null}
          </button>
        ))}
      </div>

      {msg && <div className="mem-msg" onClick={() => setMsg("")}>{msg}</div>}

      {view === "pending" && (
        <div className="mem-body">
          {!pending.length && (
            <div className="mem-empty">
              <CheckCircle2 size={18} /> 没有待确认的推断。
              <div className="mem-sub">
                agent 从你们的对话里总结出规律时，会先放在这里等你点头，不会直接当成事实。
              </div>
            </div>
          )}
          {pending.map((e) => (
            <div className="mem-card" key={`${e.category}/${e.name}`}>
              <div className="mem-card-h">
                <strong>{e.name}</strong>
                <span className="mem-tag">{CATEGORY_LABEL[e.category] || e.category}</span>
                {typeof e.sightings === "number" && (
                  <span className="mem-tag" title="跨了几次整理仍然得出同一个结论">
                    第 {e.sightings}/{e.promote_after} 次观察
                  </span>
                )}
              </div>
              <div className="mem-desc">{e.description}</div>
              <pre className="mem-pre">{e.body}</pre>
              {e.evidence && <div className="mem-sub">依据：{e.evidence}</div>}
              {isAdmin && (
                <div className="mem-actions">
                  <button className="tbtn primary" disabled={busy} onClick={() => void decide(e.name, "confirm")}>
                    <CheckCircle2 size={14} /> 确认，这是对的
                  </button>
                  <button className="tbtn" disabled={busy} onClick={() => void decide(e.name, "reject")}>
                    <XCircle size={14} /> 不对，驳回
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {view === "core" && (
        <div className="mem-body">
          <div className="mem-sub">
            这两份<b>每一轮对话都在</b> agent 的上下文里，不需要检索它就一直知道。
            所以要短、要只写长期为真的事。
          </div>
          {core.map((b) => {
            const text = coreDraft[b.block] ?? "";
            const over = text.length > (stats?.core?.[b.block]?.limit || 4000);
            return (
              <div className="mem-card" key={b.block}>
                <div className="mem-card-h">
                  <strong>{b.file}</strong>
                  <span className="mem-tag">{b.hint}</span>
                  <span className={`mem-sub${over ? " bad" : ""}`} style={{ marginLeft: "auto" }}>
                    {text.length}/{stats?.core?.[b.block]?.limit ?? 4000} 字
                  </span>
                </div>
                <textarea
                  className="mem-textarea"
                  value={text}
                  readOnly={!isAdmin}
                  onChange={(ev) => setCoreDraft((d) => ({ ...d, [b.block]: ev.target.value }))}
                  placeholder={b.block === "user"
                    ? "例：我叫 …；汇报一律用中文；未经我批准绝不发版。"
                    : "例：目标 ACoS 25%；品牌词永远不否；单次调价不超过 15%。"}
                />
                {isAdmin && (
                  <div className="mem-actions">
                    <button className="tbtn primary" disabled={busy || over} onClick={() => void saveCore(b.block)}>
                      保存
                    </button>
                    <button className="tbtn" disabled={busy}
                            onClick={() => setCoreDraft((d) => ({ ...d, [b.block]: b.text }))}>
                      撤销改动
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {view === "entries" && (
        <div className="mem-body">
          <input className="mem-search" value={q} placeholder="搜记忆（名字 / 描述 / 关键词）"
                 onChange={(e) => setQ(e.target.value)} />
          {!shown.length && <div className="mem-empty"><Layers size={18} /> 没有匹配的记忆。</div>}
          {shown.map((e) => (
            <div className="mem-row" key={`${e.category}/${e.name}`}>
              <button className="mem-row-main" onClick={() => void openDetail(e)}>
                <div className="mem-row-t">
                  <strong>{e.name}</strong>
                  <span className="mem-tag">{CATEGORY_LABEL[e.category] || e.category}</span>
                  <SourceTag entry={e} />
                  {e.decay && e.decay.in_index === false && (
                    <span className="mem-tag" title="它已退出常驻目录以省 token，但仍然能被检索到">
                      不常用
                    </span>
                  )}
                  {e.valid === false && <span className="mem-tag">已过期</span>}
                </div>
                <div className="mem-desc">{e.description}</div>
              </button>
              <span className="mem-sub">{e.updated}</span>
              {isAdmin && (
                <button className="tbtn danger" title="删除" disabled={busy} onClick={() => void removeEntry(e)}>
                  <Trash2 size={14} />
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {view === "stats" && stats && (
        <div className="mem-body">
          <div className="mem-grid">
            <div className="mem-stat"><b>{stats.store.total}</b><span>条分类记忆</span></div>
            <div className="mem-stat"><b>{stats.store.index_chars}</b><span>字常驻目录</span></div>
            <div className="mem-stat"><b>{stats.episodes?.indexed ?? 0}</b><span>条对话记录</span></div>
            <div className="mem-stat"><b>{pending.length}</b><span>条待确认</span></div>
          </div>
          <div className="mem-card">
            <div className="mem-card-h"><Clock3 size={14} /><strong>整理（把零散经历提炼成长期记忆）</strong></div>
            <div className="mem-sub">
              上次整理：{stats.reflect.last_reflect} ·
              攒了 {stats.reflect.pending_episodes}/{stats.reflect.threshold} 条新经历
              {stats.running ? " · 正在整理…" : ""}
            </div>
            {isAdmin && (
              <div className="mem-actions">
                <button className="tbtn primary" disabled={busy || stats.running} onClick={() => void runReflect()}>
                  <Sparkles size={14} /> 立即整理
                </button>
              </div>
            )}
            <div className="mem-sub">整理出来的新结论会进「待确认」，不会直接生效。</div>
          </div>
          <div className="mem-sub">记忆目录：{stats.store.dir}</div>
        </div>
      )}

      {detail && (
        <div className="mem-modal" onClick={() => setDetail(null)}>
          <div className="mem-modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="mem-card-h">
              <strong>{detail.name}</strong>
              <span className="mem-tag">{CATEGORY_LABEL[detail.category] || detail.category}</span>
              <SourceTag entry={detail} />
              <button className="tbtn" style={{ marginLeft: "auto" }} onClick={() => setDetail(null)}>关闭</button>
            </div>
            <div className="mem-desc">{detail.description}</div>
            <pre className="mem-pre">{detail.body}</pre>
            <div className="mem-sub">
              {detail.created} → {detail.updated}
              {detail.scope ? ` · 作用域 ${detail.scope}` : ""}
              {detail.history_count ? ` · ${detail.history_count} 个历史版本` : ""}
            </div>
            {detail.evidence && <div className="mem-sub">依据：{detail.evidence}</div>}
            {!!detail.backlinks?.length && (
              <div className="mem-sub">被这些记忆引用：{detail.backlinks.join("、")}</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
