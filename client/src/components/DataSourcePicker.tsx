import { useEffect, useRef, useState } from "react";
import { DATA_SOURCES, dataSourceMeta, type DataSourceId, type DataSourceSurface } from "../lib/dataSource";

// Shared market-data source dropdown for 首页 / 市场调研 / 打法推荐.
// Availability is evaluated per surface so a source can never appear active on
// a page whose backend would silently use a different provider.
export default function DataSourcePicker({
  value,
  onChange,
  disabled,
  surface = "market",
}: {
  value: DataSourceId;
  onChange: (id: DataSourceId) => void;
  disabled?: boolean;
  surface?: DataSourceSurface;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open]);

  const cur = dataSourceMeta(value, surface);

  return (
    <div className="market-mkt-wrap" ref={ref}>
      <button
        className="market-mkt-btn"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        title="选择数据源"
      >
        <span className="market-mkt-code">数据源：{cur.name}</span>
        <span className="market-mkt-arrow">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <div className="market-mkt-dropdown">
          {DATA_SOURCES.map((source) => {
            const s = dataSourceMeta(source.id, surface);
            return (
              <button
                key={s.id}
                className={"market-mkt-option" + (value === s.id ? " active" : "")}
                disabled={!s.ready}
                onClick={() => { onChange(s.id); setOpen(false); }}
              >
                <span className="market-mkt-option-name">{s.name}</span>
                {!s.ready && (
                  <span style={{ marginLeft: "auto", fontSize: "var(--fs-10)", color: "var(--amber, #fbbf24)" }}>
                    {s.note || "当前页面暂不支持"}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
