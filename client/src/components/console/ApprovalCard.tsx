/**
 * 写操作确认卡 —— beili 那张「以下调整会在你确认后提交执行」。
 *
 * 数据来自 agent serve 的 `permission_request` 事件，它就是 CLI 里
 * `permission.request_intent` 那张审批卡的远程投影：同一个引擎、同一组选项
 * （批准本次 / 本会话同类都批准 / 拒绝 / 全部停止），只是把 TTY 菜单换成了网页按钮。
 *
 * 关键语义：**没点之前 agent 那一步是阻塞的，真实写入不会发生。** 所以这张卡
 * 必须永远给得出一个决策——超时、断连、组件卸载都由上层兜底成「拒绝」，
 * 绝不能把用户晾在一个已经没人接的确认框前面。
 */
import { useEffect, useMemo, useState } from "react";
import type { IvyeaPermissionRequest } from "../../api/ivyeaAgent";

/** 把预览文本按行拆成勾选清单：以 -/•/数字序号开头的行当条目，其余当说明。 */
function parsePreview(preview: string): { intro: string[]; items: string[] } {
  const intro: string[] = [];
  const items: string[] = [];
  for (const raw of (preview || "").split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const m = line.match(/^(?:[-*•]|\d+[.)、])\s*(.+)$/);
    if (m) items.push(m[1].trim());
    else if (items.length === 0) intro.push(line);
    else items.push(line);
  }
  return { intro, items };
}

const PRIMARY_KEYS = new Set(["approve", "session"]);

/** 没带选项的请求按"确认/取消"两档兜底 —— 卡片和回执要认同一份，不能各写一份。 */
const FALLBACK_OPTIONS = [{ key: "approve", label: "确认继续" }, { key: "deny", label: "取消" }];

const optionsOf = (req: IvyeaPermissionRequest) =>
  req.options?.length ? req.options : FALLBACK_OPTIONS;

const isDeny = (choice: string) => choice === "deny" || choice === "abort";

export type ApprovalEntry = { req: IvyeaPermissionRequest; decision?: string };

export type ApprovalGroup =
  | { kind: "pending"; key: string; req: IvyeaPermissionRequest }
  | { kind: "done"; key: string; decision: string; label: string; count: number };

/**
 * 把**连着的、同一种决定**的回执并成一条。
 *
 * 「本会话同类都批准」这一档下，一轮里 agent 会连着批十几次，每次落一条回执；
 * 一条一张卡的话屏幕就全是它了（用户原话："授权十来次，那整个屏幕都被这个给
 * 占满了"）。回执本身只有"批过了"这一点信息量，连着的同一种决定合并成
 * 「已确认 3 次：本会话同类都批准」不丢任何东西。
 *
 * **只并连着的**：中间夹一张还没决定的卡就断开。这一列是时间线，把它前后的东西
 * 并到一起就等于在撒谎。
 */
export function groupApprovals(rows: ApprovalEntry[]): ApprovalGroup[] {
  const out: ApprovalGroup[] = [];
  rows.forEach((row, i) => {
    if (!row.decision) {
      out.push({ kind: "pending", key: row.req.request_id || `p${i}`, req: row.req });
      return;
    }
    const last = out[out.length - 1];
    if (last?.kind === "done" && last.decision === row.decision) { last.count += 1; return; }
    const label = optionsOf(row.req).find((o) => o.key === row.decision)?.label || row.decision;
    out.push({ kind: "done", key: row.req.request_id || `d${i}`,
               decision: row.decision, label, count: 1 });
  });
  return out;
}

/**
 * 已决策的回执 —— **一行，不是一张卡**。
 *
 * 它原来直接套 .cs-approval：column 布局 + 边框 + 11px 内边距，于是 ✓ 和文字各
 * 占一行，一条回执就是一张 ~90px 高的框。回执要传达的只有"批过了"，一行足够。
 */
export function ApprovalReceipt({ decision, label, count = 1 }: {
  decision: string;
  label: string;
  /** 合并了几条（见 groupApprovals）。1 时不显示次数。 */
  count?: number;
}) {
  const denied = isDeny(decision);
  return (
    <div className={"cs-approval-done" + (denied ? " is-deny" : "")}>
      <span className="cs-icon">{denied ? "✕" : "✓"}</span>
      <span>已{denied ? "取消" : "确认"}{count > 1 ? ` ${count} 次` : ""}：{label}</span>
    </div>
  );
}

export default function ApprovalCard({
  request,
  onDecide,
}: {
  request: IvyeaPermissionRequest;
  onDecide: (choice: string) => void;
}) {
  const { intro, items } = useMemo(() => parsePreview(request.preview), [request.preview]);
  // 勾选状态目前只影响「用户读没读」的确认感：agent 侧的这一步是整体批准 /
  // 整体拒绝，不支持逐条裁剪。所以默认全勾，取消任一条即视为不批准整步——
  // 与其假装能部分执行，不如让语义诚实。
  const [checked, setChecked] = useState<Record<number, boolean>>({});
  useEffect(() => {
    const init: Record<number, boolean> = {};
    items.forEach((_, i) => { init[i] = true; });
    setChecked(init);
  }, [items]);

  const [left, setLeft] = useState<number | null>(null);
  useEffect(() => {
    if (!request.expires_at) { setLeft(null); return; }
    const tick = () => setLeft(Math.max(0, Math.round(request.expires_at! - Date.now() / 1000)));
    tick();
    const t = window.setInterval(tick, 1000);
    return () => window.clearInterval(t);
  }, [request.expires_at]);

  const allChecked = items.length === 0 || items.every((_, i) => checked[i]);
  const options = optionsOf(request);

  return (
    <div className={"cs-approval" + (request.destructive ? " cs-destructive" : "")}>
      <div className="cs-approval-head">
        <span className="cs-icon">⚑</span>
        <b>{request.title || "以下操作会在你确认后提交执行，请核对"}</b>
        {left !== null && <span className="cs-approval-timer">{left}s 后自动取消</span>}
      </div>

      {intro.length > 0 && (
        <div className="cs-approval-intro">
          {intro.map((line, i) => <div key={i}>{line}</div>)}
        </div>
      )}

      {items.length > 0 && (
        <ul className="cs-approval-list">
          {items.map((it, i) => (
            <li key={i}>
              <label>
                <input
                  type="checkbox"
                  checked={!!checked[i]}
                  onChange={(e) => setChecked((p) => ({ ...p, [i]: e.target.checked }))}
                />
                <span>{it}</span>
              </label>
            </li>
          ))}
        </ul>
      )}

      {!allChecked && (
        <div className="cs-approval-warn">
          这一步是整体批准或整体拒绝，不能只执行其中几条。要改内容请取消后重新提要求。
        </div>
      )}

      <div className="cs-approval-actions">
        {options.map((o) => (
          <button
            key={o.key}
            type="button"
            className={"cs-btn" + (PRIMARY_KEYS.has(o.key) && allChecked ? " cs-btn-primary" : "")}
            disabled={PRIMARY_KEYS.has(o.key) && !allChecked}
            onClick={() => onDecide(o.key)}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}
