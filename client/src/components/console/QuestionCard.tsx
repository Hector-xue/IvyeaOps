/**
 * 选项卡 —— 模型拿不准时把岔路摆出来让人点。
 *
 * 数据来自 agent 的 `question_request` 事件（`ask_user_question` 工具）。它和审批卡
 * 是两回事，别混：审批卡问的是"这个写操作准不准"，答不了就拒绝；选项卡问的是
 * "两条路走哪条"，**没人答也必须往下走** —— 五分钟后 agent 自己按标了推荐的那项
 * 继续，并在收尾总结里说明那一项是替用户定的。
 *
 * 所以这张卡的两件事必须诚实：
 *   ① 倒计时写清楚"到点会按推荐项继续"，不是"到点作废"；
 *   ② 推荐项当场标出来 —— 用户不选的时候，他至少知道默认会发生什么。
 */
import { useEffect, useMemo, useState } from "react";
import type { IvyeaQuestionRequest } from "../../api/ivyeaAgent";

function fmtLeft(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m > 0 ? `${m} 分 ${String(s).padStart(2, "0")} 秒` : `${s} 秒`;
}

export default function QuestionCard({
  request,
  onAnswer,
  answered,
  autoChosen,
}: {
  request: IvyeaQuestionRequest;
  onAnswer: (answers: Record<string, string>) => void;
  /** 已经选过：转成只读回执。 */
  answered?: Record<string, string>;
  /** 超时了，已按推荐项继续。 */
  autoChosen?: boolean;
}) {
  const questions = useMemo(() => request.questions || [], [request.questions]);
  const [picked, setPicked] = useState<Record<number, string[]>>({});

  const [left, setLeft] = useState<number | null>(null);
  useEffect(() => {
    if (!request.expires_at) { setLeft(null); return; }
    const tick = () => setLeft(Math.max(0, Math.round(request.expires_at! - Date.now() / 1000)));
    tick();
    const t = window.setInterval(tick, 1000);
    return () => window.clearInterval(t);
  }, [request.expires_at]);

  const toggle = (qi: number, label: string, multi: boolean) => {
    setPicked((prev) => {
      const cur = prev[qi] || [];
      if (!multi) return { ...prev, [qi]: [label] };
      return { ...prev, [qi]: cur.includes(label) ? cur.filter((l) => l !== label) : [...cur, label] };
    });
  };

  const answers = useMemo(() => {
    const out: Record<string, string> = {};
    questions.forEach((q, i) => {
      const sel = picked[i] || [];
      if (sel.length) out[q.question] = sel.join(", ");
    });
    return out;
  }, [questions, picked]);

  const ready = questions.length > 0 && Object.keys(answers).length === questions.length;

  if (answered || autoChosen) {
    const rows = answered && Object.keys(answered).length
      ? Object.entries(answered)
      : questions.map((q) => [q.question,
          (q.options.find((o) => o.recommended) || q.options[0])?.label || ""] as [string, string]);
    return (
      <div className={"cs-question cs-question-done" + (autoChosen ? " is-auto" : "")}>
        <span className="cs-icon">{autoChosen ? "⏱" : "✓"}</span>
        <div>
          <b>{autoChosen ? "没等到选择，已按推荐项继续" : "已按你的选择继续"}</b>
          {rows.map(([q, a]) => (
            <div key={q} className="cs-question-echo">{q} → <b>{a}</b></div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="cs-question">
      <div className="cs-question-head">
        <span className="cs-icon">?</span>
        <b>有个地方拿不准，你来定</b>
        {left !== null && (
          <span className="cs-question-timer" title="到点不会作废，会按推荐项继续">
            {left > 0 ? `${fmtLeft(left)}后按推荐项继续` : "正在按推荐项继续…"}
          </span>
        )}
      </div>

      {questions.map((q, qi) => (
        <div className="cs-question-block" key={q.question}>
          <div className="cs-question-text">
            {q.header && <span className="cs-question-tag">{q.header}</span>}
            {q.question}
            {q.multi_select && <span className="cs-question-multi">可多选</span>}
          </div>
          <div className="cs-question-options">
            {q.options.map((o) => {
              const on = (picked[qi] || []).includes(o.label);
              return (
                <button
                  key={o.label}
                  type="button"
                  className={"cs-question-opt" + (on ? " is-on" : "") + (o.recommended ? " is-rec" : "")}
                  onClick={() => toggle(qi, o.label, Boolean(q.multi_select))}
                >
                  <span className="cs-question-opt-label">
                    {o.label}
                    {o.recommended && <em>推荐</em>}
                  </span>
                  {o.description && <span className="cs-question-opt-desc">{o.description}</span>}
                </button>
              );
            })}
          </div>
        </div>
      ))}

      <div className="cs-question-actions">
        <button type="button" className="cs-btn cs-btn-primary" disabled={!ready}
                onClick={() => onAnswer(answers)}>
          就按这个做
        </button>
        <span className="cs-question-hint">
          不选也没关系 —— 到点按推荐项继续，收尾时会告诉你哪几项是自动定的。
        </span>
      </div>
    </div>
  );
}
