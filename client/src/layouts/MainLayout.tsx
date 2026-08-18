import { lazy, Suspense, useEffect, useMemo, useState, type CSSProperties, type ReactElement } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api, logout } from "../api/client";
import { useAuth } from "../App";
import { resetBodyScrollLock } from "../lib/scrollLock";
import { DEFAULT_THEME, applyThemeAttrs, isThemeId } from "../lib/themes";
import {
  CONSOLE_NEW_EVENT,
  KEEP_ALIVE_PATHS,
  PERSISTENT_PATHS,
  boardPath,
  classicSections,
  isFullPage,
  primaryItems,
  readShellMode,
  toolSections,
  writeShellMode,
  type BoardEntry,
  type ShellMode,
} from "../lib/navRegistry";
// Lazy-loaded: these boards stay mounted (terminal/agents) or keep-alive
// (market/playbook/tools), but their code is split into its own chunk
// and only fetched on first visit — keeps the initial bundle small.
const Terminal = lazy(() => import("../pages/workbench/Terminal"));
const Agents = lazy(() => import("../pages/workbench/Agents"));
const Market = lazy(() => import("../pages/workbench/Market"));
const Playbook = lazy(() => import("../pages/workbench/Playbook"));
const Tools = lazy(() => import("../pages/workbench/Tools"));

function BoardFallback() {
  return <div style={{ padding: 40, textAlign: "center", color: "var(--t3)", fontSize: "var(--fs-13)" }}>加载中…</div>;
}
import ManualModal from "../components/ManualModal";
import SettingsDialog, { OPEN_SETTINGS_EVENT } from "../components/SettingsDialog";
import UpdateModal from "../components/UpdateModal";
import Tour from "../components/Tour";
import IvyeaAgentDock from "../components/IvyeaAgentDock";
import AccountMenu from "../components/AccountMenu";
import Icon from "../components/Icon";
import ToolsOverlay from "../components/ToolsOverlay";
import SessionRail from "../components/console/SessionRail";
import { TopbarSlotHost } from "../lib/uiSlots";
import { BOARD_PREFS_EVENT, getPinnedBoards, pushRecentBoard } from "../lib/boardPrefs";
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
};

// Boards mounted forever after their first visit (WebSocket / session state).
const PERSISTENT_BOARDS: Record<string, () => ReactElement> = {
  "/terminal": () => <Terminal />,
  "/agents": () => <Agents />,
};

/** 手机抽屉宽度。桌面默认 264，手机窄一点但要装得下导航项。 */
const MOBILE_SB_W = 272;

/** 「全部工具」分组的展开状态（本机偏好）。 */
const TOOLS_OPEN_KEY = "ivyea-ops.sidebar.tools-open";

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

export default function MainLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const { role, username, permissions } = useAuth();
  const isAdmin = role === "admin";
  const visibility = useMemo(() => ({ isAdmin, permissions }), [isAdmin, permissions]);

  const [shell, setShell] = useState<ShellMode>(readShellMode);
  const isConsoleShell = shell === "console";

  // Sidebar contents — both shells derive from the same registry, so a board can
  // never appear in one and vanish from the other.
  const legacySections = useMemo(() => classicSections(visibility), [visibility]);
  const primary = useMemo(() => primaryItems(visibility), [visibility]);
  const toolGroups = useMemo(() => toolSections(visibility), [visibility]);

  // 「全部工具」浮层 —— 18 个板块的目录从侧栏搬进了它，见 components/ToolsOverlay。
  const [toolsOverlay, setToolsOverlay] = useState(false);
  const [toolsExpanded, setToolsExpanded] = useState(
    () => localStorage.getItem(TOOLS_OPEN_KEY) === "1");

  // 钉在侧栏的板块（本机偏好，lib/boardPrefs）。和下面的 pinnedTools 不是一回事：
  // 那个钉的是技能工具、有后端、跨设备。两者并排显示在「我的工具」下。
  const [pinnedBoards, setPinnedBoards] = useState<string[]>(() => getPinnedBoards());
  useEffect(() => {
    const sync = () => setPinnedBoards(getPinnedBoards());
    window.addEventListener(BOARD_PREFS_EVENT, sync);
    return () => window.removeEventListener(BOARD_PREFS_EVENT, sync);
  }, []);
  const pinnedBoardItems = useMemo(
    () => pinnedBoards
      .map((to) => toolGroups.flatMap((s) => s.items).find((b) => b.to === to))
      .filter((b): b is BoardEntry => !!b),
    [pinnedBoards, toolGroups],
  );

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
  // 主题清单、图标、中文名、圆点色全部来自 lib/themes —— 这里曾经是五张手抄表，
  // 其中四套主题的强调色和 CSS 里的 --acc 已经对不上（选择器圆点和真实界面两个色）。
  const [theme, setTheme] = useState<string>(() => {
    const saved = localStorage.getItem("ivyea-ops.theme");
    return isThemeId(saved) ? saved : DEFAULT_THEME;
  });
  // 主题选择器整块搬进了左下角的账户菜单（components/AccountMenu），
  // 它的开合状态和点外面收起也一并归那边管 —— 这里只留"当前是哪套"。
  // main.tsx 在挂载前跑完迁移，用全局标记传进来 —— 那时候还没有 React，
  // 发事件会在监听器注册之前就丢掉。
  const [themeMigrated, setThemeMigrated] = useState(
    () => !!(window as unknown as { __ivyeaThemeMigrated?: boolean }).__ivyeaThemeMigrated,
  );

  // 主题的两个维度（配色 data-theme + 形状 data-skin）一次打上，
  // 走和 main.tsx 同一个函数 —— 分开写就会出现只改了一半的状态。
  useEffect(() => {
    applyThemeAttrs(theme);
  }, [theme]);

  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem("ivyea-ops.sidebar.collapsed") === "1" || window.innerWidth <= 680,
  );

  // ── 侧栏拖宽 ──────────────────────────────────────────────────────────
  // 会话标题动辄十几个字，196px 下几乎每一条都被截成"广告怎么优化…"，
  // 光看列表分不出哪条是哪条。宽度是每个人自己的取舍（屏幕宽度、会话命名习惯
  // 都不一样），所以做成可拖 + 记住，而不是我替所有人挑一个数。
  const SB_MIN = 200;
  const SB_MAX = 420;
  const [sbWidth, setSbWidth] = useState(() => {
    const v = parseInt(localStorage.getItem("ivyea-ops.sidebar.width") || "", 10);
    // 默认 264：196 是"能放下字"的宽度，不是"读着舒服"的宽度 —— 参考图里
    // ChatGPT / deepseek 的侧栏都在 260~330，会话标题基本不截断。
    return Number.isFinite(v) && v >= SB_MIN && v <= SB_MAX ? v : 264;
  });
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    if (!dragging) return;
    // 监听挂在 window 上而不是把手上：鼠标拖快了会甩出把手，
    // 挂在把手上的话指针一离开元素就断，表现为"拖着拖着自己停了"。
    const onMove = (e: MouseEvent) => {
      const w = Math.min(SB_MAX, Math.max(SB_MIN, e.clientX));
      setSbWidth(w);
    };
    const onUp = () => {
      setDragging(false);
      // 结束时才落盘：拖动过程中每一帧都写 localStorage 是同步 I/O。
      setSbWidth((w) => {
        try { localStorage.setItem("ivyea-ops.sidebar.width", String(w)); } catch { /* ignore */ }
        return w;
      });
    };
    // 拖动时全局禁选，否则会把整页文字刷成蓝色选区。
    const prevUserSelect = document.body.style.userSelect;
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      document.body.style.userSelect = prevUserSelect;
      document.body.style.cursor = "";
    };
  }, [dragging]);
  const [mobileMenu, setMobileMenu] = useState(false);
  // **实际**是不是收起态。移动端 collapsed 默认就是 true（宽度 ≤680），但抽屉一打开
  // 侧边栏是按展开呈现的 —— 内容却还在按 collapsed 渲染，于是会话列表直接 return
  // null、分组标题也不显示。结果就是移动端抽屉里**从来没有过会话列表**。
  // 侧边栏内部的所有渲染判断都该用这个，而不是裸 collapsed。
  const [isMobile, setIsMobile] = useState(() => window.innerWidth <= 900);

  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth <= 900);
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);

  // 板块总数 —— 「全部工具」按钮上带着它，是"板块一个都没丢"的凭据。
  const toolCount = useMemo(
    () => toolGroups.reduce((n, s) => n + s.items.length, 0),
    [toolGroups],
  );
  const railCollapsed = collapsed && !mobileMenu;

  // 去过的板块记一笔，供「全部工具」浮层的「最近」用。
  useEffect(() => {
    const hit = toolGroups.flatMap((s) => s.items).find((b) => boardPath(b) === location.pathname);
    if (hit) pushRecentBoard(hit.to);
  }, [location.pathname, toolGroups]);

  // ⌘K / Ctrl+K 唤起「全部工具」。**输入框里不抢** —— 用户正在打字时按下
  // ⌘K 的意图几乎不可能是"我要换个板块"。
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "k" && e.key !== "K") return;
      if (!e.metaKey && !e.ctrlKey) return;
      const el = document.activeElement as HTMLElement | null;
      const tag = el?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || el?.isContentEditable) return;
      e.preventDefault();
      setToolsOverlay((v) => !v);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

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

  // 长任务板块(市场调研 / 打法 / 分析工具):首次访问后常驻挂载,
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
  const [manualOpen, setManualOpen] = useState(false);
  // 系统配置改成对话框（见 components/AppDialog）。用全局事件而不是层层传回调：
  // 触发点散在账户菜单、花费芯片、任务台的模型行、能力市场的"去填密钥"里，
  // 一路 prop 传下去只会把中间那些组件变成传声筒。
  const [settingsSection, setSettingsSection] = useState<string | null>(null);
  useEffect(() => {
    const onOpen = (e: Event) => {
      const detail = (e as CustomEvent<{ section?: string }>).detail;
      setSettingsSection(detail?.section || "");
    };
    window.addEventListener(OPEN_SETTINGS_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_SETTINGS_EVENT, onOpen);
  }, []);

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

  // 顶栏那个秒级时钟已经删掉：系统托盘本来就有时间，而它每秒一次 setState
  // 会把整个外壳重渲染一遍 —— 拿一次全树重绘换一个谁都不看的信息。

  const selectTheme = (t: string) => {
    setTheme(t);
    applyThemeAttrs(t);
    localStorage.setItem("ivyea-ops.theme", t);
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
      title={railCollapsed ? b.label : undefined}
      onClick={() => isMobile && setMobileMenu(false)}
    >
      <i className="ic"><Icon name={b.icon} /></i>
      <span className="ni-label">{b.label}</span>
    </NavLink>
  );

  const divider = <div style={{ height: 1, background: "var(--b)", margin: "4px 12px" }} />;

  // 「我的工具」= 钉住的板块（本机偏好）+ 钉住的技能工具（有后端）。两者来源
  // 不同但对用户是同一件事：我自己挑出来常用的那几个。
  const pinnedGroup = (pinnedTools.length > 0 || pinnedBoardItems.length > 0) && (
    <div>
      {divider}
      {!railCollapsed && <div style={{ fontSize: "var(--fs-9)", color: "var(--t3)", padding: "4px 16px 2px", letterSpacing: ".08em" }}>我的工具</div>}
      {pinnedBoardItems.map(renderNavItem)}
      {pinnedTools.map((pt) => {
        const to = `/skill-tools?tool=${encodeURIComponent(pt.name)}`;
        const active = location.pathname === "/skill-tools" &&
          new URLSearchParams(location.search).get("tool") === pt.name;
        return (
          <NavLink
            key={pt.name}
            to={to}
            className={"ni" + (active ? " active" : "")}
            title={railCollapsed ? pt.label : undefined}
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
      <aside
        className={"sb" + (collapsed && !mobileMenu ? " collapsed" : "") + (dragging ? " sb-dragging" : "")}
        style={isMobile
          // 手机抽屉：**宽度和隐藏位移必须联动**。原来写死 196px 配 -200px 位移，
          // 桌面字号上调之后这个宽度装不下导航项（实测右边缘 204 > 196，溢出 8px），
          // 而位移一旦和宽度对不上，抽屉收起时还会露出一条边。
          // 用 -100% 而不是具体像素，宽度以后再改也不会脱节。
          ? { position: "fixed", zIndex: 999, height: "100%", width: MOBILE_SB_W, minWidth: MOBILE_SB_W, overflow: "auto", left: 0, transform: mobileMenu ? "translateX(0)" : "translateX(-100%)", transition: "transform .22s cubic-bezier(.4,0,.2,1)", willChange: "transform" }
          // 收起态的宽度归 CSS 的 .collapsed 管，这里只在展开时给宽度，
          // 否则拖过的宽度会把 52px 的收起态顶开。
          : (railCollapsed ? undefined : { width: sbWidth, minWidth: sbWidth })}
      >
        <div className="sb-logo">
          <div className="sb-logo-name" title="IvyeaOps">
            <img src="/ivyea-logo.png" alt="" className="sb-logo-img" />
            <span className="sb-logo-text">IvyeaOps</span>
          </div>
          <button
            className="sb-toggle"
            onClick={toggleSidebar}
            title={collapsed ? "展开侧边栏" : "收起侧边栏"}
            aria-label={collapsed ? "展开侧边栏" : "收起侧边栏"}
          >
            <Icon name={collapsed ? "panel-open" : "panel-close"} size={15} />
          </button>
        </div>
        <nav data-tour="sidebar" className={isConsoleShell ? "sb-nav-console" : undefined}>
          {isConsoleShell ? (
            <>
              {/*
               * 两段式：**只冻结「新建任务」这一栏，其余全在同一条滚动里**
               * （对标 DeepSeek Harness 的侧栏）。
               *
               * 上一版是三段式（固定区 / 会话弹性区 / 收纳区），两个毛病都被用户点名：
               * ① 「全部工具」一展开，固定区就长到 949px —— 比整条 nav（791px）还高，
               *    而 nav 是 overflow:hidden，于是会话区被压成 0 高、工具树自己也滚不动，
               *    整块目录只能看见前几行（实测值，见 harness 探针）。
               * ② 收起「全部工具」时，能滚的只有下半截（会话那块自己滚），
               *    上半截的一级项和工具入口跟着钉死 —— 一条侧栏两种滚动行为。
               * 现在固定区里只剩「新建任务」这一个按钮，它高度恒定，撑不爆任何东西。
               */}
              {/* ── 冻结区：只有「新建任务」──────────────────────────────── */}
              <div className="sb-nav-top">
                <button
                  className="ni ni-action"
                  onClick={startNewTask}
                  title={railCollapsed ? "新建任务" : undefined}
                  data-tour="console-new"
                >
                  <i className="ic"><Icon name="new-task" /></i>
                  <span className="ni-label">新建任务</span>
                </button>
              </div>

              {/* ── 滚动区：一级项 / 全部工具 / 会话 / 我的工具，一条滚到底 ── */}
              <div className="sb-nav-scroll scroll-thin">
                {primary.map(renderNavItem)}

                {/* 「全部工具」跟在定时任务后面，做成可展开的分组 —— 它是导航的一部分，
                    不该沉到侧栏最底下和账户挤在一起。展开状态记在本机。 */}
                {toolGroups.length > 0 && (
                  <>
                    <button
                      className={"ni ni-group" + (toolsExpanded ? " open" : "")}
                      onClick={() => {
                        if (railCollapsed) { setToolsOverlay(true); return; }  // 收起态点它开浮层
                        setToolsExpanded((v) => { localStorage.setItem(TOOLS_OPEN_KEY, v ? "0" : "1"); return !v; });
                      }}
                      title={railCollapsed ? `全部工具（${toolCount}）` : "全部工具"}
                    >
                      <i className="ic"><Icon name="all-tools" /></i>
                      <span className="ni-label">全部工具</span>
                      {!railCollapsed && (
                        <>
                          <span className="ni-count">{toolCount}</span>
                          <span className={"ni-caret" + (toolsExpanded ? " open" : "")}>›</span>
                        </>
                      )}
                    </button>
                    {toolsExpanded && !railCollapsed && (
                      <div className="sb-tools-tree">
                        {toolGroups.map((g) => (
                          <div key={g.title}>
                            <div className="sb-tools-group">{g.title}</div>
                            {g.items.map(renderNavItem)}
                          </div>
                        ))}
                      </div>
                    )}
                  </>
                )}

                {/* 工作区 / 会话。
                 *
                 * **这里永远是任务台的会话列表，不给板块接管。** 曾经让终端页把自己的
                 * 终端列表画到这儿，结果是主侧边栏在不同板块下变成不同东西 —— 全局导航
                 * 就不再是全局的了。板块自己的列表归板块自己的页面。 */}
                <SessionRail
                  collapsed={railCollapsed}
                  activeSessionId={activeSessionId}
                  onNavigate={() => isMobile && setMobileMenu(false)}
                />

                {/* 「我的工具」（钉住的板块和技能）跟着一起滚 —— 用户要的是
                    "只冻结新建任务那一栏"，多钉一块在底下就又变成两种滚动行为了。 */}
                <div className="sb-nav-dock">{pinnedGroup}</div>
              </div>
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
        {/* 左下角账户区 —— 顶栏那 8 个常驻按钮和原来的版本行都收进了它的菜单。
            版本号在副标题里常驻，有更新时头像挂红点。 */}
        <AccountMenu
          collapsed={railCollapsed}
          username={username}
          isAdmin={isAdmin}
          theme={theme}
          onSelectTheme={selectTheme}
          versionLabel={versionLabel}
          hasUpdate={hasUpdate}
          updateTitle={updateTitle}
          updating={updating}
          onUpdate={startUpdate}
          isConsoleShell={isConsoleShell}
          onToggleShell={toggleShell}
          onManual={() => setManualOpen(true)}
          onTour={hasTour(location.pathname) ? () => setTourOn(true) : null}
          onLogout={handleLogout}
          onNavigated={() => isMobile && setMobileMenu(false)}
        />
        {/* 拖宽把手。只在展开态出现 —— 收起态是固定的 52px 图标条，拖它没有意义。
            双击回默认宽度：拖歪了不用一点点试回去。 */}
        {!railCollapsed && !isMobile && (
          <div
            className="sb-resizer"
            onMouseDown={(e) => { e.preventDefault(); setDragging(true); }}
            onDoubleClick={() => {
              setSbWidth(196);
              try { localStorage.setItem("ivyea-ops.sidebar.width", "196"); } catch { /* ignore */ }
            }}
            title="拖动调整宽度，双击恢复默认"
            role="separator"
            aria-orientation="vertical"
          />
        )}
      </aside>

      {/* MAIN */}
      <div className="main">
        {/* 顶栏只回答一件事：我在哪儿。
         *
         * 这里原本常驻 8 个带框按钮（用量 / 时钟 / 外壳切换 / 手册 / 引导 /
         * 刷新 / 主题 / 退出），每天占着视野换一个月一次的使用频率。它们全部
         * 搬进了左下角的账户菜单。右侧留一个挂位给**属于当前这一页**的动作
         * （lib/topbarSlot），没有板块挂东西时它不占任何位置。 */}
        {/* 顶栏**不再显示面包屑**（原来那行「~/任务台」）：左边侧栏的高亮项已经
            回答了"我在哪"，再写一遍是重复，还平白占掉 40px。
            这一条现在只在**有内容时**才出现 —— 板块挂了动作（见 lib/uiSlots），
            或者移动端需要那个菜单按钮；两者都没有时整条消失（CSS 的 :has）。 */}
        <div className="topbar">
          {isMobile && (
            <button className="tbtn" onClick={() => setMobileMenu(!mobileMenu)}
                    style={{ marginRight: 4 }} aria-label="打开侧边栏">☰</button>
          )}
          {/* 属于当前这一页的动作挂这里（lib/uiSlots）。没人挂时是个空 div，不占位置。
              这条注释以前描述的 lib/topbarSlot 其实从没建起来 —— 于是各板块只能
              自己再画一行工具条，把同一个板块名写两遍、白占 44px。 */}
          <TopbarSlotHost />
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
      <ToolsOverlay
        open={toolsOverlay}
        onClose={() => setToolsOverlay(false)}
        visibility={visibility}
      />
      {manualOpen && <ManualModal onClose={() => setManualOpen(false)} />}
      {settingsSection !== null && (
        <SettingsDialog section={settingsSection || undefined} onClose={() => setSettingsSection(null)} />
      )}
      {updating && <UpdateModal currentVersion={appVersion} onClose={() => setUpdating(false)} />}
      {tourOn && hasTour(location.pathname) && (
        <Tour steps={TOURS[location.pathname]} onClose={() => setTourOn(false)} />
      )}
      {/* 默认主题换成门道之后，给老用户的一次性告知。
          不用 toast：ToastProvider 是各页面自己挂的，外壳这一层拿不到。 */}
      {themeMigrated && (
        <div className="wb-toast" role="status"
             style={{ position: "fixed", right: 16, bottom: 16, zIndex: 200,
                      display: "flex", alignItems: "center", gap: 10,
                      padding: "10px 12px", maxWidth: 420,
                      border: "1px solid var(--b)", background: "var(--bg1)",
                      color: "var(--t)", fontSize: "var(--fs-12)" }}>
            <span>已切换到新的默认主题「门道」。右上角可以随时换回原来的。</span>
            <button className="tbtn" onClick={() => { selectTheme("dark"); setThemeMigrated(false); }}>
              换回暗夜
            </button>
            <button className="tbtn" onClick={() => setThemeMigrated(false)}>知道了</button>
        </div>
      )}

      {/* 任务台本身就是 Agent 的主入口，右下角再挂一个悬浮球等于同一件事摆两遍。
          其余板块保留 —— 在那儿它是"随手问一句"的快捷方式，仍然有用。 */}
      {location.pathname !== "/console" && <IvyeaAgentDock />}
    </div>
  );
}
