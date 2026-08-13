import { lazy, Suspense, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import SkillTools from "./SkillTools";
import IdeaSkill from "./IdeaSkill";
import SkillManage from "./skill/SkillManage";

const ImportGitHubDialog = lazy(() => import("../skill/ImportGitHubDialog"));

const TABS = [
  { key: "tools", label: "工具" },
  { key: "create", label: "创建" },
  { key: "manage", label: "管理" },
] as const;

type TabKey = (typeof TABS)[number]["key"];
const STORAGE_TAB = "ivyea-ops-skill-hub-tab";
const isTab = (v: string | null): v is TabKey => TABS.some((t) => t.key === v);

export default function SkillHub() {
  // Deep-linkable (?tab=create) + remembered across visits.
  const [searchParams, setSearchParams] = useSearchParams();
  const [tab, setTab] = useState<TabKey>(() => {
    const fromUrl = searchParams.get("tab");
    if (isTab(fromUrl)) return fromUrl;
    const stored = localStorage.getItem(STORAGE_TAB);
    return isTab(stored) ? stored : "tools";
  });
  const [showGithubImport, setShowGithubImport] = useState(false);

  useEffect(() => { localStorage.setItem(STORAGE_TAB, tab); }, [tab]);

  // Follow in-app navigations that change ?tab= while the hub stays mounted
  // (e.g. 想法工坊 saves a skill and jumps to the tools tab to open it).
  useEffect(() => {
    const fromUrl = searchParams.get("tab");
    if (isTab(fromUrl)) setTab((cur) => (cur === fromUrl ? cur : fromUrl));
  }, [searchParams]);

  const switchTab = (t: TabKey) => {
    setTab(t);
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("tab", t);
      next.delete("tool"); // tool deep-link belongs to the tools tab only
      return next;
    }, { replace: true });
  };

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
        <div className="ptitle" style={{ marginBottom: 0 }}>/ Skill 中心</div>
        <div style={{ display: "flex", gap: 2 }}>
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => switchTab(t.key)}
              style={{
                padding: "5px 14px",
                fontSize: "var(--fs-11)",
                border: "none",
                borderRadius: 3,
                cursor: "pointer",
                background: tab === t.key ? "var(--acc)" : "var(--bg2)",
                color: tab === t.key ? "#000" : "var(--t2)",
                fontWeight: tab === t.key ? 600 : 400,
              }}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {tab === "tools" && <div className="skill-hub-tab wb-enter"><SkillTools embedded /></div>}
      {tab === "create" && <div className="skill-hub-tab wb-enter"><IdeaSkill embedded /></div>}
      {tab === "manage" && (
        <div className="wb-enter">
          <div style={{ marginBottom: 10, display: "flex", gap: 8 }}>
            <button
              className="tbtn"
              onClick={() => setShowGithubImport(true)}
              style={{ fontSize: "var(--fs-10)" }}
            >
              ⬇ 从 GitHub 导入 Skill
            </button>
          </div>
          <SkillManage />
        </div>
      )}

      {showGithubImport && (
        <Suspense fallback={null}>
          <ImportGitHubDialog onClose={() => setShowGithubImport(false)} />
        </Suspense>
      )}
    </div>
  );
}
