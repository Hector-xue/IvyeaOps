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
 *
 * 除了给定的选项，每一问还有两条出路（和 /agents 那张面板、终端菜单三处对齐）：
 *   · **其他…** —— 给的选项都不对时能写自己的话。后端对答案的值不做校验（见
 *     ask._clean_answers「值原样保留」），所以自由文本走的是同一条路，不需要
 *     任何特殊协议；
 *   · **跳过这题** —— 提交时**不带**这一问。后端看到缺了哪问，就按推荐项补上并
 *     记进 auto_filled，收尾时如实说明。所以"跳过"不是把问题丢掉，是明确地把
 *     这一项的决定权交回给模型，而且留了痕。
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
  const [otherOn, setOtherOn] = useState<Record<number, boolean>>({});
  const [otherText, setOtherText] = useState<Record<number, string>>({});
  const [skipped, setSkipped] = useState<Record<number, boolean>>({});

  const [left, setLeft] = useState<number | null>(null);
  useEffect(() => {
    if (!request.expires_at) { setLeft(null); return; }
    const tick = () => setLeft(Math.max(0, Math.round(request.expires_at! - Date.now() / 1000)));
    tick();
    const t = window.setInterval(tick, 1000);
    return () => window.clearInterval(t);
  }, [request.expires_at]);

  const toggle = (qi: number, label: string, multi: boolean) => {
    setSkipped((prev) => ({ ...prev, [qi]: false }));   // 选了就不算跳过了
    setPicked((prev) => {
      const cur = prev[qi] || [];
      if (!multi) return { ...prev, [qi]: [label] };
      return { ...prev, [qi]: cur.includes(label) ? cur.filter((l) => l !== label) : [...cur, label] };
    });
  };

  const toggleOther = (qi: number, multi: boolean) => {
    setSkipped((prev) => ({ ...prev, [qi]: false }));
    setOtherOn((prev) => {
      const on = !prev[qi];
      // 单选题打开"其他"就把已选的清掉 —— 一个问题只能有一个答案，
      // 留着上一个选中项会让人以为两个都算数。
      if (on && !multi) setPicked((p) => ({ ...p, [qi]: [] }));
      return { ...prev, [qi]: on };
    });
  };

  const toggleSkip = (qi: number) => {
    setSkipped((prev) => {
      const on = !prev[qi];
      if (on) {                       // 跳过就把这一问已填的都收回去，免得提交时自相矛盾
        setPicked((p) => ({ ...p, [qi]: [] }));
        setOtherOn((p) => ({ ...p, [qi]: false }));
      }
      return { ...prev, [qi]: on };
    });
  };

  /** 只放**真答了**的问题。跳过的、没碰的一律不带 —— 后端按推荐补齐并记账。 */
  const answers = useMemo(() => {
    const out: Record<string, string> = {};
    questions.forEach((q, i) => {
      if (skipped[i]) return;
      const sel = [...(picked[i] || [])];
      const free = (otherText[i] || "").trim();
      if (otherOn[i] && free) sel.push(free);
      if (sel.length) out[q.question] = sel.join(", ");
    });
    return out;
  }, [questions, picked, otherOn, otherText, skipped]);

  const answeredCount = Object.keys(answers).length;
  const skippedCount = questions.filter((_, i) => skipped[i]).length;
  // 答了一问就能提交：剩下的按推荐补齐，界面下面那行已经把这件事说清楚了。
  const ready = answeredCount > 0 || skippedCount > 0;

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

      {questions.map((q, qi) => {
        const isSkipped = Boolean(skipped[qi]);
        return (
          <div className={"cs-question-block" + (isSkipped ? " is-skipped" : "")} key={q.question}>
            <div className="cs-question-text">
              {q.header && <span className="cs-question-tag">{q.header}</span>}
              {q.question}
              {q.multi_select && <span className="cs-question-multi">可多选</span>}
              <button type="button" className="cs-question-skip"
                      aria-pressed={isSkipped}
                      title="这题我不定，按你推荐的来"
                      onClick={() => toggleSkip(qi)}>
                {isSkipped ? "已跳过 · 撤销" : "跳过这题"}
              </button>
            </div>
            <div className="cs-question-options">
              {q.options.map((o) => {
                const on = (picked[qi] || []).includes(o.label);
                return (
                  <button
                    key={o.label}
                    type="button"
                    disabled={isSkipped}
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
              <button
                type="button"
                disabled={isSkipped}
                className={"cs-question-opt cs-question-other" + (otherOn[qi] ? " is-on" : "")}
                onClick={() => toggleOther(qi, Boolean(q.multi_select))}
              >
                <span className="cs-question-opt-label">✎ 其他…</span>
                <span className="cs-question-opt-desc">给的都不合适？自己写一个</span>
              </button>
            </div>
            {otherOn[qi] && !isSkipped && (
              <input
                className="cs-question-input"
                autoFocus
                value={otherText[qi] || ""}
                placeholder="输入你的答案"
                onChange={(e) => setOtherText((prev) => ({ ...prev, [qi]: e.target.value }))}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && ready) { e.preventDefault(); onAnswer(answers); }
                }}
              />
            )}
          </div>
        );
      })}

      <div className="cs-question-actions">
        <button type="button" className="cs-btn cs-btn-primary" disabled={!ready}
                onClick={() => onAnswer(answers)}>
          就按这个做
        </button>
        <button type="button" className="cs-btn cs-question-skipall"
                title="所有问题都交给它按推荐项决定"
                onClick={() => onAnswer({})}>
          全部跳过
        </button>
        <span className="cs-question-hint">
          {skippedCount > 0 || answeredCount < questions.length
            ? "没答的按推荐项继续，收尾时会告诉你哪几项是自动定的。"
            : "不选也没关系 —— 到点按推荐项继续，收尾时会告诉你哪几项是自动定的。"}
        </span>
      </div>
    </div>
  );
}
