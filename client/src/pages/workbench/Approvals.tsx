/**
 * 待审批 —— 手机上就能点同意/拒绝的那一页。
 *
 * 这一页是这个产品对 WorkBuddy「IM 远程下指令」的回答，而且是更安全的版本：
 * **只审批、不下达**。人不在电脑前时，能做的仅仅是对已经排到面前的操作说同意或
 * 拒绝，没法从手机上凭空发起一个新指令 —— 少了一整类"手滑把生产改了"的可能。
 *
 * 为什么要单独一页
 * ----------------
 * 审批此前只能按会话查：要处理一条，得先知道它在哪个会话、点进去、再在长长的
 * 对话里找到那张卡片。在电脑前还能忍，在手机上等于做不到。
 *
 * 决定走的是**和桌面端完全同一个接口**（`ivyeaChatPermission`）——那条路上有
 * 归属校验、有"另一个页签已经点过"的 409、有"agent 确认收下才留痕"。
 * 另起一条并行路径迟早会和它分叉，而分叉的代价是一条本该被拒的操作被放行。
 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ivyeaChatPermission, ivyeaPendingApprovals, type PendingApproval,
} from "../../api/ivyeaAgent";
import { errText } from "../../lib/errText";

function ago(ts: number): string {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return `${s} 秒前`;
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return `${Math.floor(s / 86400)} 天前`;
}

export default function Approvals() {
  const navigate = useNavigate();
  const [items, setItems] = useState<PendingApproval[] | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const [done, setDone] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    try {
      setItems(await ivyeaPendingApprovals());
      setErr("");
    } catch (e) {
      setErr(errText(e, "读不到待审批列表"));
    }
  }, []);

  useEffect(() => {
    void load();
    // agent 那一步是**阻塞等待**的，还带超时。这里刷得勤一点，免得用户看着一条
    // 早已超时失效的卡片纠结要不要点。
    const t = window.setInterval(load, 15_000);
    return () => window.clearInterval(t);
  }, [load]);

  const decide = async (a: PendingApproval, choice: "approve" | "deny") => {
    setBusy(a.request_id);
    try {
      await ivyeaChatPermission({
        request_id: a.request_id, session_id: a.session_id, choice,
      });
      setDone((d) => ({ ...d, [a.request_id]: choice }));
      await load();
    } catch (e) {
      // 409 = 另一个页签已经点过 / 已超时。这不是错误，是"你来晚了"，
      // 说清楚比红一片有用。
      setDone((d) => ({ ...d, [a.request_id]: "gone" }));
      setErr(errText(e, "这条审批已经失效"));
      await load();
    } finally { setBusy(""); }
  };

  const list = items || [];

  return (
    <div className="ap-wrap">
      <div className="ap-head">
        <b>待审批</b>
        <span>{items === null ? "加载中…" : `${list.length} 条等你决定`}</span>
      </div>

      {err && <div className="ap-err">{err}</div>}

      {items !== null && list.length === 0 && (
        <div className="ap-empty">
          没有等待确认的操作。
          <div className="ap-empty-sub">
            Agent 要动真实数据（改投放、写文件、跑命令）时会停下来问你，
            那时候这里会出现一条，你配的通知渠道也会响一声。
          </div>
        </div>
      )}

      {list.map((a) => {
        const state = done[a.request_id];
        return (
          <div className={"ap-card" + (state ? " ap-card-done" : "")} key={a.request_id}>
            <div className="ap-card-top">
              <span className="ap-op">{a.op_type || "操作"}</span>
              <span className="ap-time">{ago(a.requested_at)}</span>
            </div>
            <div className="ap-title">{a.title || "（这一步没有给出说明）"}</div>

            {state ? (
              <div className="ap-state">
                {state === "approve" ? "✓ 已同意" : state === "deny" ? "× 已拒绝" : "— 已失效"}
              </div>
            ) : (
              <div className="ap-actions">
                {/* 拒绝放左边、同意放右边，且同意不是默认焦点 —— 这一页是在
                    手机上单手操作的，误触的代价是放行一个本该被拦的操作。 */}
                <button className="ap-btn ap-deny" disabled={!!busy}
                        onClick={() => decide(a, "deny")}>
                  拒绝
                </button>
                <button className="ap-btn ap-approve" disabled={!!busy}
                        onClick={() => decide(a, "approve")}>
                  {busy === a.request_id ? "提交中…" : "同意"}
                </button>
              </div>
            )}

            <button className="ap-link" onClick={() => navigate("/console")}>
              去看完整上下文 →
            </button>
          </div>
        );
      })}
    </div>
  );
}
