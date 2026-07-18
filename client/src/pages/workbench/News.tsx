import { useCallback, useEffect, useMemo, useState } from "react";
import SheetSelect from "../../components/SheetSelect";
import {
  DatesResponse,
  NewsCategory,
  NewsDay,
  NewsItem,
  getNewsDay,
  listNewsDates,
  refreshNews,
} from "../../api/news";

const CATS: { key: NewsCategory | "all"; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "ai_industry", label: "AI 动态" },
  { key: "amazon_seller", label: "亚马逊卖家" },
];

const CAT_STYLE: Record<string, { cls: string; label: string }> = {
  ai_industry: { cls: "tp", label: "AI" },
  amazon_seller: { cls: "ta", label: "亚马逊" },
};

function fmtDate(iso: string): string {
  try {
    const d = new Date(iso + "T00:00:00");
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const diff = Math.round(
      (today.getTime() - d.getTime()) / (1000 * 60 * 60 * 24),
    );
    if (diff === 0) return "今天";
    if (diff === 1) return "昨天";
    if (diff < 7) return `${diff} 天前`;
    return iso.slice(5);
  } catch {
    return iso;
  }
}

function fmtGenerated(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function HeatBadge({ n }: { n: number }) {
  const v = Math.max(0, Math.min(5, n));
  const color =
    v >= 5 ? "var(--red)" : v >= 4 ? "var(--amber)" : v >= 3 ? "var(--t2)" : "var(--t3)";
  return (
    <span
      title={`热度 ${v}/5（AI 评估的重要度）`}
      style={{
        fontSize: 9,
        color,
        border: `1px solid ${v >= 4 ? "currentColor" : "var(--b)"}`,
        borderRadius: 3,
        padding: "1px 5px",
        letterSpacing: ".05em",
        whiteSpace: "nowrap",
      }}
    >
      🔥 {v}
    </span>
  );
}

function NewsCard({
  n,
  activeTag,
  onTagClick,
}: {
  n: NewsItem;
  activeTag: string | null;
  onTagClick: (t: string) => void;
}) {
  const tag = CAT_STYLE[n.category] ?? { cls: "tb-tag", label: n.category };
  return (
    <div
      className="ni-item"
      style={{
        alignItems: "flex-start",
        padding: "10px 0",
        borderLeft: n.is_official ? "2px solid var(--amber)" : "none",
        paddingLeft: n.is_official ? 10 : 0,
        marginLeft: n.is_official ? -12 : 0,
      }}
    >
      <div
        className={"ni-dot" + (n.importance >= 4 ? " fresh" : "")}
        style={{ marginTop: 7 }}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <a
          href={n.url}
          target="_blank"
          rel="noopener noreferrer"
          className="ni-title"
          style={{
            display: "block",
            color: "var(--t)",
            textDecoration: "none",
            fontSize: 12,
            fontWeight: 500,
            marginBottom: 4,
          }}
        >
          {n.is_official && (
            <span
              style={{
                fontSize: 9,
                color: "var(--amber)",
                marginRight: 6,
                padding: "1px 5px",
                border: "1px solid rgba(251,191,36,.3)",
                borderRadius: 3,
                verticalAlign: "1px",
              }}
            >
              OFFICIAL
            </span>
          )}
          {n.title}
        </a>
        {n.summary_zh && (
          <div
            style={{
              fontSize: 10.5,
              color: "var(--t2)",
              lineHeight: 1.55,
              marginBottom: 5,
            }}
          >
            {n.summary_zh}
          </div>
        )}
        {n.reason_zh && (
          <div
            style={{
              fontSize: 10,
              color: "var(--acc)",
              lineHeight: 1.5,
              marginBottom: 5,
              opacity: 0.85,
            }}
          >
            💡 {n.reason_zh}
          </div>
        )}
        <div className="ni-meta" style={{ flexWrap: "wrap", rowGap: 3 }}>
          <span className={"tag " + tag.cls}>{tag.label}</span>
          {(n.tags ?? []).map((t) => (
            <button
              key={t}
              onClick={() => onTagClick(t)}
              title={activeTag === t ? "取消标签筛选" : `只看「${t}」`}
              style={{
                fontSize: 9,
                color: activeTag === t ? "var(--acc)" : "var(--t3)",
                border: `1px solid ${activeTag === t ? "var(--acc)" : "var(--b)"}`,
                borderRadius: 3,
                padding: "0 5px",
                background: "none",
                cursor: "pointer",
                lineHeight: "16px",
              }}
            >
              #{t}
            </button>
          ))}
          <span style={{ color: "var(--t3)" }}>{n.source}</span>
          {n.published_at && (
            <span style={{ color: "var(--t3)" }}>
              · {n.published_at.slice(5, 16).replace("T", " ")}
            </span>
          )}
          <span style={{ flex: 1 }} />
          <HeatBadge n={n.importance} />
          <a
            href={n.url}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              fontSize: 9,
              color: "var(--acc)",
              textDecoration: "none",
              marginLeft: 6,
            }}
          >
            原文 ↗
          </a>
        </div>
      </div>
    </div>
  );
}

function SectionHeader({
  icon,
  label,
  count,
  subtle,
}: {
  icon: string;
  label: string;
  count: number;
  subtle?: boolean;
}) {
  return (
    <div
      style={{
        fontSize: 10,
        color: subtle ? "var(--t3)" : "var(--amber)",
        letterSpacing: ".1em",
        padding: "10px 0 6px 0",
        borderBottom: "1px solid var(--b)",
        marginBottom: 4,
        display: "flex",
        alignItems: "center",
        gap: 6,
      }}
    >
      <span style={{ fontSize: 12 }}>{icon}</span>
      <span>{label}</span>
      <span style={{ color: "var(--t3)", fontWeight: 400 }}>· {count} 条</span>
    </div>
  );
}

export default function News() {
  const [dates, setDates] = useState<DatesResponse | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [day, setDay] = useState<NewsDay | null>(null);
  const [cat, setCat] = useState<NewsCategory | "all">("all");
  const [q, setQ] = useState("");
  const [activeTag, setActiveTag] = useState<string | null>(null);
  const [pickedOnly, setPickedOnly] = useState(false);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);

  const loadDates = useCallback(async () => {
    try {
      const r = await listNewsDates();
      setDates(r);
      setPicked((prev) => prev ?? r.latest);
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e.message ?? "日期加载失败");
    }
  }, []);

  useEffect(() => {
    loadDates();
  }, [loadDates]);

  const loadDay = useCallback(async (target: string | null) => {
    setLoading(true);
    setErr(null);
    try {
      const d = await getNewsDay({ date: target ?? undefined });
      setDay(d);
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e.message ?? "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadDay(picked);
  }, [picked, loadDay]);

  const filtered = useMemo<NewsItem[]>(() => {
    if (!day) return [];
    let items = cat === "all" ? day.items : day.items.filter((i) => i.category === cat);
    if (pickedOnly) items = items.filter((i) => i.importance >= 4);
    if (activeTag) items = items.filter((i) => (i.tags ?? []).includes(activeTag));
    const kw = q.trim().toLowerCase();
    if (kw) {
      items = items.filter((i) =>
        [i.title, i.summary_zh, i.reason_zh ?? "", i.source, ...(i.tags ?? [])]
          .join(" ")
          .toLowerCase()
          .includes(kw),
      );
    }
    return items;
  }, [day, cat, q, activeTag, pickedOnly]);

  const onTagClick = useCallback(
    (t: string) => setActiveTag((prev) => (prev === t ? null : t)),
    [],
  );

  const { officialItems, regularItems } = useMemo(() => {
    const off = filtered.filter((i) => i.is_official);
    const reg = filtered.filter((i) => !i.is_official);
    // Official: newest first
    off.sort((a, b) => (b.published_at ?? "").localeCompare(a.published_at ?? ""));
    // Regular: importance desc, then published_at desc
    reg.sort((a, b) => {
      if (b.importance !== a.importance) return b.importance - a.importance;
      return (b.published_at ?? "").localeCompare(a.published_at ?? "");
    });
    return { officialItems: off, regularItems: reg };
  }, [filtered]);

  const counts = useMemo(() => {
    if (!day) return { ai_industry: 0, amazon_seller: 0, total: 0, official: 0 };
    const ai = day.items.filter((i) => i.category === "ai_industry").length;
    const az = day.items.filter((i) => i.category === "amazon_seller").length;
    const off = day.items.filter((i) => i.is_official).length;
    return { ai_industry: ai, amazon_seller: az, total: day.items.length, official: off };
  }, [day]);

  const onRefresh = async () => {
    setRefreshing(true);
    setFlash(null);
    try {
      const r = await refreshNews();
      setFlash(r.message);
      // Poll for new data every 10s for up to 60s
      if (r.triggered) {
        let attempts = 0;
        const poll = setInterval(async () => {
          attempts += 1;
          await loadDates();
          await loadDay(picked);
          if (attempts >= 6) clearInterval(poll);
        }, 10000);
      }
    } catch (e: any) {
      setFlash(e?.response?.data?.detail ?? e.message ?? "刷新失败");
    } finally {
      setRefreshing(false);
      setTimeout(() => setFlash(null), 30000);
    }
  };

  return (
    <div>
      <div className="ptitle">/ 资讯中心 · AI + 亚马逊卖家每日动态</div>

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 6,
          marginBottom: 12,
          alignItems: "center",
        }}
      >
        {CATS.map((c) => {
          const n =
            c.key === "all"
              ? counts.total
              : c.key === "ai_industry"
              ? counts.ai_industry
              : counts.amazon_seller;
          return (
            <button
              key={c.key}
              className={"rbtn" + (cat === c.key ? " ar" : "")}
              onClick={() => setCat(c.key)}
            >
              {c.label}
              <span style={{ opacity: 0.6, marginLeft: 4 }}>{n}</span>
            </button>
          );
        })}

        <button
          className={"rbtn" + (pickedOnly ? " ar" : "")}
          onClick={() => setPickedOnly((v) => !v)}
          title="只看热度 ≥ 4 的精选内容"
        >
          🔥 精选
        </button>

        <span style={{ flex: 1 }} />

        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索标题 / 摘要 / 标签…"
          style={{
            fontSize: 10,
            background: "none",
            border: "1px solid var(--b)",
            borderRadius: 4,
            color: "var(--t)",
            padding: "4px 8px",
            width: 160,
            outline: "none",
          }}
        />

        {dates && dates.dates.length > 0 && (
          <SheetSelect
            className="rbtn"
            value={picked ?? ""}
            onChange={setPicked}
            title="切换日期"
            options={dates.dates.map((d) => ({ value: d, label: `${fmtDate(d)} · ${d}` }))}
          />
        )}
        <button
          className="rbtn"
          data-tour="news-refresh"
          onClick={onRefresh}
          disabled={refreshing}
          title="触发后端重新抓取"
        >
          {refreshing ? "⟳ 刷新中…" : "⟳ 立即刷新"}
        </button>
      </div>

      {flash && (
        <div
          className="card"
          style={{ marginBottom: 10, fontSize: 10, color: "var(--t3)" }}
        >
          {flash}
        </div>
      )}

      {err && <div className="sks-error">⚠ {err}</div>}

      {day && (
        <div
          style={{
            display: "flex",
            gap: 12,
            fontSize: 9,
            color: "var(--t3)",
            marginBottom: 10,
            flexWrap: "wrap",
          }}
        >
          <span>
            日期 <b style={{ color: "var(--t2)" }}>{day.date}</b>
          </span>
          <span>
            生成 <b style={{ color: "var(--t2)" }}>{fmtGenerated(day.generated_at)}</b>
          </span>
          <span>
            共 <b style={{ color: "var(--t2)" }}>{day.items.length}</b> 条
            (官方 {counts.official})
          </span>
          <span style={{ color: "var(--t3)" }}>· 按需生成 · 保留最近 7 天</span>
          {activeTag && (
            <button
              onClick={() => setActiveTag(null)}
              style={{
                fontSize: 9,
                color: "var(--acc)",
                background: "none",
                border: "1px solid var(--acc)",
                borderRadius: 3,
                padding: "0 6px",
                cursor: "pointer",
              }}
            >
              #{activeTag} ✕
            </button>
          )}
          {day.notes && <span style={{ color: "var(--amber)" }}>· {day.notes}</span>}
        </div>
      )}

      <div className="card">
        {loading && (
          <div aria-busy="true" aria-live="polite" style={{ padding: 12, display: "grid", gap: 14 }}>
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i}>
                <div className="skeleton line md" />
                <div className="skeleton line lg" />
              </div>
            ))}
          </div>
        )}

        {!loading && filtered.length === 0 && (
          <div style={{ padding: 12, fontSize: 10, color: "var(--t3)" }}>
            {day && day.items.length === 0
              ? "该日尚未生成任何资讯。点击「立即刷新」现场抓取 RSS 并 AI 汇总。"
              : "当前筛选条件下无资讯（试试清掉搜索词 / 标签 / 精选开关）"}
          </div>
        )}

        {officialItems.length > 0 && (
          <>
            <SectionHeader
              icon="🏢"
              label="头部大厂官方"
              count={officialItems.length}
            />
            {officialItems.map((n, i) => (
              <NewsCard key={"off-" + i} n={n} activeTag={activeTag} onTagClick={onTagClick} />
            ))}
          </>
        )}

        {regularItems.length > 0 && (
          <>
            <SectionHeader
              icon="⚡"
              label="行业动态"
              count={regularItems.length}
              subtle
            />
            {regularItems.map((n, i) => (
              <NewsCard key={"reg-" + i} n={n} activeTag={activeTag} onTagClick={onTagClick} />
            ))}
          </>
        )}
      </div>
    </div>
  );
}
