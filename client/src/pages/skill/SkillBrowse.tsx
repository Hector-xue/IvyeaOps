import { useCallback, useEffect, useMemo, useState, lazy, Suspense } from "react";
import { useSearchParams } from "react-router-dom";
import { listSkills, SkillMeta } from "../../api/skill";
import SheetSelect from "../../components/SheetSelect";
// SkillBrowse owns the sks-* styles. Import them here (not only in SkillStudio) so
// the component is styled wherever it's mounted — e.g. embedded in the Skill 中心
// (/skill-hub → SkillManage), where SkillStudio never mounts. Without this the
// 管理 tab rendered completely unstyled ("没 UI"). CSS imports dedupe, so this is
// safe even when SkillStudio also imports the same file.
import "../../styles/skill-studio.css";
import { errText } from "../../lib/errText";

// SkillEditor brings in CodeMirror (~240KB gzip) — keep it off the list page.
const SkillEditor = lazy(() => import("./SkillEditor"));

function fmtDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString("zh-CN", {
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    });
  } catch { return iso; }
}

export default function SkillBrowse() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const category = params.get("category") ?? "";
  const selected = params.get("name") ?? "";

  const [skills, setSkills] = useState<SkillMeta[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Bumped whenever a mutation (delete, create) should refetch the list.
  const [refreshKey, setRefreshKey] = useState(0);

  // Local debounce for the search input so we don't thrash the URL bar / API.
  const [qInput, setQInput] = useState(q);
  useEffect(() => setQInput(q), [q]);
  useEffect(() => {
    if (qInput === q) return;
    const t = setTimeout(() => {
      const next = new URLSearchParams(params);
      if (qInput) next.set("q", qInput);
      else next.delete("q");
      setParams(next, { replace: true });
    }, 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qInput]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setErr(null);
    listSkills({ q: q || undefined, category: category || undefined })
      .then((r) => { if (alive) setSkills(r.skills); })
      .catch((e) => { if (alive) setErr(errText(e, "加载失败")); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [q, category, refreshKey]);

  const categories = useMemo(() => {
    const seen = new Set<string>();
    skills.forEach((s) => s.category && seen.add(s.category));
    return Array.from(seen).sort();
  }, [skills]);

  // Group skills by category for the blocked layout. Skills with no category fall
  // under "未分类", which sorts last; everything else is alphabetical.
  const grouped = useMemo(() => {
    const map = new Map<string, SkillMeta[]>();
    for (const s of skills) {
      const key = s.category || "未分类";
      const arr = map.get(key);
      if (arr) arr.push(s);
      else map.set(key, [s]);
    }
    return Array.from(map.entries()).sort(([a], [b]) => {
      if (a === "未分类") return 1;
      if (b === "未分类") return -1;
      return a.localeCompare(b);
    });
  }, [skills]);

  // Collapsed categories (persisted nowhere — session-local is fine).
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const toggleCat = (cat: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      next.has(cat) ? next.delete(cat) : next.add(cat);
      return next;
    });

  const pickSkill = (name: string) => {
    const next = new URLSearchParams(params);
    next.set("name", name);
    setParams(next, { replace: true });
  };

  const clearSelection = () => {
    const next = new URLSearchParams(params);
    next.delete("name");
    setParams(next, { replace: true });
  };

  return (
    <div className="sks-browse">
      {/* Toolbar */}
      <div className="sks-list-toolbar">
        <input
          className="sks-input"
          placeholder="搜索 name / description…"
          value={qInput}
          onChange={(e) => setQInput(e.target.value)}
        />
        <SheetSelect
          className="sks-input"
          style={{ flex: "0 0 auto", minWidth: 140 }}
          value={category}
          title="选择分类"
          onChange={(v) => {
            const next = new URLSearchParams(params);
            if (v) next.set("category", v);
            else next.delete("category");
            setParams(next, { replace: true });
          }}
          options={[
            { value: "", label: "全部分类" },
            ...categories.map((c) => ({ value: c, label: c || "(顶层)" })),
          ]}
        />
        <span style={{ color: "var(--t3)", fontSize: 10, marginLeft: "auto" }}>
          {loading ? "加载中…" : `共 ${skills.length} 项`}
        </span>
      </div>

      {err && <div className="sks-error">⚠ {err}</div>}

      {/* Two-column layout: list + detail */}
      <div className={"sks-browse-split" + (selected ? " has-sel" : "")}>
        <div className="sks-list sks-list--grouped">
          {!loading && skills.length === 0 ? (
            <div className="sks-empty">没有符合条件的 skill</div>
          ) : (
            grouped.map(([cat, items]) => {
              const isCollapsed = collapsed.has(cat);
              return (
                <section key={cat} className="sks-cat-group">
                  <button
                    type="button"
                    className="sks-cat-head"
                    onClick={() => toggleCat(cat)}
                    aria-expanded={!isCollapsed}
                  >
                    <span className={"sks-cat-caret" + (isCollapsed ? " collapsed" : "")}>▾</span>
                    <span className="sks-cat-name">{cat}</span>
                    <span className="sks-cat-count">{items.length}</span>
                  </button>
                  {!isCollapsed && items.map((s) => (
                    <div
                      key={s.name}
                      className={"sks-list-row" + (s.name === selected ? " active" : "")}
                      onClick={() => pickSkill(s.name)}
                      role="button"
                      tabIndex={0}
                      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && pickSkill(s.name)}
                    >
                      <div>
                        <div className="title">
                          {s.name}
                          {s.pinned && <span className="sks-badge">PINNED</span>}
                        </div>
                        {(s.description_zh || s.description) && (
                          <div className="desc">{s.description_zh || s.description}</div>
                        )}
                      </div>
                      <div className="date">{fmtDate(s.updated_at)}</div>
                    </div>
                  ))}
                </section>
              );
            })
          )}
        </div>

        {selected && (
          <Suspense fallback={<aside className="sks-detail"><div className="sks-loading">编辑器加载中…</div></aside>}>
            <SkillEditor
              name={selected}
              onClose={clearSelection}
              onDeleted={() => setRefreshKey((k) => k + 1)}
            />
          </Suspense>
        )}
      </div>
    </div>
  );
}
