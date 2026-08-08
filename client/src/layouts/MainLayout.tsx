import { lazy, Suspense, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactElement } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api, logout } from "../api/client";
import { useAuth } from "../App";
import { resetBodyScrollLock } from "../lib/scrollLock";
import {
  CONSOLE_NEW_EVENT,
  KEEP_ALIVE_PATHS,
  PERSISTENT_PATHS,
  boardPath,
  classicSections,
  isFullPage,
  pathLabel as breadcrumbFor,
  primaryItems,
  readShellMode,
  toolSections,
  writeShellMode,
  type BoardEntry,
  type ShellMode,
} from "../lib/navRegistry";
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
import IvyeaAgentDock from "../components/IvyeaAgentDock";
import SessionRail from "../components/console/SessionRail";
import { TOURS, hasTour } from "../lib/tours";

// Boards with long-running tasks (research / generation / audit). These are kept
// mounted after first visit and merely hidden when inactive — so an in-progress
// task (its polling timer / streaming fetch + UI state) survives switching boards
// and is still there (and lands in history) when you come back. Same technique
// the Terminal/Agents boards already use to preserve their WebSockets.
//
// Which paths belong here now comes from lib/navRegistry (`keepAlive` /
// `persistent` flags) — this map only says *how* to render each one.
const KEEP_ALIVE_BOARDS: Record<string, () => ReactElement> = {
  "/market": () => <Market />,
  "/playbook": () => <Playbook />,
  "/tools": () => <Tools />,
  "/imagegen": () => <ImageGen />,
};

// Boards mounted forever after their first visit (WebSocket / session state).
const PERSISTENT_BOARDS: Record<string, () => ReactElement> = {
  "/terminal": () => <Terminal />,
  "/agents": () => <Agents />,
};

const HIDDEN_STYLE: CSSProperties = {
  position: "absolute", width: 0, height: 0, overflow: "hidden", opacity: 0, pointerEvents: "none",
};

type UpdateInfo = {
  current: string;
  latest: string;
  update_available: boolean;
  release_url: string;
  platform_update_supported: boolean;
  detail: string;
};

const TOOLS_OPEN_KEY = "ivyea-ops.sidebar.tools-open";

export default function MainLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const { role, permissions } = useAuth();
  const isAdmin = role === "admin";
  const visibility = useMemo(() => ({ isAdmin, permissions }), [isAdmin, permissions]);

  const [shell, setShell] = useState<ShellMode>(readShellMode);
  const isConsoleShell = shell === "console";

  // Sidebar contents — both shells derive from the same registry, so a board can
  // never appear in one and vanish from the other.
  const legacySections = useMemo(() => classicSections(visibility), [visibility]);
  const primary = useMemo(() => primaryItems(visibility), [visibility]);
  const toolGroups = useMemo(() => toolSections(visibility), [visibility]);

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

  // "更多工具" group. Defaults to open so an upgrade never looks like boards went
  // missing; auto-opens whenever the active route lives inside it.
  const [toolsOpen, setToolsOpen] = useState(() => {
    try { return localStorage.getItem(TOOLS_OPEN_KEY) !== "0"; } catch { return true; }
  });
  const activeIsTool = useMemo(
    () => toolGroups.some((s) => s.items.some((b) => boardPath(b) === location.pathname)),
    [toolGroups, location.pathname],
  );
  useEffect(() => {
    if (activeIsTool && !toolsOpen) setToolsOpen(true);
    // Only reacts to the route landing inside the group — never fights a manual close.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeIsTool]);
  const toggleTools = () => {
    const next = !toolsOpen;
    setToolsOpen(next);
    try { localStorage.setItem(TOOLS_OPEN_KEY, next ? "1" : "0"); } catch { /* ignore */ }
  };

  // Persistent boards (terminal / agents): once visited, stay mounted forever.
  const [persistentVisited, setPersistentVisited] = useState<Set<string>>(() => new Set());
  useEffect(() => {
    if (PERSISTENT_PATHS.includes(location.pathname)) {
      setPersistentVisited((prev) => (prev.has(location.pathname) ? prev : new Set(prev).add(location.pathname)));
    }
  }, [location.pathname]);

  // Safety net for the "页面偶尔无法滚动、刷新才好" report. Modals/drawers now use
  // a ref-counted body scroll lock (lib/scrollLock) which is leak-safe even when
  // overlays overlap; this last-resort reset zeros the counter on every route
  // change, so a missed release can never leave the page permanently locked.
  // A no-op when nothing leaked.
  useEffect(() => {
    resetBodyScrollLock();
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

  const toggleShell = () => {
    const next: ShellMode = isConsoleShell ? "classic" : "console";
    setShell(next);
    writeShellMode(next);
    // 落地页跟着外壳走：切回经典就把停在任务台的人送回运营驾驶舱，反之亦然，
    // 免得切完之后站在一个新外壳才有的页面上。
    if (next === "classic" && location.pathname === "/console") navigate("/dashboard");
  };

  const startNewTask = () => {
    if (isMobile) setMobileMenu(false);
    if (location.pathname !== "/console") navigate("/console");
    // Console listens for this and opens a fresh turn even when already there.
    window.dispatchEvent(new CustomEvent(CONSOLE_NEW_EVENT));
  };

  const handleLogout = async () => {
    try {
      await logout();
    } finally {
      navigate("/login");
    }
  };

  // 左栏高亮当前打开的会话：地址栏 ?session= 是唯一真相（任务台打开一条会话时
  // 会把它写进 URL），这样刷新/分享链接后高亮也对得上。
  const activeSessionId = location.pathname === "/console"
    ? (new URLSearchParams(location.search).get("session") || "")
    : "";

  const path = breadcrumbFor(location.pathname);
  const versionLabel = appVersion.startsWith("v") ? appVersion : `v${appVersion}`;
  const hasUpdate = !!updateInfo?.update_available;
  const updateTitle = updateInfo
    ? updateInfo.detail
    : "检测更新";

  const renderNavItem = (b: BoardEntry) => (
    <NavLink
      key={b.to}
      to={b.to}
      end={b.to === "/"}
      className={({ isActive }) => "ni" + (isActive ? " active" : "")}
      title={collapsed ? b.label : undefined}
      onClick={() => isMobile && setMobileMenu(false)}
    >
      <i className="ic">{b.icon}</i>
      <span className="ni-label">{b.label}</span>
    </NavLink>
  );

  const divider = <div style={{ height: 1, background: "var(--b)", margin: "4px 12px" }} />;

  const pinnedGroup = pinnedTools.length > 0 && (
    <div>
      {divider}
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
  );

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
          {isConsoleShell ? (
            <>
              {/* ── 新建任务 + 一级项 ─────────────────────────────────── */}
              <button
                className="ni ni-action"
                onClick={startNewTask}
                title={collapsed ? "新建任务" : undefined}
                data-tour="console-new"
              >
                <i className="ic">⊕</i>
                <span className="ni-label">新建任务</span>
              </button>
              {primary.map(renderNavItem)}

              {/* ── 更多工具（现有全部板块）────────────────────────────── */}
              {toolGroups.length > 0 && (
                <>
                  {divider}
                  <button
                    className={"ni ni-group" + (toolsOpen ? " open" : "")}
                    onClick={toggleTools}
                    title={collapsed ? "更多工具" : undefined}
                    aria-expanded={toolsOpen}
                  >
                    <i className="ic">⋯</i>
                    <span className="ni-label">更多工具</span>
                    <span className="ni-caret">{toolsOpen ? "▾" : "▸"}</span>
                  </button>
                  {toolsOpen && toolGroups.map((sec, si) => (
                    <div key={sec.title} className="ni-sub">
                      {si > 0 && divider}
                      {!collapsed && <div className="ns">{sec.title}</div>}
                      {sec.items.map(renderNavItem)}
                    </div>
                  ))}
                </>
              )}

              {/* ── 工作区 / 会话 ────────────────────────────────────────── */}
              {divider}
              <SessionRail
                collapsed={collapsed}
                activeSessionId={activeSessionId}
                onNavigate={() => isMobile && setMobileMenu(false)}
              />

              {pinnedGroup}
            </>
          ) : (
            <>
              {/* ── 旧壳：改造前的四段分组，逐条一致 ─────────────────────── */}
              {legacySections.map((sec, si) => (
                <div key={sec.title}>
                  {si > 0 && divider}
                  {sec.items.map(renderNavItem)}
                </div>
              ))}
              {pinnedGroup}
            </>
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
              onClick={toggleShell}
              title={isConsoleShell ? "切回经典侧边栏布局" : "切换到任务台布局"}
            >
              {isConsoleShell ? "▤ 经典" : "◆ 任务台"}
            </button>
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
        <div className={"content" + (isFullPage(location.pathname) ? " content-fullpage" : "")}>
          {/* Persistent boards (terminal / agents) are always mounted after their
              first visit but hidden when not active, so their WebSocket and
              session state survive board switches.
              Each lazy board gets its OWN Suspense so first-loading one never
              flips another (mounted, hidden) keep-alive board into a fallback. */}
          {PERSISTENT_PATHS.map((p) =>
            persistentVisited.has(p) ? (
              <div key={p} style={location.pathname === p ? { display: "contents" } : HIDDEN_STYLE}>
                <Suspense fallback={<BoardFallback />}>{PERSISTENT_BOARDS[p]()}</Suspense>
              </div>
            ) : null,
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
          {!PERSISTENT_PATHS.includes(location.pathname)
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
      {/* 任务台本身就是 Agent 的主入口，右下角再挂一个悬浮球等于同一件事摆两遍。
          其余板块保留 —— 在那儿它是"随手问一句"的快捷方式，仍然有用。 */}
      {location.pathname !== "/console" && <IvyeaAgentDock />}
    </div>
  );
}
