import { useEffect, useState } from "react";
import { fetchAlerts, type AlertItem } from "../../../api/home";
import type { DataSourceId } from "../../../lib/dataSource";
import { symOfMarketplace } from "../lingxingCurrency";

const METRIC_LABEL: Record<string, string> = {
  price: "价格", bsr: "BSR", est_sales: "月销", rating: "评分",
  review_count: "评论", inventory: "库存", coupon: "Coupon", deal: "活动",
};

function fmtNum(v: number | boolean | null, metric: string, full = false, mkt = "US"): string {
  if (typeof v === "boolean") return v ? "有" : "无";
  if (v == null) return "—";
  if (metric === "price") return v <= 0 ? "无价" : symOfMarketplace(mkt) + v.toFixed(2);
  if (metric === "bsr") return "#" + Math.round(v).toLocaleString("en-US");
  if (v >= 1000 && !full) return (v / 1000).toFixed(1) + "K";
  return Math.round(v).toLocaleString("en-US");
}

/** 前后两个值。K 缩写会把 4,988→5,012 显示成「5.0K→5.0K」—— 看着像没变却被列为异动，
 *  这种情况改用完整数字。 */
function fmtPair(from: AlertItem["from"], to: AlertItem["to"], metric: string, mkt: string): string {
  let a = fmtNum(from, metric, false, mkt), b = fmtNum(to, metric, false, mkt);
  if (a === b) { a = fmtNum(from, metric, true, mkt); b = fmtNum(to, metric, true, mkt); }
  return `${a} → ${b}`;
}

function describe(a: AlertItem): { text: string; up: boolean } {
  const label = METRIC_LABEL[a.metric] ?? a.metric;
  if (a.metric === "coupon" || a.metric === "deal") {
    const on = a.to === true;
    return { text: `${label}${on ? "上线" : "下线"}`, up: on };
  }
  const up = typeof a.diff === "number" ? a.diff > 0 : false;
  return {
    text: `${label} ${fmtPair(a.from, a.to, a.metric, a.marketplace)}`,
    up,
  };
}

export default function AlertStrip({ reloadKey, dataSource, marketplace, onJump }: {
  reloadKey: number;
  dataSource: DataSourceId;
  marketplace: string;
  onJump?: (kind: "competitor" | "own", marketplace: string) => void;
}) {
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    fetchAlerts(dataSource)
      .then(setAlerts)
      .catch(() => setAlerts([]))
      .finally(() => setLoaded(true));
  }, [reloadKey, dataSource]);

  if (!loaded) return null;

  if (alerts.length === 0) {
    return (
      <div className="home-alerts home-alerts-empty">
        暂无异动。监控的 ASIN 有两次以上快照后，价格、排名、活动的变化会出现在这里
      </div>
    );
  }

  return (
    <div className="home-alerts">
      <span className="home-alerts-label">异动</span>
      <div className="home-alerts-track">
        {alerts.map((a, i) => {
          const { text, up } = describe(a);
          return (
            <button
              key={i}
              className={"home-alert-chip " + (up ? "up" : "down")}
              title={`${a.label || a.asin} · ${a.marketplace}`}
              onClick={() => onJump?.(a.kind, a.marketplace)}
            >
              <span className="home-alert-asin">
                {a.label || a.asin}
                {a.kind === "own" && <span className="home-alert-own">自有</span>}
                {a.marketplace !== marketplace && <span className="home-alert-mkt">{a.marketplace}</span>}
              </span>
              <span className="home-alert-dir">{up ? "▲" : "▼"}</span>
              <span className="home-alert-text">{text}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
