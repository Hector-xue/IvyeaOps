import { lazy, Suspense, useEffect, useRef, useState, type CSSProperties, type ReactElement } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api, logout } from "../api/client";
import { useAuth } from "../App";
import { resetBodyScrollLock } from "../lib/scrollLock";
// Lazy-loaded: these boards stay mounted (terminal/agents) or keep-alive
// (market/playbook/tools/imagegen), but their code is split into its own chunk
// and only fetched on first visit — keeps the initial bundle small.
const Terminal = lazy(() => import("../pages/workbench/Terminal"));
const Agents = lazy(() => import("../pages/workbench/Agents"));
const Market = lazy(() => import("../pages/workbench/Market"));
const Playbook = lazy(() => import("../pages/workbench/Playbook"));
const Tools = lazy(() => import("../pages/workbench/Tools"));
const ImageGen = lazy(() => import("../pages/workbench/ImageGen"));

function BoardFallback() {
  return <div style={{ padding: 40, textAlign: "center", color: "var(--t3)", fontSize: 13 }}>加载中…</div>;
}
import ManualModal from "../components/ManualModal";
import UpdateModal from "../components/UpdateModal";
import Tour from "../components/Tour";
import { TOURS, hasTour } from "../lib/tours";

// Boards with long-running tasks (research / generation / audit). These are kept
// mounted after first visit and merely hidden when inactive — so an in-progress
// task (its polling timer / streaming fetch + UI state) survives switching boards
// and is still there (and lands in history) when you come back. Same technique
// the Terminal/Agents boards already use to preserve their WebSockets.
const KEEP_ALIVE_BOARDS: Record<string, () => ReactElement> = {
  "/market": () => <Market />,
  "/playbook": () => <Playbook />,
  "/tools": () => <Tools />,
  "/imagegen": () => <ImageGen />,
};
const KEEP_ALIVE_PATHS = Object.keys(KEEP_ALIVE_BOARDS);

const HIDDEN_STYLE: CSSProperties = {
  position: "absolute", width: 0, height: 0, overflow: "hidden", opacity: 0, pointerEvents: "none",
};

type NavItem = {
  to: string;
  icon: string;
  label: string;
  badge?: string;
  admin?: boolean;        // true = visible to admin only
  key?: string;           // grantable module key; non-admins see it if granted
};

type NavSection = {
  title: string;
  items: NavItem[];
};

type UpdateInfo = {
  current: string;
  latest: string;
  update_available: boolean;
  release_url: string;
  platform_update_supported: boolean;
  detail: string;
};

const NAV: NavSection[] = [
  {
    title: "工具",
    items: [
      { to: "/", icon: "⌂", label: "首页" },
      { to: "/market", icon: "◈", label: "市场调研" },
      { to: "/playbook", icon: "◎", label: "打法推荐" },
      { to: "/listing", icon: "◧", label: "Listing工作台", admin: true, key: "listing" },
      { to: "/tools", icon: "⊕", label: "分析工具", admin: true, key: "tools" },
      { to: "/lingxing", icon: "◭", label: "领星 ERP", admin: true },
      { to: "/skill-hub", icon: "✦", label: "Skill 中心", admin: true, key: "skill-hub" },
    ],
  },
  {
    title: "AI & 系统",
    items: [
      { to: "/assistant", icon: "⊡", label: "AI 问答" },
      { to: "/imagegen", icon: "▦", label: "AI 生图" },
      { to: "/agents", icon: "◉", label: "智能体会话", admin: true, key: "agents" },
      { to: "/brain", icon: "▣", label: "GBrain 知识库", admin: true, key: "brain" },
      { to: "/terminal", icon: "▶", label: "服务器终端", admin: true, key: "terminal" },
      { to: "/servmon", icon: "⊙", label: "服务器监控", admin: true, key: "servmon" },
    ],
  },
  {
    title: "小工具",
    items: [
      { to: "/freight", icon: "⊞", label: "头程比价" },
    ],
  },
  {
    title: "管理",
    items: [
      { to: "/users", icon: "⊗", label: "用户管理", admin: true },
      { to: "/hub-settings", icon: "⚙", label: "系统配置", admin: true },
      { to: "/news", icon: "≡", label: "资讯", admin: true, key: "news" },
    ],
  },
];

const PATH_LABEL: Record<string, string> = {
  "/": "~/首页",
  "/tools": "~/分析工具",
  "/lingxing": "~/领星ERP",
  "/listing": "~/Listing工作台",
  "/freight": "~/头程比价",
  "/market": "~/市场调研",
  "/playbook": "~/打法推荐",
  "/assistant": "~/AI问答",
  "/imagegen": "~/AI生图",
  "/idea-skill": "~/想法工坊",
  "/skill-tools": "~/运营商店",
  "/skill-hub": "~/Skill中心",
  "/users": "~/用户管理",
  "/skill": "~/SkillStudio",
  "/brain": "~/GBrain知识库",
  "/agents": "~/智能体会话",
  "/agent": "~/AgentOS",
  "/terminal": "~/服务器终端",
  "/servmon": "~/服务器监控",
  "/news": "~/资讯",
  "/hub-settings": "~/系统配置",
};

export default function MainLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const { role, permissions } = useAuth();
  const isAdmin = role === "admin";
  // Visibility: admin sees all; everyone sees non-admin modules; a non-admin
  // also sees an admin module if its key is in their granted permissions.
  const canSee = (it: NavItem) => isAdmin || !it.admin || (!!it.key && permissions.includes(it.key));
  const navSections = NAV
    .map((sec) => ({ ...sec, items: sec.items.filter(canSee) }))
    .filter((sec) => sec.items.length > 0);

  // Pinned skill tools → dynamic sidebar entries. Refreshed on mount and when
  // a tool is pinned/unpinned (SkillTools dispatches 'ivyea-ops:pinned-changed').
  const [pinnedTools, setPinnedTools] = useState<{ name: string; icon: string; label: string }[]>([]);
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const { listPinnedTools } = await import("../api/skillTools");
        const items = await listPinnedTools();
        if (alive) setPinnedTools(items.map((t) => ({
          name: t.name,
          icon: t.icon || "⊞",
          label: t.description_zh?.slice(0, 8) || t.name.split("/").pop() || t.name,
        })));
      } catch { /* ignore — sidebar still works without pinned tools */ }
    };
    load();
    const onChange = () => load();
    window.addEventListener("ivyea-ops:pinned-changed", onChange);
    return () => { alive = false; window.removeEventListener("ivyea-ops:pinned-changed", onChange); };
  }, []);

  const [termMounted, setTermMounted] = useState(false);
  const [agentsMounted, setCcuiMounted] = useState(false);
  const [appVersion, setAppVersion] = useState("dev");
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null);
  const [updating, setUpdating] = useState(false);
  const THEMES = [
    "dark", "deep-space", "smoke-gold", "catppuccin", "hermes", "light",
    "klein", "mars", "hermes-orange", "burgundy", "mummy",
    "prussian", "tiffany", "titian", "schonbrunn", "bordeaux",
  ] as const;
  type Theme = typeof THEMES[number];
  const THEME_LABELS: Record<Theme, string> = {
    "dark":         "🌲 暗夜",
    "deep-space":   "🌌 星渊",
    "smoke-gold":   "✦ 烟金",
    "catppuccin":   "🔮 紫幕",
    "hermes":       "◆ 幽林",
    "light":        "☀ 月岩",
    "klein":        "◈ 克莱蓝",
    "mars":         "⬡ 马尔绿",
    "hermes-orange":"◉ 爱马橙",
    "burgundy":     "⊕ 勃艮红",
    "mummy":        "△ 木乃棕",
    "prussian":     "▣ 普鲁蓝",
    "tiffany":      "◇ 蒂芙蓝",
    "titian":       "✦ 提香红",
    "schonbrunn":   "⊙ 申布黄",
    "bordeaux":     "⊗ 波尔红",
  };
  const THEME_ICONS: Record<Theme, string> = {
    "dark": "🌲", "deep-space": "🌌", "smoke-gold": "✦",
    "catppuccin": "🔮", "hermes": "◆", "light": "☀",
    "klein": "◈", "mars": "⬡", "hermes-orange": "◉",
    "burgundy": "⊕", "mummy": "△", "prussian": "▣",
    "tiffany": "◇", "titian": "✦", "schonbrunn": "⊙", "bordeaux": "⊗",
  };
  const THEME_NAMES: Record<Theme, string> = {
    "dark": "暗夜", "deep-space": "星渊", "smoke-gold": "烟金",
    "catppuccin": "紫幕", "hermes": "幽林", "light": "月岩",
    "klein": "克莱蓝", "mars": "马尔绿", "hermes-orange": "爱马橙",
    "burgundy": "勃艮红", "mummy": "木乃棕", "prussian": "普鲁蓝",
    "tiffany": "蒂芙蓝", "titian": "提香红", "schonbrunn": "申布黄", "bordeaux": "波尔红",
  };
  const THEME_ACCENTS: Record<Theme, string> = {
    "dark":         "#4ade80",
    "deep-space":   "#60a5fa",
    "smoke-gold":   "#fbbf24",
    "catppuccin":   "#a78bfa",
    "hermes":       "#34d399",
    "light":        "#16a34a",
    "klein":        "#4d7fff",
    "mars":         "#8aad3c",
    "hermes-orange":"#f46020",
    "burgundy":     "#c03060",
    "mummy":        "#c87838",
    "prussian":     "#2d8ab5",
    "tiffany":      "#50c0b8",
    "titian":       "#c86030",
    "schonbrunn":   "#e8b01a",
    "bordeaux":     "#b03280",
  };
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = localStorage.getItem("ivyea-ops.theme") as Theme | null;
    return THEMES.includes(saved as any) ? saved! : "dark";
  });
  const [themePicker, setThemePicker] = useState(false);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);
  const themePickerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!themePicker) return;
    const handler = (e: MouseEvent) => {
      if (themePickerRef.current && !themePickerRef.current.contains(e.target as Node))
        setThemePicker(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [themePicker]);
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem("ivyea-ops.sidebar.collapsed") === "1" || window.innerWidth <= 680,
  );
  const [mobileMenu, setMobileMenu] = useState(false);
  const [isMobile, setIsMobile] = useState(() => window.innerWidth <= 900);

  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth <= 900);
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);

  // Once the user visits /terminal, keep it mounted forever.
  useEffect(() => {
    if (location.pathname === "/terminal") setTermMounted(true);
  }, [location.pathname]);

  // Safety net for the "页面偶尔无法滚动、刷新才好" report. Modals/drawers now use
  // a ref-counted body scroll lock (lib/scrollLock) which is leak-safe even when
  // overlays overlap; this last-resort reset zeros the counter on every route
  // change, so a missed release can never leave the page permanently locked.
  // A no-op when nothing leaked.
  useEffect(() => {
    resetBodyScrollLock();
  }, [location.pathname]);

  // 智能体会话(/agents):首次访问后常驻挂载,切板块秒回、WS/会话状态不丢。
  useEffect(() => {
    if (location.pathname === "/agents") setCcuiMounted(true);
  }, [location.pathname]);

  // 长任务板块(市场调研 / 打法 / 分析工具 / AI 生图):首次访问后常驻挂载,
  // 切走再回来时正在进行的任务(轮询/流式 + UI 状态)还在,完成后也能进历史。
  const [kaVisited, setKaVisited] = useState<Set<string>>(() => new Set());
  useEffect(() => {
    if (KEEP_ALIVE_PATHS.includes(location.pathname)) {
      setKaVisited((prev) => (prev.has(location.pathname) ? prev : new Set(prev).add(location.pathname)));
    }
  }, [location.pathname]);

  useEffect(() => {
    let alive = true;
    fetch("/api/health", { credentials: "include" })
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (alive && data?.version) setAppVersion(String(data.version));
      })
      .catch(() => void 0);
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!isAdmin) return;
    let alive = true;
    const checkUpdate = async () => {
      try {
        const { data } = await api.get<UpdateInfo>("/setup/update-info", { timeout: 8000 });
        if (!alive) return;
        setUpdateInfo(data);
        if (data.current) setAppVersion(data.current);
      } catch {
        // Silent: a blocked GitHub/network check should not distract normal use.
      }
    };
    checkUpdate();
    const timer = window.setInterval(checkUpdate, 6 * 60 * 60 * 1000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [isAdmin]);

  const startUpdate = async () => {
    if (updating) return;
    if (updateInfo && !updateInfo.update_available) {
      alert("当前已经是最新版本。");
      return;
    }
    if (updateInfo && !updateInfo.platform_update_supported) {
      alert(updateInfo.detail || "当前平台暂不支持应用内自动更新，将打开 Release 页面。");
      window.open(updateInfo.release_url || "https://github.com/Hector-xue/IvyeaOps/releases/latest", "_blank");
      return;
    }
    // In-app modal drives the whole flow: download w/ progress → install → poll
    // health until the new version answers. No external WinForms window.
    setUpdating(true);
  };
  const [clock, setClock] = useState("");
  const [manualOpen, setManualOpen] = useState(false);

  // Interactive tour: auto-run a board's tour on first visit (remembered per
  // board in localStorage); replayable via the "?" button.
  const [tourOn, setTourOn] = useState(false);
  useEffect(() => {
    const p = location.pathname;
    if (!hasTour(p)) { setTourOn(false); return; }
    let seen = false;
    try { seen = localStorage.getItem("ivyea-tour:" + p) === "1"; } catch { /* ignore */ }
    if (seen) return;
    const t = window.setTimeout(() => {
      setTourOn(true);
      try { localStorage.setItem("ivyea-tour:" + p, "1"); } catch { /* ignore */ }
    }, 700); // let the board render first
    return () => clearTimeout(t);
  }, [location.pathname]);

  // Clock
  useEffect(() => {
    const tick = () =>
      setClock(
        new Date().toLocaleTimeString("zh-CN", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        }),
      );
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, []);

  const selectTheme = (t: Theme) => {
    setTheme(t);
    document.documentElement.setAttribute("data-theme", t);
    localStorage.setItem("ivyea-ops.theme", t);
    setThemePicker(false);
    window.dispatchEvent(new CustomEvent("ivyea-ops:theme-changed", { detail: t }));
  };

  const toggleSidebar = () => {
    const next = !collapsed;
    setCollapsed(next);
    localStorage.setItem("ivyea-ops.sidebar.collapsed", next ? "1" : "0");
  };

  const handleLogout = async () => {
    try {
      await logout();
    } finally {
      navigate("/login");
    }
  };

  const path = PATH_LABEL[location.pathname] || "~/";
  const versionLabel = appVersion.startsWith("v") ? appVersion : `v${appVersion}`;
  const hasUpdate = !!updateInfo?.update_available;
  const updateTitle = updateInfo
    ? updateInfo.detail
    : "检测更新";

  return (
    <div className="app">
      {/* SIDEBAR */}
      {isMobile && mobileMenu && <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.5)", zIndex: 998 }} onClick={() => setMobileMenu(false)} />}
      <aside className={"sb" + (collapsed && !mobileMenu ? " collapsed" : "")} style={isMobile ? { position: "fixed", zIndex: 999, height: "100%", width: 196, minWidth: 196, overflow: "auto", left: 0, transform: mobileMenu ? "translateX(0)" : "translateX(-200px)", transition: "transform .22s cubic-bezier(.4,0,.2,1)", willChange: "transform" } : undefined}>
        <div className="sb-logo">
          <div className="sb-logo-name" title="个人工作台">
            <span className="sb-logo-icon">◆</span>
            <span className="sb-logo-text">个人工作台</span>
          </div>
          <button
            className="sb-toggle"
            onClick={toggleSidebar}
            title={collapsed ? "展开侧边栏" : "收起侧边栏"}
            aria-label={collapsed ? "展开侧边栏" : "收起侧边栏"}
          >
            {collapsed ? "▶" : "◀"}
          </button>
        </div>
        <nav data-tour="sidebar">
          {navSections.map((sec, si) => (
            <div key={sec.title}>
              {si > 0 && <div style={{height:1,background:"var(--b)",margin:"4px 12px"}} />}
              {sec.items.map((it) => (
                <NavLink
                  key={it.to}
                  to={it.to}
                  end={it.to === "/"}
                  className={({ isActive }) => "ni" + (isActive ? " active" : "")}
                  title={collapsed ? it.label : undefined}
                  onClick={() => isMobile && setMobileMenu(false)}
                >
                  <i className="ic">{it.icon}</i>
                  <span className="ni-label">{it.label}</span>
                  {it.badge && <span className="nb">{it.badge}</span>}
                </NavLink>
              ))}
            </div>
          ))}
          {pinnedTools.length > 0 && (
            <div>
              <div style={{ height: 1, background: "var(--b)", margin: "4px 12px" }} />
              {!collapsed && <div style={{ fontSize: 9, color: "var(--t3)", padding: "4px 16px 2px", letterSpacing: ".08em" }}>我的工具</div>}
              {pinnedTools.map((pt) => {
                const to = `/skill-tools?tool=${encodeURIComponent(pt.name)}`;
                const active = location.pathname === "/skill-tools" &&
                  new URLSearchParams(location.search).get("tool") === pt.name;
                return (
                  <NavLink
                    key={pt.name}
                    to={to}
                    className={"ni" + (active ? " active" : "")}
                    title={collapsed ? pt.label : undefined}
                    onClick={() => isMobile && setMobileMenu(false)}
                  >
                    <i className="ic">{pt.icon}</i>
                    <span className="ni-label">{pt.label}</span>
                  </NavLink>
                );
              })}
            </div>
          )}
        </nav>
        <div className="sb-bot">
          <div className="sb-status">
            <div className="dot" />
            <span className="sb-version-wrap" title={updateTitle}>
              <span className="sb-bot-text">{versionLabel}</span>
              {hasUpdate && <span className="sb-update-dot" aria-label="发现新版本" />}
            </span>
          </div>
          {isAdmin && (
            <button
              className={"sb-update-btn" + (hasUpdate ? " has-update" : "")}
              onClick={startUpdate}
              disabled={updating}
              title={updateTitle}
            >
              ↻
              <span className="sb-update-label">{updating ? "更新中" : hasUpdate ? "更新" : "检查"}</span>
            </button>
          )}
        </div>
      </aside>

      {/* MAIN */}
      <div className="main">
        <div className="topbar">
          {isMobile && <button className="tbtn" onClick={() => setMobileMenu(!mobileMenu)} style={{ marginRight: 4 }}>☰</button>}
          <div className="tb-path">
            <b>{path}</b>
          </div>
          <div className="tb-r">
            <div className="tb-time">{clock}</div>
            <button
              className="tbtn"
              data-tour="tour-help"
              onClick={() => setManualOpen(true)}
              title="使用手册"
            >
              📖
            </button>
            {hasTour(location.pathname) && (
              <button
                className="tbtn"
                onClick={() => setTourOn(true)}
                title="本板块使用引导"
              >
                ?
              </button>
            )}
            <button
              className="tbtn"
              onClick={() => {
                if ((window as any).OpsApp?.reload) {
                  (window as any).OpsApp.reload();
                } else {
                  window.location.reload();
                }
              }}
              title="刷新页面"
            >
              ↻
            </button>
            <div ref={themePickerRef} style={{ position: "relative", display: "flex", alignItems: "center" }}>
              <button
                className="tbtn"
                onClick={() => setThemePicker(!themePicker)}
                style={{ minWidth: 72 }}
                title="切换主题"
              >
                {THEME_LABELS[theme]}
              </button>
              {themePicker && (
                <div className="theme-picker">
                  {THEMES.map((t) => (
                    <button
                      key={t}
                      className={"theme-picker-card" + (t === theme ? " active" : "")}
                      onClick={() => selectTheme(t)}
                    >
                      <span className="theme-picker-dot" style={{ background: THEME_ACCENTS[t] }} />
                      <span className="theme-picker-icon">{THEME_ICONS[t]}</span>
                      <span className="theme-picker-name">{THEME_NAMES[t]}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <button className="tbtn" onClick={handleLogout} title="退出登录">
              ↩ 退出
            </button>
          </div>
        </div>
        <div className={"content" + (
          location.pathname === "/agents"
            ? " content-fullpage" : ""
        )}>
          {/* Terminal is always mounted (after first visit) but hidden when
              not active, so the iframe WebSocket survives tab switches. */}
          {/* Each lazy board gets its OWN Suspense so first-loading one never
              flips another (mounted, hidden) keep-alive board into a fallback. */}
          {termMounted && (
            <div style={location.pathname === "/terminal" ? { display: "contents" } : { position: "absolute", width: 0, height: 0, overflow: "hidden", opacity: 0, pointerEvents: "none" }}>
              <Suspense fallback={<BoardFallback />}><Terminal /></Suspense>
            </div>
          )}
          {/* Agents 原生版常驻挂载(独立 React root),切板块时不卸载,保持 WS/会话状态、秒切。 */}
          {agentsMounted && (
            <div style={location.pathname === "/agents" ? { display: "contents" } : { position: "absolute", width: 0, height: 0, overflow: "hidden", opacity: 0, pointerEvents: "none" }}>
              <Suspense fallback={<BoardFallback />}><Agents /></Suspense>
            </div>
          )}
          {/* Long-task boards: mounted on first visit, hidden when inactive so
              in-progress tasks survive board switches. */}
          {KEEP_ALIVE_PATHS.map((p) =>
            kaVisited.has(p) ? (
              <div key={p} style={location.pathname === p ? { display: "contents" } : HIDDEN_STYLE}>
                <Suspense fallback={<BoardFallback />}>{KEEP_ALIVE_BOARDS[p]()}</Suspense>
              </div>
            ) : null,
          )}
          {location.pathname !== "/terminal" && location.pathname !== "/agents"
            && !KEEP_ALIVE_PATHS.includes(location.pathname) && (
              <Suspense fallback={<BoardFallback />}><Outlet /></Suspense>
            )}
        </div>
      </div>
      {manualOpen && <ManualModal onClose={() => setManualOpen(false)} />}
      {updating && <UpdateModal currentVersion={appVersion} onClose={() => setUpdating(false)} />}
      {tourOn && hasTour(location.pathname) && (
        <Tour steps={TOURS[location.pathname]} onClose={() => setTourOn(false)} />
      )}
    </div>
  );
}
