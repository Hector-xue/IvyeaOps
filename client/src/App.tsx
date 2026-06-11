import { createContext, lazy, useContext, useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
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
const TrashList = lazy(() => import("./pages/skill/TrashList"));
const SettingsPage = lazy(() => import("./pages/skill/SettingsPage"));
const Terminal = lazy(() => import("./pages/workbench/Terminal"));
const ServerMonitor = lazy(() => import("./pages/workbench/ServerMonitor"));
const News = lazy(() => import("./pages/workbench/News"));
const Brain = lazy(() => import("./pages/workbench/Brain"));
const ListingWorkbench = lazy(() => import("./pages/workbench/ListingWorkbench"));
const Agents = lazy(() => import("./pages/workbench/Agents"));
const Market = lazy(() => import("./pages/workbench/Market"));
const Playbook = lazy(() => import("./pages/workbench/Playbook"));
const HubSettings = lazy(() => import("./pages/workbench/HubSettings"));
const FreightQuote = lazy(() => import("./pages/workbench/FreightQuote"));
const Users = lazy(() => import("./pages/workbench/Users"));
const Assistant = lazy(() => import("./pages/workbench/Assistant"));
const ImageGen = lazy(() => import("./pages/workbench/ImageGen"));
const IdeaSkill = lazy(() => import("./pages/workbench/IdeaSkill"));
const SkillTools = lazy(() => import("./pages/workbench/SkillTools"));
const SkillHub = lazy(() => import("./pages/workbench/SkillHub"));
const DeepAnalysis = lazy(() => import("./pages/workbench/DeepAnalysis"));
const LingXing = lazy(() => import("./pages/workbench/LingXing"));
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
          height: "100vh",
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
            <Route index element={<Home />} />
            <Route path="tools" element={<Tools />} />
            <Route path="skill" element={<SkillStudio />}>
              <Route index element={<StatsOverview />} />
              <Route path="browse" element={<SkillBrowse />} />
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
            <Route path="assistant" element={<Assistant />} />
            <Route path="imagegen" element={<ImageGen />} />
            <Route path="idea-skill" element={<IdeaSkill />} />
            <Route path="skill-tools" element={<SkillTools />} />
            <Route path="skill-hub" element={<SkillHub />} />
            <Route path="deep-analysis" element={<DeepAnalysis />} />
            <Route path="lingxing" element={<LingXing />} />
            <Route path="hub-settings" element={<HubSettings />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </ErrorBoundary>
      </ConfirmProvider>
    </BrowserRouter>
  );
}
