import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import KeywordMonitor from "./home/KeywordMonitor";
import AsinMonitor from "./home/AsinMonitor";
import AlertStrip from "./home/AlertStrip";
import CategoryWatch from "./home/CategoryWatch";
import MarketTraffic from "./home/MarketTraffic";
import { LingXingProvider, useLingXing } from "./home/lingxingContext";
import { getDataSource, setDataSource, dataSourceMeta, type DataSourceId } from "../../lib/dataSource";
import DataSourcePicker from "../../components/DataSourcePicker";
import SheetSelect from "../../components/SheetSelect";
import AppDialog from "../../components/AppDialog";
import { FLAG_URL } from "../../lib/marketplaces";
import { ToastProvider } from "../../components/toast";
import { Chip, inputStyle } from "./lingxingUi";
import { useAuth } from "../../App";

/*
 * 运营驾驶舱 —— 市场侧（看别人）与经营侧（看自己）合在一个板块里。
 *
 * 经营侧那几个 tab 原本是独立的「领星 ERP」板块。合并的理由是它们本来就是同一件事
 * 被切成了两半：驾驶舱负责"看"，领星负责"数据从哪来 + 怎么落地"。最刺眼的一处是
 * 工单 —— 在广告看板点「调预算」生成的工单，此前必须切到另一个板块才能确认，
 * 而两个板块之间连一个跳转都没有。
 *
 * 两件必须守住的事：
 *
 * 1. **经营侧一律 lazy。** 这个文件是 eager 的（App.tsx 里 Home 属于 shell，
 *    和 Login/Setup 一起进首屏包）。领星那几个组件加起来三千多行，直接 import
 *    会让每个人 —— 包括永远看不到这些 tab 的非管理员 —— 都下载一遍。
 * 2. **经营侧的 provider 只包管理员。** 非管理员看不到那几个 tab，也就不该有人
 *    替他们去打管理员专属接口（后端会 403，前端不该主动撞上去）。
 */

// 经营侧：全部 lazy，理由见上。
const PromoCalendar = lazy(() => import("./home/PromoCalendar"));
const AdsBoard = lazy(() => import("./home/AdsBoard"));
const LingXingSuggest = lazy(() => import("./LingXingSuggest"));
const LingXingOperate = lazy(() => import("./LingXingOperate"));
const LingXingBrowse = lazy(() => import("./LingXingBrowse"));
const LingXingAudit = lazy(() => import("./LingXingAudit"));
const LingXingConfig = lazy(() => import("./LingXingConfig"));

const STORAGE_MKT = "ivyea-ops-pulse-marketplace";
const STORAGE_TAB = "ivyea-ops-home-tab";
const STORAGE_DATASET = "ivyea-ops-home-dataset";
const MARKETPLACES = [
  { code: "US", name: "美国" }, { code: "UK", name: "英国" },
  { code: "DE", name: "德国" }, { code: "JP", name: "日本" },
  { code: "CA", name: "加拿大" }, { code: "FR", name: "法国" },
  { code: "AU", name: "澳大利亚" }, { code: "IT", name: "意大利" },
];

type HomeTab =
  | "market" | "keyword" | "competitor" | "own" | "category"   // 市场侧
  | "promo" | "ads" | "suggest" | "tickets";                    // 经营侧
type TabGroup = "market" | "ops";

/** 齿轮里的三块。低频、工具性，不该和「广告看板」抢主 tab 行。 */
type ToolPanel = "browse" | "audit" | "config";
const TOOL_PANELS: [ToolPanel, string][] = [
  ["browse", "数据浏览"], ["audit", "审计"], ["config", "配置"],
];

// group:"ops" 的读的是自家店铺经营数据（领星），且广告看板能发起真会改线上投放的
// 调整 —— 非管理员不显示，后端 /api/cockpit 与 /api/lingxing 也有真闸。
const TABS: { key: HomeTab; label: string; icon: string; group: TabGroup }[] = [
  { key: "market", label: "大盘流量", icon: "↗", group: "market" },
  { key: "keyword", label: "关键词", icon: "◈", group: "market" },
  { key: "competitor", label: "竞品监控", icon: "⊞", group: "market" },
  { key: "own", label: "自有 ASIN", icon: "★", group: "market" },
  { key: "category", label: "类目大盘", icon: "☰", group: "market" },
  { key: "promo", label: "促销日历", icon: "◷", group: "ops" },
  { key: "ads", label: "广告看板", icon: "◎", group: "ops" },
  { key: "suggest", label: "优化建议", icon: "◆", group: "ops" },
  { key: "tickets", label: "工单", icon: "▤", group: "ops" },
];
const TAB_KEYS = new Set(TABS.map((t) => t.key));

/** 促销/广告这两块自带 .cp-page（内部已有滚动与内边距）；领星那几块是普通文档流。 */
const SELF_SCROLLING: HomeTab[] = ["promo", "ads"];

// Shown when the chosen data source has no backend wired yet.
function DataSourcePlaceholder({ name }: { name: string }) {
  return (
    <div className="pulse-onboard" style={{ textAlign: "center", padding: "48px 24px" }}>
      <div className="pulse-onboard-title">数据源「{name}」即将支持</div>
      <div className="pulse-onboard-desc" style={{ marginTop: 8 }}>
        当前仅 <b>Sorftime</b> 已接入。{name} 的数据客户端还在开发中——
        在「系统配置 → 数据源」填好 {name} 密钥后，接入完成即可在此切换使用。
      </div>
    </div>
  );
}

function PanelFallback({ what }: { what: string }) {
  return <div className="cp-loading" style={{ padding: "40px 20px", textAlign: "center" }}>正在加载{what}…</div>;
}

/* ── 经营侧：状态条 / 徽标 / 内容（都在 LingXingProvider 里） ──────────────── */

/** 顶栏在经营组时显示的那一条：接没接上、写开关开没开、当前哪家店。 */
function OpsContextBar({ onOpenConfig }: { onOpenConfig: () => void }) {
  const { status, sellers, storeSid, setStoreSid, enableMaster } = useLingXing();
  return (
    <div className="home-ops-bar" data-tour="home-ops-status">
      <Chip on={!!status?.openapi_configured} label={status?.openapi_configured ? "OpenAPI 已配置" : "未配置凭证"} />
      <Chip on={!!status?.master_enabled} label={status?.master_enabled ? "数据已启用" : "数据未启用"} />
      <Chip on={!!status?.operate_active} label={status?.operate_active ? "操作开关：开" : "操作开关：关(只读)"} warn={!!status?.operate_active} />
      {status && !status.master_enabled && (
        <button className="cp-btn primary" onClick={() => { void enableMaster(); }}>启用领星数据(只读)</button>
      )}
      {status?.master_enabled && (
        <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: "var(--fs-11)", color: "var(--t3)" }}>店铺</span>
          <SheetSelect value={storeSid} onChange={setStoreSid} title="选择店铺" placeholder="（加载中/无）"
            style={{ ...inputStyle, minWidth: 160 }}
            options={sellers.map((s) => ({ value: String(s.sid), label: String(s.name || s.sid), sub: String(s.sid) }))} />
        </span>
      )}
      <button className="cp-btn" onClick={onOpenConfig} title="数据浏览 / 审计 / 配置">⚙ 领星工具</button>
    </div>
  );
}

/** 工单 tab 上的待确认徽标。**常驻**：不进工单页也得知道有几张等着。 */
function TicketBadge() {
  const { status } = useLingXing();
  const pending = status?.ticket_counts?.awaiting_human || 0;
  const reviewing = status?.ticket_counts?.reviewing || 0;
  if (pending > 0) return <span className="lx-badge" title={`${pending} 张工单待确认`}>{pending}</span>;
  if (reviewing > 0) return <span className="lx-badge lx-badge-soft" title={`${reviewing} 张工单复核中`}>{reviewing}</span>;
  return null;
}

function OpsTabBody({ tab, onOpenConfig, focusTicket, onFocusConsumed, onGoTickets }: {
  tab: HomeTab;
  onOpenConfig: () => void;
  focusTicket: string;
  onFocusConsumed: () => void;
  onGoTickets: (id?: string) => void;
}) {
  const { status, loading, error, storeSid } = useLingXing();

  if (loading) return <PanelFallback what="领星状态" />;
  if (error) {
    return (
      <div className="cp-empty">
        <div className="cp-empty-title">读不到领星状态</div>
        <div className="cp-empty-desc">{error}（后端可能未重启，新接口未生效）</div>
      </div>
    );
  }
  // 促销/广告自己会用 LingXingGate 判断并给出去处；建议/工单没有那层，在这里兜住。
  if (!status?.master_enabled && (tab === "suggest" || tab === "tickets")) {
    return (
      <div className="cp-empty">
        <div className="cp-empty-title">还没接上领星</div>
        <div className="cp-empty-desc">
          先填好 OpenAPI 凭证、测试连接，再打开数据总开关。
          <br />写操作另有独立的「操作开关」+ 三重复核，默认全关。
        </div>
        <div style={{ marginTop: 12 }}>
          <button className="cp-btn primary" onClick={onOpenConfig}>去配置领星</button>
        </div>
      </div>
    );
  }

  return (
    <Suspense fallback={<PanelFallback what="面板" />}>
      {tab === "promo" && <PromoCalendar />}
      {tab === "ads" && <AdsBoard />}
      {tab === "suggest" && <LingXingSuggest storeSid={storeSid} onGoTickets={onGoTickets} />}
      {tab === "tickets" && (
        <LingXingOperate focusTicket={focusTicket} onFocusConsumed={onFocusConsumed} />
      )}
    </Suspense>
  );
}

/** 齿轮对话框：数据浏览 / 审计 / 配置。 */
function LingXingToolsDialog({ panel, setPanel, onClose }: {
  panel: ToolPanel; setPanel: (p: ToolPanel) => void; onClose: () => void;
}) {
  const { datasets, storeSid } = useLingXing();
  const [dataset, setDataset] = useState<string>(
    () => localStorage.getItem(STORAGE_DATASET) || "sellers");
  useEffect(() => { localStorage.setItem(STORAGE_DATASET, dataset); }, [dataset]);

  return (
    <AppDialog
      title="领星工具"
      icon="⚙"
      onClose={onClose}
      nav={
        <nav className="app-dialog-nav-list">
          {TOOL_PANELS.map(([id, label]) => (
            <button key={id} className={"app-dialog-nav-item" + (panel === id ? " active" : "")}
                    onClick={() => setPanel(id)}>{label}</button>
          ))}
        </nav>
      }
    >
      <Suspense fallback={<div className="app-dialog-loading">加载中…</div>}>
        {panel === "browse" && (
          <LingXingBrowse datasets={datasets} active={dataset} setActive={setDataset} storeSid={storeSid} />
        )}
        {panel === "audit" && <LingXingAudit />}
        {panel === "config" && <LingXingConfig />}
      </Suspense>
    </AppDialog>
  );
}

/* ── 板块本体 ─────────────────────────────────────────────────────────────── */

export default function Home() {
  const { role } = useAuth();
  const isAdmin = role === "admin";
  return (
    <ToastProvider>
      {isAdmin ? <LingXingProvider><Cockpit isAdmin /></LingXingProvider> : <Cockpit isAdmin={false} />}
    </ToastProvider>
  );
}

function Cockpit({ isAdmin }: { isAdmin: boolean }) {
  const [params, setParams] = useSearchParams();
  const [marketplace, setMarketplace] = useState(() => localStorage.getItem(STORAGE_MKT) || "US");
  const visibleTabs = useMemo(
    () => TABS.filter((t) => isAdmin || t.group === "market"), [isAdmin]);

  // **初始值就要过权限**，不能只靠下面那个 useEffect 纠正：effect 在首次渲染**之后**
  // 才跑，而经营组的组件在渲染时就会 useLingXing() —— 非管理员没有 provider，
  // 于是整个板块直接被 ErrorBoundary 接走，白屏。降权的老用户 localStorage 里
  // 正好存着 "ads" 就会踩到。
  const [tab, setTab] = useState<HomeTab>(() => {
    const ok = (v: string | null): v is HomeTab =>
      !!v && TAB_KEYS.has(v as HomeTab)
      && (isAdmin || TABS.find((t) => t.key === v)?.group === "market");
    const fromUrl = params.get("tab");
    if (ok(fromUrl)) return fromUrl;
    const saved = localStorage.getItem(STORAGE_TAB);
    return ok(saved) ? saved : "market";
  });
  const [dataSource, setDataSourceState] = useState<DataSourceId>(getDataSource);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [alertReloadKey, setAlertReloadKey] = useState(0);
  const [focusTicket, setFocusTicket] = useState("");
  // 齿轮对话框。深链 ?panel=config 会直接把它打开（领星板块的老书签就落在这儿）。
  const [toolPanel, setToolPanel] = useState<ToolPanel | null>(() => {
    const p = params.get("panel");
    return isAdmin && (p === "browse" || p === "audit" || p === "config") ? p : null;
  });
  const pickerRef = useRef<HTMLDivElement>(null);

  // isAdmin 是第二道闸：上面的初始值已经过了权限，但 tab 还能被 setTab 改动，
  // 而"经营组"这个判断的下游是**要不要调 useLingXing**，错一次就是白屏。
  const group: TabGroup = TABS.find((t) => t.key === tab)?.group ?? "market";
  const isOps = isAdmin && group === "ops";

  // Persist + reload all data when the source changes (the tab body remounts
  // via its key, and AlertStrip re-fetches via reloadKey).
  const changeDataSource = (id: DataSourceId) => {
    if (id === dataSource) return;
    setDataSource(id);
    setDataSourceState(id);
    setAlertReloadKey((k) => k + 1);
  };
  const dsReady = dataSourceMeta(dataSource, "home").ready;

  useEffect(() => { localStorage.setItem(STORAGE_MKT, marketplace); }, [marketplace]);

  // tab 同时写进 localStorage 和 URL：前者记住"上次停在哪"，后者让深链和
  // 「在工单里看」这类跳转可以被分享、可以后退。
  useEffect(() => {
    localStorage.setItem(STORAGE_TAB, tab);
    if (params.get("tab") !== tab) {
      const next = new URLSearchParams(params);
      next.set("tab", tab);
      setParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  // 上次停在管理员标签、这次换成普通用户登录时，别让他停在一个只会 403 的页面上。
  useEffect(() => {
    if (!visibleTabs.some((t) => t.key === tab)) setTab(visibleTabs[0]?.key ?? "market");
  }, [visibleTabs, tab]);

  useEffect(() => {
    if (!pickerOpen) return;
    const handler = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) setPickerOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [pickerOpen]);

  const today = new Date().toLocaleDateString("zh-CN", { month: "long", day: "numeric", weekday: "short" });
  const currentMkt = MARKETPLACES.find((m) => m.code === marketplace) ?? MARKETPLACES[0];
  const openConfig = () => setToolPanel("config");

  return (
    <div className="home-cockpit">
      {/* ── Top bar: title + date + 随组切换的上下文 ── */}
      <div className="home-topbar">
        <span className="home-title">
          <span style={{ color: "var(--acc)" }}>◧</span> 运营驾驶舱
          <span className="home-date">{today}</span>
        </span>
        {/* 站点/数据源只对市场侧有意义（经营侧按**店铺**取数），所以这一条随组换掉，
            而不是把两套控件并排堆在一起让人猜哪个管哪边。 */}
        {isOps ? (
          <OpsContextBar onOpenConfig={openConfig} />
        ) : (
          <div data-tour="home-source" style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <DataSourcePicker value={dataSource} onChange={changeDataSource} surface="home" />
            <div className="market-mkt-wrap" ref={pickerRef}>
              <button className="market-mkt-btn" onClick={() => setPickerOpen((o) => !o)} title="选择站点">
                <span className="market-mkt-flag"><img src={FLAG_URL(currentMkt.code)} alt={currentMkt.code} style={{ width: 16, height: 12, verticalAlign: "middle" }} /></span>
                <span className="market-mkt-code">{currentMkt.code}</span>
                <span className="market-mkt-arrow">{pickerOpen ? "▴" : "▾"}</span>
              </button>
              {pickerOpen && (
                <div className="market-mkt-dropdown hide-mobile-picker">
                  {MARKETPLACES.map((m) => (
                    <button
                      key={m.code}
                      className={"market-mkt-option" + (marketplace === m.code ? " active" : "")}
                      onClick={() => { setMarketplace(m.code); setPickerOpen(false); }}
                    >
                      <span><img src={FLAG_URL(m.code)} alt={m.code} style={{ width: 16, height: 12, verticalAlign: "middle" }} /></span>
                      <span className="market-mkt-option-code">{m.code}</span>
                      <span className="market-mkt-option-name">{m.name}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* ── Alert strip：市场侧的 ASIN 异动。经营组不显示 —— 它跳转的目标
           （竞品/自有 ASIN）都在市场组，挂在广告看板上方只会把人弹走。 ── */}
      {!isOps && dsReady && (
        <AlertStrip
          reloadKey={alertReloadKey}
          dataSource={dataSource}
          marketplace={marketplace}
          onJump={(kind, mkt) => { setMarketplace(mkt); setTab(kind); }}
        />
      )}

      {/* ── Tabs：两组，中间一条带字的分隔。分组标签不是装饰 ——
           「大盘流量」和「广告看板」一个看别人一个看自己，混排会让人选错。 ── */}
      <div className="home-tabs">
        <span className="home-tab-group">市场</span>
        {visibleTabs.filter((t) => t.group === "market").map((t) => (
          <button key={t.key} className={"home-tab" + (tab === t.key ? " active" : "")}
                  onClick={() => setTab(t.key)}>
            <span className="home-tab-icon">{t.icon}</span>
            <span className="home-tab-label">{t.label}</span>
          </button>
        ))}
        {isAdmin && (
          <>
            <span className="home-tab-group home-tab-group-sep">自家店铺</span>
            {visibleTabs.filter((t) => t.group === "ops").map((t) => (
              <button key={t.key} className={"home-tab" + (tab === t.key ? " active" : "")}
                      onClick={() => setTab(t.key)}>
                <span className="home-tab-icon">{t.icon}</span>
                <span className="home-tab-label">{t.label}</span>
                {t.key === "tickets" && <TicketBadge />}
              </button>
            ))}
          </>
        )}
      </div>

      {/* ── Tab body ──
           市场侧按 数据源/站点 重挂载（换源要重新取数，也防上一个站点的在途请求
           串进新站点）；经营侧按**店铺**取数，不吃这两个 key。 */}
      <div className="home-tab-body wb-enter"
           key={isOps ? tab : tab + ":" + dataSource + ":" + marketplace}>
        {isOps ? (
          // 领星那几块是普通文档流，塞进 .home-cockpit（height:100%;overflow:hidden）
          // 会撑破外壳；促销/广告自带 .cp-page，自己会滚。
          SELF_SCROLLING.includes(tab) ? (
            <OpsTabBody tab={tab} onOpenConfig={openConfig} focusTicket={focusTicket}
                        onFocusConsumed={() => setFocusTicket("")}
                        onGoTickets={(id) => { if (id) setFocusTicket(id); setTab("tickets"); }} />
          ) : (
            <div className="home-ops-scroll">
              <OpsTabBody tab={tab} onOpenConfig={openConfig} focusTicket={focusTicket}
                          onFocusConsumed={() => setFocusTicket("")}
                          onGoTickets={(id) => { if (id) setFocusTicket(id); setTab("tickets"); }} />
            </div>
          )
        ) : !dsReady ? (
          <DataSourcePlaceholder name={dataSourceMeta(dataSource, "home").name} />
        ) : (
          <>
            {tab === "keyword" && <KeywordMonitor marketplace={marketplace} dataSource={dataSource} />}
            {tab === "competitor" && (
              <AsinMonitor kind="competitor" marketplace={marketplace} dataSource={dataSource} onChanged={() => setAlertReloadKey((k) => k + 1)} />
            )}
            {tab === "own" && (
              <AsinMonitor kind="own" marketplace={marketplace} dataSource={dataSource} onChanged={() => setAlertReloadKey((k) => k + 1)} />
            )}
            {tab === "category" && <CategoryWatch marketplace={marketplace} dataSource={dataSource} />}
            {tab === "market" && <MarketTraffic marketplace={marketplace} dataSource={dataSource} />}
          </>
        )}
      </div>

      {isAdmin && toolPanel && (
        <LingXingToolsDialog panel={toolPanel} setPanel={setToolPanel}
                             onClose={() => setToolPanel(null)} />
      )}

      {/* ── Mobile bottom-sheet marketplace picker ── */}
      {pickerOpen && (
        <div className="show-mobile-picker">
          <div className="market-sheet-backdrop" onClick={() => setPickerOpen(false)} />
          <div className="market-sheet">
            <div className="market-sheet-handle" />
            <div className="market-sheet-title">选择站点</div>
            <div className="market-sheet-grid">
              {MARKETPLACES.map((m) => (
                <button
                  key={m.code}
                  className={"market-sheet-item" + (marketplace === m.code ? " active" : "")}
                  onClick={() => { setMarketplace(m.code); setPickerOpen(false); }}
                >
                  <span className="market-sheet-flag"><img src={FLAG_URL(m.code)} alt={m.code} style={{ width: 16, height: 12, verticalAlign: "middle" }} /></span>
                  <span className="market-sheet-code">{m.code}</span>
                  <span className="market-sheet-name">{m.name}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
