import { useEffect, useRef, useState } from "react";
import KeywordMonitor from "./home/KeywordMonitor";
import PromoCalendar from "./home/PromoCalendar";
import AdsBoard from "./home/AdsBoard";
import AsinMonitor from "./home/AsinMonitor";
import AlertStrip from "./home/AlertStrip";
import CategoryWatch from "./home/CategoryWatch";
import MarketTraffic from "./home/MarketTraffic";
import { getDataSource, setDataSource, dataSourceMeta, type DataSourceId } from "../../lib/dataSource";
import DataSourcePicker from "../../components/DataSourcePicker";
import { FLAG_URL } from "../../lib/marketplaces";
import { ToastProvider } from "../../components/toast";
import { useAuth } from "../../App";

const STORAGE_MKT = "ivyea-ops-pulse-marketplace";
const STORAGE_TAB = "ivyea-ops-home-tab";
const MARKETPLACES = [
  { code: "US", name: "美国" }, { code: "UK", name: "英国" },
  { code: "DE", name: "德国" }, { code: "JP", name: "日本" },
  { code: "CA", name: "加拿大" }, { code: "FR", name: "法国" },
  { code: "AU", name: "澳大利亚" }, { code: "IT", name: "意大利" },
];

type HomeTab = "keyword" | "competitor" | "own" | "category" | "market" | "promo" | "ads";

// admin: true 的两个标签读的是自家店铺经营数据（领星），且广告看板能发起真会改
// 线上投放的调整 —— 与「领星ERP」板块同源同权限，非管理员不显示，后端也有真闸。
const TABS: { key: HomeTab; label: string; icon: string; admin?: boolean }[] = [
  { key: "promo", label: "促销日历", icon: "◷", admin: true },
  { key: "ads", label: "广告看板", icon: "◎", admin: true },
  { key: "market", label: "大盘流量", icon: "↗" },
  { key: "keyword", label: "关键词", icon: "◈" },
  { key: "competitor", label: "竞品监控", icon: "⊞" },
  { key: "own", label: "自有 ASIN", icon: "★" },
  { key: "category", label: "类目大盘", icon: "☰" },
];

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

export default function Home() {
  const [marketplace, setMarketplace] = useState(() => localStorage.getItem(STORAGE_MKT) || "US");
  const [tab, setTab] = useState<HomeTab>(() => (localStorage.getItem(STORAGE_TAB) as HomeTab) || "market");
  const { role } = useAuth();
  const isAdmin = role === "admin";
  const visibleTabs = TABS.filter(t => !t.admin || isAdmin);
  const [dataSource, setDataSourceState] = useState<DataSourceId>(getDataSource);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [alertReloadKey, setAlertReloadKey] = useState(0);
  const pickerRef = useRef<HTMLDivElement>(null);

  // Persist + reload all data when the source changes (the tab body remounts
  // via its key, and AlertStrip re-fetches via reloadKey).
  const changeDataSource = (id: DataSourceId) => {
    if (id === dataSource) return;
    setDataSource(id);
    setDataSourceState(id);
    setAlertReloadKey(k => k + 1);
  };
  const dsReady = dataSourceMeta(dataSource, "home").ready;

  useEffect(() => { localStorage.setItem(STORAGE_MKT, marketplace); }, [marketplace]);
  useEffect(() => { localStorage.setItem(STORAGE_TAB, tab); }, [tab]);
  // 上次停在管理员标签、这次换成普通用户登录时，别让他停在一个只会 403 的页面上。
  useEffect(() => {
    if (!visibleTabs.some(t => t.key === tab)) setTab(visibleTabs[0]?.key ?? "market");
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
  const currentMkt = MARKETPLACES.find(m => m.code === marketplace) ?? MARKETPLACES[0];

  return (
    <ToastProvider>
    <div className="home-cockpit">
      {/* ── Top bar: title + date + global marketplace ── */}
      <div className="home-topbar">
        <span className="home-title">
          <span style={{ color: "var(--acc)" }}>◧</span> 运营驾驶舱
          <span className="home-date">{today}</span>
        </span>
        <div data-tour="home-source" style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <DataSourcePicker value={dataSource} onChange={changeDataSource} surface="home" />
          <div className="market-mkt-wrap" ref={pickerRef}>
          <button className="market-mkt-btn" onClick={() => setPickerOpen(o => !o)} title="选择站点">
            <span className="market-mkt-flag"><img src={FLAG_URL(currentMkt.code)} alt={currentMkt.code} style={{width:16,height:12,verticalAlign:"middle"}} /></span>
            <span className="market-mkt-code">{currentMkt.code}</span>
            <span className="market-mkt-arrow">{pickerOpen ? "▴" : "▾"}</span>
          </button>
          {pickerOpen && (
            <div className="market-mkt-dropdown hide-mobile-picker">
              {MARKETPLACES.map(m => (
                <button
                  key={m.code}
                  className={"market-mkt-option" + (marketplace === m.code ? " active" : "")}
                  onClick={() => { setMarketplace(m.code); setPickerOpen(false); }}
                >
                  <span><img src={FLAG_URL(m.code)} alt={m.code} style={{width:16,height:12,verticalAlign:"middle"}} /></span>
                  <span className="market-mkt-option-code">{m.code}</span>
                  <span className="market-mkt-option-name">{m.name}</span>
                </button>
              ))}
            </div>
          )}
          </div>
        </div>
      </div>

      {/* ── Alert strip ── */}
      {dsReady && (
        <AlertStrip
          reloadKey={alertReloadKey}
          dataSource={dataSource}
          marketplace={marketplace}
          onJump={(kind, mkt) => { setMarketplace(mkt); setTab(kind); }}
        />
      )}

      {/* ── Tabs ── */}
      <div className="home-tabs">
        {visibleTabs.map(t => (
          <button
            key={t.key}
            className={"home-tab" + (tab === t.key ? " active" : "")}
            onClick={() => setTab(t.key)}
          >
            <span className="home-tab-icon">{t.icon}</span>
            <span className="home-tab-label">{t.label}</span>
          </button>
        ))}
      </div>

      {/* ── Tab body (remounts on data-source / marketplace change → reloads
           all data; the marketplace key also keeps in-flight fetches from the
           previous site from bleeding into the new one) ── */}
      <div className="home-tab-body wb-enter"
           key={tab === "promo" || tab === "ads" ? tab : tab + ":" + dataSource + ":" + marketplace}>
        {tab === "promo" ? (
          <PromoCalendar />
        ) : tab === "ads" ? (
          <AdsBoard />
        ) : !dsReady ? (
          <DataSourcePlaceholder name={dataSourceMeta(dataSource, "home").name} />
        ) : (
          <>
            {tab === "keyword" && <KeywordMonitor marketplace={marketplace} dataSource={dataSource} />}
            {tab === "competitor" && (
              <AsinMonitor kind="competitor" marketplace={marketplace} dataSource={dataSource} onChanged={() => setAlertReloadKey(k => k + 1)} />
            )}
            {tab === "own" && (
              <AsinMonitor kind="own" marketplace={marketplace} dataSource={dataSource} onChanged={() => setAlertReloadKey(k => k + 1)} />
            )}
            {tab === "category" && <CategoryWatch marketplace={marketplace} dataSource={dataSource} />}
            {tab === "market" && <MarketTraffic marketplace={marketplace} dataSource={dataSource} />}
          </>
        )}
      </div>

      {/* ── Mobile bottom-sheet marketplace picker ── */}
      {pickerOpen && (
        <div className="show-mobile-picker">
          <div className="market-sheet-backdrop" onClick={() => setPickerOpen(false)} />
          <div className="market-sheet">
            <div className="market-sheet-handle" />
            <div className="market-sheet-title">选择站点</div>
            <div className="market-sheet-grid">
              {MARKETPLACES.map(m => (
                <button
                  key={m.code}
                  className={"market-sheet-item" + (marketplace === m.code ? " active" : "")}
                  onClick={() => { setMarketplace(m.code); setPickerOpen(false); }}
                >
                  <span className="market-sheet-flag"><img src={FLAG_URL(m.code)} alt={m.code} style={{width:16,height:12,verticalAlign:"middle"}} /></span>
                  <span className="market-sheet-code">{m.code}</span>
                  <span className="market-sheet-name">{m.name}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
    </ToastProvider>
  );
}
