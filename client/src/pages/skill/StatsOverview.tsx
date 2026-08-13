import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getStats, SkillStats } from "../../api/skill";
import { errText } from "../../lib/errText";

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

function fmtDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

export default function StatsOverview() {
  const [data, setData] = useState<SkillStats | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    getStats()
      .then((d) => alive && setData(d))
      .catch((e) => alive && setErr(errText(e, "加载失败")));
    return () => {
      alive = false;
    };
  }, []);

  if (err) return <div className="sks-error">⚠ {err}</div>;
  if (!data) return <div className="sks-loading">加载中…</div>;

  const categories = Object.entries(data.categories).sort((a, b) => b[1] - a[1]);

  return (
    <div>
      {/* Metric tiles */}
      <div className="sks-stats-grid">
        <div className="met">
          <div className="ml">SKILLS</div>
          <div className="mv">{data.total_skills}</div>
          <div className="ms neu">技能总数</div>
        </div>
        <div className="met">
          <div className="ml">CATEGORIES</div>
          <div className="mv">{Object.keys(data.categories).length}</div>
          <div className="ms neu">分类数</div>
        </div>
        <div className="met">
          <div className="ml">FOOTPRINT</div>
          <div className="mv">{fmtBytes(data.total_size_bytes)}</div>
          <div className="ms neu">磁盘占用</div>
        </div>
        <div className="met">
          <div className="ml">RECENT</div>
          <div className="mv">{data.recently_edited.length}</div>
          <div className="ms neu">7 天内更新</div>
        </div>
      </div>

      {/* Categories */}
      <div className="card mb14">
        <div className="ct">分类分布</div>
        {categories.length === 0 ? (
          <div style={{ color: "var(--t3)", fontSize: "var(--fs-11)" }}>尚无分类</div>
        ) : (
          <div className="sks-cat-grid">
            {categories.map(([name, count]) => (
              <Link
                key={name}
                to={`/skill/browse?category=${encodeURIComponent(name)}`}
                className="sks-cat-row"
                style={{ textDecoration: "none" }}
              >
                <span className="cat-n">{name || "(顶层)"}</span>
                <span className="cat-c">{count}</span>
              </Link>
            ))}
          </div>
        )}
      </div>

      {/* Recently edited */}
      <div className="card">
        <div className="ct">最近编辑</div>
        {data.recently_edited.length === 0 ? (
          <div style={{ color: "var(--t3)", fontSize: "var(--fs-11)" }}>空</div>
        ) : (
          data.recently_edited.map((s) => (
            <Link
              key={s.name}
              to={`/skill/browse?q=${encodeURIComponent(s.name)}`}
              className="sks-recent-row"
              style={{ textDecoration: "none" }}
            >
              <div>
                <span className="n">{s.name}</span>
                {s.pinned && <span className="sks-badge">PINNED</span>}
              </div>
              <span className="d">{fmtDate(s.updated_at)}</span>
            </Link>
          ))
        )}
      </div>
    </div>
  );
}
