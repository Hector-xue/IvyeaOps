/**
 * 右侧产物栏 —— 对标 MyLevis 右边那条竖排图标（待办 / 文档 / 文件 / diff / 浏览器）。
 *
 * 默认是一条 40px 的图标条，点开才占宽度，所以不抢会话区。
 * 只放**真的有数据**的格：报告、待办、审批记录、会话信息。
 *
 * 「文件 / diff / 浏览器」暂缺是有意的：工作区绑目录是管理员功能且当前无人绑定，
 * 没有可复用的文件列表端点 —— 现在加等于摆个永远是空的壳子。等真有数据源再补。
 */
import { useMemo, useState, type ReactNode } from "react";
import { MarkdownReport } from "../../lib/reportFormat";

export type RailTodo = { content?: string; status?: string; [k: string]: any };
export type RailApproval = { title: string; decision: string; at: number };

type TabKey = "report" | "todo" | "approval" | "session";

const TABS: { key: TabKey; icon: string; label: string }[] = [
  { key: "report", icon: "▤", label: "报告" },
  { key: "todo", icon: "☑", label: "待办" },
  { key: "approval", icon: "⚑", label: "审批" },
  { key: "session", icon: "◷", label: "会话" },
];

const STATUS_LABEL: Record<string, string> = {
  completed: "已完成",
  in_progress: "进行中",
  pending: "待开始",
};

function Empty({ children }: { children: ReactNode }) {
  return <div className="cr-empty">{children}</div>;
}

export default function ArtifactRail({
  answers,
  todos,
  approvals,
  sessionId,
  model,
  readOnly,
  usage,
}: {
  /** 本会话里 Agent 给出的正文，按先后顺序。 */
  answers: string[];
  todos: RailTodo[];
  approvals: RailApproval[];
  sessionId: string;
  model?: string;
  readOnly?: boolean;
  usage?: { prompt_tokens?: number; completion_tokens?: number; [k: string]: any } | null;
}) {
  const [open, setOpen] = useState<TabKey | null>(null);
  const [copied, setCopied] = useState(false);

  // 本会话的正文拼成一份报告：多轮之间用分隔线断开，便于整段带走。
  const report = useMemo(
    () => answers.filter((a) => a.trim()).join("\n\n---\n\n"),
    [answers],
  );

  const counts: Record<TabKey, number> = {
    report: answers.filter((a) => a.trim()).length,
    todo: todos.length,
    approval: approvals.length,
    session: 0,
  };

  const copyReport = async () => {
    try {
      await navigator.clipboard.writeText(report);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      // 非 HTTPS / 无剪贴板权限时退回选中，让用户自己复制
      const el = document.querySelector<HTMLElement>(".cc-rail-report");
      if (el) {
        const r = document.createRange();
        r.selectNodeContents(el);
        const sel = window.getSelection();
        sel?.removeAllRanges();
        sel?.addRange(r);
      }
    }
  };

  const downloadReport = () => {
    const blob = new Blob([report], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `ivyea-${sessionId || "console"}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <aside className={"cc-rail" + (open ? " open" : "")}>
      <div className="cc-rail-tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            className={"cc-rail-tab" + (open === t.key ? " active" : "")}
            title={t.label}
            onClick={() => setOpen((v) => (v === t.key ? null : t.key))}
          >
            <span>{t.icon}</span>
            {counts[t.key] > 0 && <em className="cc-rail-badge">{counts[t.key]}</em>}
          </button>
        ))}
      </div>

      {open && (
        <div className="cc-rail-panel scroll-thin">
          <div className="cc-rail-title">
            {TABS.find((t) => t.key === open)?.label}
            <button type="button" className="cc-rail-close" onClick={() => setOpen(null)} title="收起">✕</button>
          </div>

          {open === "report" && (
            !report
              ? <Empty>这一会话还没有产出正文。Agent 回答之后，整段结论会汇总到这里，可以直接复制或下载。</Empty>
              : (
                <>
                  <div className="cc-rail-actions">
                    <button className="cs-btn" onClick={() => void copyReport()}>
                      {copied ? "已复制" : "复制全文"}
                    </button>
                    <button className="cs-btn" onClick={downloadReport}>下载 .md</button>
                  </div>
                  <div className="cc-rail-report">
                    <MarkdownReport text={report} />
                  </div>
                </>
              )
          )}

          {open === "todo" && (
            todos.length === 0
              ? <Empty>本轮还没有拆出待办。复杂任务 Agent 会自己列计划，这里会同步显示。</Empty>
              : (
                <ul className="cc-rail-todos">
                  {todos.map((t, i) => (
                    <li key={i} className={"st-" + (t.status || "pending")}>
                      <span className="cc-rail-dot" />
                      <span>{t.content || String(t)}</span>
                      <em>{STATUS_LABEL[t.status || ""] || ""}</em>
                    </li>
                  ))}
                </ul>
              )
          )}

          {open === "approval" && (
            approvals.length === 0
              ? <Empty>本轮没有需要确认的写操作。切到「逐项审批」后，Agent 想改动线上数据时会在这里留痕。</Empty>
              : (
                <ul className="cc-rail-approvals">
                  {approvals.map((a, i) => (
                    <li key={i}>
                      <span className={a.decision === "deny" || a.decision === "abort" ? "cs-err" : "cs-ok"}>
                        {a.decision === "deny" || a.decision === "abort" ? "✕" : "✓"}
                      </span>
                      <span>{a.title}</span>
                      <em>{new Date(a.at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</em>
                    </li>
                  ))}
                </ul>
              )
          )}

          {open === "session" && (
            <dl className="cc-rail-meta">
              <dt>会话</dt><dd>{sessionId || "未开始"}</dd>
              <dt>模型</dt><dd>{model || "—"}</dd>
              <dt>模式</dt><dd>{readOnly === false ? "逐项审批（可写）" : "只读建议"}</dd>
              {usage && (
                <>
                  <dt>用量</dt>
                  <dd>{(usage.prompt_tokens ?? 0)} in / {(usage.completion_tokens ?? 0)} out</dd>
                </>
              )}
            </dl>
          )}
        </div>
      )}
    </aside>
  );
}
