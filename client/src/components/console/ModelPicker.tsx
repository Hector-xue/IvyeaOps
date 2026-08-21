/**
 * 任务台输入框上那枚模型芯片 —— 点一下就换主脑。
 *
 * ── 为什么是"本会话"而不是"全局" ──────────────────────────────────────────
 * agent 的主脑本来是一个全局设置。要是点一下芯片就去改它，IvyeaOps 的其他用户、
 * 正在跑的定时任务会跟着一起换掉模型 —— 一个人在输入框里随手试个模型不该有这种
 * 连带。所以这里选的模型是**逐轮下发**的（chat payload 的 model 字段），只影响
 * 这台浏览器的这条会话。想真的改全局有另一条明路：面板底部的「设为默认」。
 *
 * ── 为什么不复用 SheetSelect ──────────────────────────────────────────────
 * 那是个扁平列表。中转商一家就能列出 90+ 个模型，没有搜索和分组的话，找一个模型
 * 要滚十几屏。
 */
import {
  useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState,
  type CSSProperties,
} from "react";
import { createPortal } from "react-dom";
import Icon from "../Icon";
import {
  ivyeaModelProviders, ivyeaProviderModels, providerKeyReady,
  type IvyeaProvider,
} from "../../api/ivyeaAgent";

/** `provider:model`。agent 的 models.by_id 认这个形态。 */
export function modelId(provider: string, model: string): string {
  return `${provider}:${model}`;
}

export function splitModelId(id: string): { provider: string; model: string } {
  const i = String(id || "").indexOf(":");
  if (i < 0) return { provider: "", model: String(id || "") };
  return { provider: id.slice(0, i), model: id.slice(i + 1) };
}

type Row = {
  id: string;
  provider: string;
  providerLabel: string;
  model: string;
  ready: boolean;
  keyEnv: string;
};

/**
 * provider 清单每次开面板都拉一遍太浪费（23 家 + 能力矩阵），进程内存一份。
 *
 * 但**不能一直用**：用户在这个面板里看到"未配置密钥"，点过去填好 key 再回来，
 * 缓存还说没配 —— 那是个死胡同。所以带一分钟保质期，过期就在后台重取（列表照旧
 * 先用旧的渲染，不闪一下空白）。
 */
let providerCache: IvyeaProvider[] | null = null;
let providerCacheAt = 0;
const PROVIDER_TTL_MS = 60_000;

function keyEnvOf(row: IvyeaProvider): string {
  const s = String(row.key_status || "");
  const i = s.indexOf(":");
  return i >= 0 ? s.slice(i + 1) : "";
}

export default function ModelPicker({
  currentLabel,
  value,
  onChange,
  switchable,
  onOpenSettings,
  onSetDefault,
  openSignal = 0,
  disabled,
}: {
  /** 当前**实际生效**的模型显示名（来自 /health 或本轮 start 事件）。 */
  currentLabel: string;
  /** 本会话选中的模型 id；"" = 跟随全局。 */
  value: string;
  onChange: (id: string) => void;
  /**
   * agent 支持按轮次切模型吗（≥ v1.15.4）。
   * 老 agent 会**忽略** model 字段 —— 那种情况下给个下拉框就是个假开关：
   * 用户选了别的模型，跑的还是老模型，且毫无提示。所以老版本只留「去系统配置」。
   */
  switchable: boolean;
  onOpenSettings: () => void;
  /** 把某个模型写成全局默认（写 ops 的系统配置再下推给 agent）。 */
  onSetDefault?: (id: string) => Promise<void>;
  /** 外部要求打开面板（`/model` 命令）。数字变化即触发。 */
  openSignal?: number;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<CSSProperties>({});
  const [providers, setProviders] = useState<IvyeaProvider[]>(providerCache || []);
  const [loading, setLoading] = useState(false);
  const [loadErr, setLoadErr] = useState("");
  /** 某家 provider 现拉到的实时清单，覆盖内置那份。 */
  const [live, setLive] = useState<Record<string, string[]>>({});
  const [refreshing, setRefreshing] = useState("");
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  /** 「设为默认」的二次确认：全局的事不该点一下就生效。 */
  const [confirmDefault, setConfirmDefault] = useState("");
  const [savingDefault, setSavingDefault] = useState(false);

  const wrapRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // ── 定位：portal 到 body + fixed，免得被输入框那一层 overflow 裁掉 ────────
  const computePos = useCallback(() => {
    const el = wrapRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const vh = window.innerHeight;
    const width = Math.min(380, Math.max(280, window.innerWidth - 24));
    const spaceAbove = r.top;
    const spaceBelow = vh - r.bottom;
    // 输入框在页面底部，面板绝大多数时候要向上开。
    const openUp = spaceAbove > spaceBelow;
    // **高度要按那个方向剩下多少来定。** 一律给 440 的话，空会话态（输入框在页面
    // 正中）往上只剩 430 多，面板顶部会被切到视口外面去 —— 搜索框整条看不见。
    const maxH = Math.max(200, Math.min(440, (openUp ? spaceAbove : spaceBelow) - 12));
    const left = Math.max(12, Math.min(Math.round(r.right - width), window.innerWidth - width - 12));
    const s: CSSProperties = { position: "fixed", left, width, maxHeight: maxH };
    if (openUp) { s.bottom = Math.round(vh - r.top + 6); s.top = "auto"; }
    else { s.top = Math.round(r.bottom + 6); s.bottom = "auto"; }
    setPos(s);
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    computePos();
    const on = () => computePos();
    window.addEventListener("scroll", on, true);
    window.addEventListener("resize", on);
    return () => {
      window.removeEventListener("scroll", on, true);
      window.removeEventListener("resize", on);
    };
  }, [open, computePos]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!wrapRef.current?.contains(t) && !panelRef.current?.contains(t)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // `/model` 命令：外部信号打开面板。
  useEffect(() => {
    if (openSignal > 0) setOpen(true);
  }, [openSignal]);

  // ── provider 清单 ────────────────────────────────────────────────────────
  /*
   * **别把 loading 放进 effect 的依赖里，也别用 alive 标志守回调。**
   * 曾经这么写过，结果是面板永远停在"正在取模型清单…"：
   *   effect 里 setLoading(true) → 依赖变了 → React 先跑 cleanup（alive=false）
   *   → 请求回来时 setProviders / setLoading(false) 全被那个标志挡掉。
   * 假后端在微任务里就 resolve，抢在重渲染前面，所以验证台一路绿灯；真实网络
   * 有几十毫秒延迟，顺序反过来就死锁。取数据放事件驱动的函数里，用 ref 防重入。
   */
  const fetchingRef = useRef(false);

  const loadProviders = useCallback(async (force: boolean) => {
    if (fetchingRef.current) return;
    const fresh = !!providerCache && Date.now() - providerCacheAt < PROVIDER_TTL_MS;
    if (!force && fresh) return;
    fetchingRef.current = true;
    setLoading(true);
    setLoadErr("");
    try {
      const d = await ivyeaModelProviders();
      const rows = d?.providers || [];
      providerCache = rows;
      providerCacheAt = Date.now();
      setProviders(rows);
    } catch (e: any) {
      // 已经有一份（哪怕过期）就别把错误摆出来：列表照常能用，
      // 弹一句"取模型清单失败"只会让人以为面板坏了。
      if (!providerCache?.length) {
        setLoadErr(e?.response?.data?.detail || e?.message || "取模型清单失败");
      }
    } finally {
      fetchingRef.current = false;
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) void loadProviders(false);
  }, [open, loadProviders]);

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 30);
    else { setQuery(""); setActive(0); setConfirmDefault(""); }
  }, [open]);

  const refreshProvider = async (pid: string) => {
    setRefreshing(pid);
    try {
      const d = await ivyeaProviderModels(pid, true);
      setLive((m) => ({ ...m, [pid]: d?.catalog?.models || [] }));
    } catch {
      // 拉不到就继续用内置清单 —— 面板绝不能因为一次取清单失败而变成空的。
    } finally {
      setRefreshing("");
    }
  };

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const ready: { p: IvyeaProvider; rows: Row[] }[] = [];
    const notReady: IvyeaProvider[] = [];
    for (const p of providers) {
      const ok = providerKeyReady(String(p.key_status || ""));
      const plabel = String(p.label || p.id);
      if (!ok) {
        if (!q || plabel.toLowerCase().includes(q) || p.id.includes(q)) notReady.push(p);
        continue;
      }
      const models = live[p.id] || p.models || [];
      const rows: Row[] = models
        .map((m) => ({
          id: modelId(p.id, m), provider: p.id, providerLabel: plabel,
          model: m, ready: true, keyEnv: keyEnvOf(p),
        }))
        .filter((r) => !q || `${r.providerLabel} ${r.model}`.toLowerCase().includes(q));
      if (rows.length || (!q && models.length === 0)) ready.push({ p, rows });
    }
    return { ready, notReady };
  }, [providers, live, query]);

  /** 扁平化后的可选行，供上下键走位。 */
  const flat = useMemo(() => groups.ready.flatMap((g) => g.rows), [groups]);

  useEffect(() => { setActive(0); }, [query]);

  const pick = (id: string) => {
    onChange(id);
    setOpen(false);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => Math.min(i + 1, flat.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)); }
    else if (e.key === "Enter") {
      e.preventDefault();
      if (flat[active]) pick(flat[active].id);
    }
  };

  const chosen = value ? splitModelId(value) : null;
  const chosenProvider = chosen ? providers.find((p) => p.id === chosen.provider) : undefined;
  const label = value ? (chosen?.model || value) : (currentLabel || "模型未配置");

  const doSetDefault = async () => {
    if (!value || !onSetDefault) return;
    if (confirmDefault !== value) { setConfirmDefault(value); return; }
    setSavingDefault(true);
    try {
      await onSetDefault(value);
      setConfirmDefault("");
      setOpen(false);
    } finally {
      setSavingDefault(false);
    }
  };

  return (
    <div ref={wrapRef} className="mp-wrap">
      <button
        type="button"
        className={"cc-chip cc-chip-model" + (value ? " mp-overridden" : "")}
        onClick={() => { if (!disabled) (switchable ? setOpen((o) => !o) : onOpenSettings()); }}
        title={switchable
          ? "本轮主脑 —— 点击切换（只影响这条会话）"
          : "当前主脑模型 · 点击去「系统配置」切换（当前 IvyeaAgent 版本不支持按会话切换）"}
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={disabled}
      >
        <Icon name="model" size={14} />
        <span className="cc-chip-label">{label}</span>
        {switchable && <span className="mp-caret" aria-hidden>{open ? "▴" : "▾"}</span>}
      </button>

      {open && createPortal(
        <div ref={panelRef} className="mp-panel" style={pos} role="listbox" onKeyDown={onKeyDown}>
          <div className="mp-head">
            <Icon name="search" size={13} />
            <input
              ref={inputRef}
              className="mp-search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜模型或厂商…"
              spellCheck={false}
            />
            <button type="button" className="mp-x" onClick={() => setOpen(false)} title="关闭">
              <Icon name="close" size={13} />
            </button>
          </div>

          <div className="mp-body">
            {/* 跟随全局永远排第一：它是"回到默认"的那条路，找不到它的人只能重开页面。 */}
            <button
              type="button"
              className={"mp-row mp-row-global" + (value ? "" : " active")}
              onClick={() => pick("")}
            >
              <span className="mp-row-name">跟随全局主脑</span>
              <span className="mp-row-sub">{currentLabel || "未配置"}</span>
              {!value && <span className="mp-check" aria-hidden>✓</span>}
            </button>

            {loading && providers.length === 0 && <div className="mp-note">正在取模型清单…</div>}
            {!loading && loadErr && (
              <div className="mp-note mp-note-err">
                {loadErr}
                <button type="button" className="mp-link" onClick={onOpenSettings}>去系统配置</button>
              </div>
            )}

            {groups.ready.map((g) => (
              <div key={g.p.id} className="mp-group">
                <div className="mp-group-hd">
                  <span>{g.p.label || g.p.id}</span>
                  <button
                    type="button"
                    className="mp-refresh"
                    title="重新拉取这家的模型清单"
                    disabled={refreshing === g.p.id}
                    onClick={(e) => { e.stopPropagation(); void refreshProvider(g.p.id); }}
                  >
                    {refreshing === g.p.id ? "…" : <Icon name="regenerate" size={12} />}
                  </button>
                </div>
                {g.rows.length === 0 && (
                  <div className="mp-note">没有内置清单，点上面的刷新去拉一次。</div>
                )}
                {g.rows.map((r) => {
                  const idx = flat.indexOf(r);
                  return (
                    <button
                      key={r.id}
                      type="button"
                      className={"mp-row" + (r.id === value ? " active" : "") + (idx === active ? " cursor" : "")}
                      onClick={() => pick(r.id)}
                      onMouseEnter={() => setActive(idx)}
                    >
                      <span className="mp-row-name">{r.model}</span>
                      {r.id === value && <span className="mp-check" aria-hidden>✓</span>}
                    </button>
                  );
                })}
              </div>
            ))}

            {groups.notReady.length > 0 && (
              <div className="mp-group mp-group-off">
                <div className="mp-group-hd"><span>未配置密钥</span></div>
                {groups.notReady.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    className="mp-row mp-row-off"
                    onClick={onOpenSettings}
                    title={`去系统配置填 ${keyEnvOf(p) || "密钥"}`}
                  >
                    <span className="mp-row-name">{p.label || p.id}</span>
                    <span className="mp-row-sub">{keyEnvOf(p) || "需登录授权"}</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="mp-foot">
            <button type="button" className="mp-link" onClick={onOpenSettings}>
              <Icon name="settings" size={12} /> 系统配置
            </button>
            {value && onSetDefault && (
              <button
                type="button"
                className={"mp-default" + (confirmDefault === value ? " confirm" : "")}
                onClick={() => void doSetDefault()}
                disabled={savingDefault || !!(chosenProvider && !providerKeyReady(String(chosenProvider.key_status || "")))}
                title="把这个模型写成全局默认（对所有用户和定时任务生效）"
              >
                {savingDefault ? "保存中…"
                  : confirmDefault === value ? "确定？全局生效" : "设为默认"}
              </button>
            )}
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}
