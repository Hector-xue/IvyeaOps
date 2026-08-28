import { createContext, lazy, useContext, useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import MainLayout from "./layouts/MainLayout";
import ErrorBoundary from "./components/ErrorBoundary";
import { ConfirmProvider } from "./components/ConfirmDialog";
import AutoFixProvider from "./components/AutoFixProvider";
import Login from "./pages/Login";
import NotFound from "./pages/NotFound";
import Home from "./pages/workbench/Home";
import Setup from "./pages/Setup";
// Workbench boards are lazy-loaded (each becomes its own chunk fetched on first
// navigation), so the initial bundle no longer ships every board up front. The
// Suspense boundary lives in MainLayout (around <Outlet/> and the persistent /
// keep-alive boards). Home / Login / NotFound / Setup stay eager (shell).
const Tools = lazy(() => import("./pages/workbench/Tools"));
const SkillStudio = lazy(() => import("./pages/skill/SkillStudio"));
const StatsOverview = lazy(() => import("./pages/skill/StatsOverview"));
const SkillBrowse = lazy(() => import("./pages/skill/SkillBrowse"));
const SkillMarket = lazy(() => import("./pages/skill/SkillMarket"));
const TrashList = lazy(() => import("./pages/skill/TrashList"));
const SettingsPage = lazy(() => import("./pages/skill/SettingsPage"));
const Terminal = lazy(() => import("./pages/workbench/Terminal"));
const ServerMonitor = lazy(() => import("./pages/workbench/ServerMonitor"));
const News = lazy(() => import("./pages/workbench/News"));
const Brain = lazy(() => import("./pages/workbench/Brain"));
const ListingWorkbench = lazy(() => import("./pages/workbench/listing/ListingWorkbench"));
const Agents = lazy(() => import("./pages/workbench/Agents"));
const Market = lazy(() => import("./pages/workbench/Market"));
const Playbook = lazy(() => import("./pages/workbench/Playbook"));
const HubSettings = lazy(() => import("./pages/workbench/HubSettings"));
const FreightQuote = lazy(() => import("./pages/workbench/FreightQuote"));
const Users = lazy(() => import("./pages/workbench/Users"));
const ImageTranslate = lazy(() => import("./pages/workbench/ImageTranslate"));
const IdeaSkill = lazy(() => import("./pages/workbench/IdeaSkill"));
const SkillTools = lazy(() => import("./pages/workbench/SkillTools"));
const DeepAnalysis = lazy(() => import("./pages/workbench/DeepAnalysis"));
const LingXingRedirect = lazy(() => import("./pages/workbench/LingXingRedirect"));
const Console = lazy(() => import("./pages/workbench/Console"));
const Capabilities = lazy(() => import("./pages/workbench/Capabilities"));
const Approvals = lazy(() => import("./pages/workbench/Approvals"));
const Schedules = lazy(() => import("./pages/workbench/Schedules"));
import { landingPath } from "./lib/navRegistry";
import { me } from "./api/client";
import { getSetupStatus, type SetupChecks } from "./api/setup";

// ---------------------------------------------------------------------------
// Auth context — exposes the current user's role to all pages / the layout.
// ---------------------------------------------------------------------------

export type Role = "admin" | "user";
const AuthCtx = createContext<{ role: Role; username: string; permissions: string[] }>({ role: "user", username: "", permissions: [] });
export const useAuth = () => useContext(AuthCtx);

// ---------------------------------------------------------------------------
// Auth guard — also checks whether the first-run wizard is needed (admin only).
// ---------------------------------------------------------------------------

type AuthState = "loading" | "setup" | "ok" | "no";

function RequireAuth({ children }: { children: JSX.Element }) {
  const [state, setState] = useState<AuthState>("loading");
  const [setupChecks, setSetupChecks] = useState<SetupChecks | null>(null);
  const [auth, setAuth] = useState<{ role: Role; username: string; permissions: string[] }>({ role: "user", username: "", permissions: [] });

  useEffect(() => {
    me()
      .then(async (u) => {
        setAuth({ role: u.role, username: u.username, permissions: u.permissions || [] });
        // First-run wizard is admin-only; registered users skip it.
        if (u.role !== "admin") {
          setState("ok");
          return;
        }
        try {
          const s = await getSetupStatus();
          if (s.needs_setup) {
            setSetupChecks(s.checks);
            setState("setup");
          } else {
            setState("ok");
          }
        } catch {
          setState("ok");
        }
      })
      .catch(() => setState("no"));
  }, []);

  if (state === "loading") {
    return (
      <div
        style={{
          display: "grid",
          placeItems: "center",
          height: "100%",
          background: "var(--bg)",
          color: "var(--t3)",
          fontSize: 11,
          letterSpacing: ".1em",
        }}
      >
        <span>
          <span className="spin" style={{ marginRight: 8 }} />
          AUTHENTICATING...
        </span>
      </div>
    );
  }
  if (state === "no") return <Navigate to="/login" replace />;
  if (state === "setup" && setupChecks) return <Setup checks={setupChecks} />;
  return (
    <AuthCtx.Provider value={auth}>
      <AutoFixProvider>{children}</AutoFixProvider>
    </AuthCtx.Provider>
  );
}


/**
 * `/skill-hub` → 能力市场的「技能」标签。
 *
 * 老的三个标签（tools / create / manage）平移成新的分段（run / create / manage），
 * `?tool=` 深链原样带过去 —— 那是「运行」里定位到某个具体工具用的。
 */
function SkillHubRedirect() {
  const { search } = useLocation();
  const from = new URLSearchParams(search);
  const seg = { tools: "run", create: "create", manage: "manage" }[from.get("tab") || ""] || "run";
  const to = new URLSearchParams({ tab: "skills", seg });
  const tool = from.get("tool");
  if (tool) to.set("tool", tool);
  return <Navigate to={`/capabilities?${to}`} replace />;
}

export default function App() {
  return (
    <BrowserRouter>
      <ConfirmProvider>
      <ErrorBoundary>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            path="/"
            element={
              <RequireAuth>
                <MainLayout />
              </RequireAuth>
            }
          >
            {/* "/" 只做落地分流：新外壳去任务台，经典外壳照旧进运营驾驶舱。
                驾驶舱本身搬到 /dashboard，两套外壳都能稳定链过去，老书签落到
                "/" 也还是会被送到该去的地方。 */}
            <Route index element={<Navigate to={landingPath()} replace />} />
            <Route path="console" element={<Console />} />
            <Route path="capabilities" element={<Capabilities />} />
            <Route path="approvals" element={<Approvals />} />
            {/* 社区市场已并入「能力市场」的第一个 tab。这条老路径保留，
                指过去即可 —— 已经发出去的链接不该 404。 */}
            <Route path="community-market"
                   element={<Navigate to="/capabilities?tab=community" replace />} />
            <Route path="schedules" element={<Schedules />} />
            <Route path="dashboard" element={<Home />} />
            <Route path="tools" element={<Tools />} />
            <Route path="skill" element={<SkillStudio />}>
              <Route index element={<StatsOverview />} />
              <Route path="browse" element={<SkillBrowse />} />
              <Route path="market" element={<SkillMarket />} />
              <Route path="trash" element={<TrashList />} />
              <Route path="settings" element={<SettingsPage />} />
            </Route>
            <Route path="terminal" element={<Terminal />} />
            <Route path="servmon" element={<ServerMonitor />} />
            <Route path="news" element={<News />} />
            <Route path="brain" element={<Brain />} />
            <Route path="agents" element={<Agents />} />
            <Route path="listing" element={<ListingWorkbench />} />
            <Route path="freight" element={<FreightQuote />} />
            <Route path="market" element={<Market />} />
            <Route path="playbook" element={<Playbook />} />
            <Route path="users" element={<Users />} />
            {/* AI 问答 / AI 生图已并入任务台：问答就是任务台不带工具的那一档，
                作图由任务台的 image_generate 工具直接调同一条链路。老书签和老会话
                链接不能 404，一律接回任务台。 */}
            <Route path="assistant" element={<Navigate to="/console" replace />} />
            <Route path="imagegen" element={<Navigate to="/console" replace />} />
            <Route path="image-translate" element={<ImageTranslate />} />
            <Route path="idea-skill" element={<IdeaSkill />} />
            <Route path="skill-tools" element={<SkillTools />} />
            {/* Skill 中心已并入能力市场：同一批技能以前被列了三遍（这一页只读卡片、
                Skill 中心的运行列表、Skill 中心的文件管理），还分在两个板块里。
                老书签和老深链都不能 404 —— ?tab=create 要平移成 ?tab=skills&seg=create。 */}
            <Route path="skill-hub" element={<SkillHubRedirect />} />
            <Route path="deep-analysis" element={<DeepAnalysis />} />
            {/* 领星 ERP 已并入运营驾驶舱（市场侧 5 个 tab + 自家店铺 4 个 tab，
                数据浏览/审计/配置进了齿轮对话框）。老书签带着 ?tab= 进来，要按
                映射表落到对应的位置 —— 直接 Navigate 会把 query 丢掉。 */}
            <Route path="lingxing" element={<LingXingRedirect />} />
            <Route path="hub-settings" element={<HubSettings />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </ErrorBoundary>
      </ConfirmProvider>
    </BrowserRouter>
  );
}
