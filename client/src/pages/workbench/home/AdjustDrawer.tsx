import { useEffect, useRef, useState } from "react";
import {
  confirmAdjust, createAdjust, fetchTicket, rejectAdjust,
  type AdjustPayload, type Ticket,
} from "../../../api/cockpit";
import { errText } from "../../../lib/errText";

/**
 * 就地调整抽屉 —— 从看板发起一次广告改动。
 *
 * 界面上必须让人一眼看懂**这次改动走的是哪条路**：
 *
 *   小幅止血（降预算/降bid/暂停，幅度 ≤ 上限）→ 快车道：免 AI 复核，直接等你确认
 *   其余（提预算/加bid/大幅调整）           → 全复核：三个独立 AI 视角都过了才轮到你
 *
 * 两条路的**终点是一样的**：都要你亲手点「确认执行」，执行前都会重跑护栏、
 * 抓回滚快照。快车道省掉的只有中间那十几秒的 AI 复核，没有省掉任何一道闸。
 */

const STATUS_TEXT: Record<string, string> = {
  reviewing: "AI 复核中…",
  awaiting_human: "等你确认",
  guardrail_blocked: "被护栏拦下",
  review_rejected: "AI 复核不通过",
  executed: "已执行",
  failed: "执行失败",
  rejected: "已放弃",
  rolled_back: "已回滚",
};

export default function AdjustDrawer({ payload, onClose, onDone }: {
  payload: AdjustPayload & { unit?: string; label?: string };
  onClose: () => void;
  onDone?: (t: Ticket) => void;
}) {
  const [value, setValue] = useState<number>(payload.new_value ?? 0);
  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const poll = useRef<number | null>(null);

  const stopPolling = () => { if (poll.current) { window.clearInterval(poll.current); poll.current = null; } };
  useEffect(() => stopPolling, []);

  const submit = async () => {
    setBusy(true); setError("");
    try {
      const t = await createAdjust({ ...payload, new_value: value });
      setTicket(t);
      // 工单的护栏/复核在后台跑，轮询到出结论为止。
      stopPolling();
      poll.current = window.setInterval(async () => {
        try {
          const next = await fetchTicket(t.id);
          setTicket(next);
          if (next.status !== "reviewing") stopPolling();
        } catch { stopPolling(); }
      }, 1500);
    } catch (e: any) {
      setError(errText(e, "创建失败"));
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    if (!ticket) return;
    setBusy(true); setError("");
    try {
      const t = await confirmAdjust(ticket.id);
      setTicket(t);
      onDone?.(t);
    } catch (e: any) {
      setError(errText(e, "执行失败"));
    } finally { setBusy(false); }
  };

  const drop = async () => {
    if (!ticket) { onClose(); return; }
    try { await rejectAdjust(ticket.id); } catch { /* 放弃失败不必打扰用户 */ }
    onClose();
  };

  const pct = payload.cur_value
    ? ((value - payload.cur_value) / payload.cur_value) * 100
    : null;
  const fast = ticket?.fast_lane;

  return (
    <div className="cp-drawer-backdrop" onClick={onClose}>
      <div className="cp-drawer" onClick={e => e.stopPropagation()}>
        <div className="cp-drawer-hd">
          <span className="cp-drawer-title">{payload.label || "调整广告"}</span>
          <button className="cp-drawer-x" onClick={onClose}>×</button>
        </div>

        <div className="cp-drawer-target">
          <span className="cp-drawer-name">{payload.target_name || payload.target_id}</span>
          {payload.rationale && <div className="cp-drawer-why">{payload.rationale}</div>}
        </div>

        {!ticket && (
          <>
            <div className="cp-drawer-field">
              <label>当前值</label>
              <span className="cp-drawer-cur">{payload.cur_value ?? "—"}{payload.unit || ""}</span>
            </div>
            <div className="cp-drawer-field">
              <label>改为</label>
              <input type="number" step="0.01" value={value}
                     onChange={e => setValue(Number(e.target.value))} />
              {pct != null && (
                <span className={"cp-drawer-pct" + (pct < 0 ? " down" : " up")}>
                  {pct > 0 ? "+" : ""}{pct.toFixed(1)}%
                </span>
              )}
            </div>
            <div className="cp-drawer-hint">
              提交后先过确定性护栏；小幅止血动作免 AI 复核，其余走三重复核。
              <b>无论哪条路，都要你再点一次「确认执行」才会真的改。</b>
            </div>
            <div className="cp-drawer-actions">
              <button className="cp-btn" onClick={onClose}>取消</button>
              <button className="cp-btn primary" onClick={submit} disabled={busy}>
                {busy ? "提交中…" : "提交"}
              </button>
            </div>
          </>
        )}

        {ticket && (
          <>
            <div className={`cp-ticket-status cp-ticket-${ticket.status}`}>
              {STATUS_TEXT[ticket.status] || ticket.status}
            </div>

            {fast && (
              <div className={"cp-lane" + (fast.eligible ? " fast" : " full")}>
                <span className="cp-lane-tag">{fast.eligible ? "快车道" : "全复核"}</span>
                <span className="cp-lane-why">{fast.reason}</span>
              </div>
            )}

            {ticket.guardrail && (
              <ul className="cp-checks">
                {ticket.guardrail.checks.map(c => (
                  <li key={c.name} className={c.ok ? "ok" : "bad"}>
                    <span>{c.ok ? "✓" : "✗"}</span> {c.detail || c.name}
                  </li>
                ))}
              </ul>
            )}

            {ticket.reviews && (
              <div className="cp-reviews">
                {(ticket.reviews.reviews || []).map((r: any, i: number) => (
                  <div key={i} className={"cp-review" + (r.approved ? " ok" : " bad")}>
                    <b>{r.persona || `复核 ${i + 1}`}</b>
                    <span>{r.approved ? "通过" : "否决"}</span>
                    <p>{r.reason || r.summary || ""}</p>
                  </div>
                ))}
              </div>
            )}

            {ticket.error && <div className="cp-error">{ticket.error}</div>}
            {error && <div className="cp-error">{error}</div>}

            <div className="cp-drawer-actions">
              <button className="cp-btn" onClick={drop}>放弃</button>
              <button className="cp-btn primary" onClick={confirm}
                      disabled={busy || ticket.status !== "awaiting_human"}>
                {busy ? "执行中…" : "确认执行"}
              </button>
            </div>
            {ticket.status === "executed" && (
              <div className="cp-drawer-hint">
                已写入领星。回滚快照已保存，可在「领星 → 受控操作」里回滚。
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
