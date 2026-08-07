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

export default function ApprovalCard({
  request,
  onDecide,
  decided,
}: {
  request: IvyeaPermissionRequest;
  onDecide: (choice: string) => void;
  /** 已经做过决定：卡片转为只读回执，不再可点。 */
  decided?: string;
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
  const options = request.options?.length
    ? request.options
    : [{ key: "approve", label: "确认继续" }, { key: "deny", label: "取消" }];

  if (decided) {
    const label = options.find((o) => o.key === decided)?.label || decided;
    return (
      <div className="cs-approval cs-approval-done">
        <span className="cs-icon">{decided === "deny" || decided === "abort" ? "✕" : "✓"}</span>
        <span>已{decided === "deny" || decided === "abort" ? "取消" : "确认"}：{label}</span>
      </div>
    );
  }

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
