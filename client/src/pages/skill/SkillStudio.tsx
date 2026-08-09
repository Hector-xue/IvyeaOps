import { useState, lazy, Suspense } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import "../../styles/skill-studio.css";

// Dialogs pull in nothing heavy — keep them eager for snappy UX.
const NewSkillDialog = lazy(() => import("./NewSkillDialog"));
const ImportGitHubDialog = lazy(() => import("./ImportGitHubDialog"));

/**
 * Skill Studio shell. The outer workbench's MainLayout already supplies the
 * top/left chrome; this adds a tab strip and renders the active sub-page
 * via <Outlet />.
 */
const TABS = [
  { to: "/skill", label: "总览", end: true },
  { to: "/skill/browse", label: "技能" },
  { to: "/skill/market", label: "社区市场" },
  { to: "/skill/trash", label: "回收站" },
  { to: "/skill/settings", label: "设置" },
];

export default function SkillStudio() {
  const { pathname } = useLocation();
  const [modal, setModal] = useState<null | "new" | "import">(null);

  return (
    <div className="sks-wrap" style={{ height: "calc(100vh - 72px)" }}>
      <div className="sks-tabs" role="tablist" aria-label="Skill Studio sections">
        {TABS.map((t) => (
          <NavLink
            key={t.to}
            to={t.to}
            end={t.end}
            className={({ isActive }) => "sks-tab" + (isActive ? " active" : "")}
          >
            {t.label}
          </NavLink>
        ))}
        <div className="sks-tab-right">
          <button className="tbtn" onClick={() => setModal("new")} title="新建 Skill">
            + 新建
          </button>
          <button className="tbtn" onClick={() => setModal("import")} title="从 GitHub 导入">
            GitHub 导入
          </button>
          <span className="sks-path-hint" title={pathname}>{pathname}</span>
        </div>
      </div>
      <div className="sks-body">
        <Outlet />
      </div>

      {modal === "new" && (
        <Suspense fallback={null}>
          <NewSkillDialog onClose={() => setModal(null)} />
        </Suspense>
      )}
      {modal === "import" && (
        <Suspense fallback={null}>
          <ImportGitHubDialog onClose={() => setModal(null)} />
        </Suspense>
      )}
    </div>
  );
}
