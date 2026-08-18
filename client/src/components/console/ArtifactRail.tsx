/**
 * 右侧产物栏 —— 对标 MyLevis 右边那条竖排图标（待办 / 文档 / 文件 / diff / 浏览器）。
 *
 * 默认是一条 40px 的图标条，点开才占宽度，所以不抢会话区。
 * 只放**真的有数据**的格：报告、待办、审批记录、会话信息。
 *
 * 「浏览器」仍然缺：agent 只有 web_fetch / web_search，没有浏览器自动化 ——
 * 那一格要先有那个能力，不是补个端点的事。
 *
 * 「文件 / diff」的数据源是 Agent 发来的 file_change 事件（write_file / edit_file
 * 落盘成功后才发），所以这里列的都是**真的改到磁盘上**的东西。
 */
import { useMemo, useState, type ReactNode } from "react";
import Icon from "../Icon";
import { MarkdownReport } from "../../lib/reportFormat";
import type { IvyeaFileChange } from "../../api/ivyeaAgent";

export type RailTodo = { content?: string; status?: string; [k: string]: any };
export type RailApproval = { title: string; decision: string; at: number };

type TabKey = "report" | "file" | "diff" | "todo" | "approval" | "session";

const TABS: { key: TabKey; icon: string; label: string }[] = [
  { key: "report", icon: "report", label: "报告" },
  { key: "file", icon: "file", label: "文件" },
  { key: "diff", icon: "diff", label: "改动" },
  { key: "todo", icon: "todo", label: "待办" },
  { key: "approval", icon: "flag", label: "审批" },
  { key: "session", icon: "history", label: "会话" },
];

const ACTION_LABEL: Record<string, string> = {
  create: "新建", overwrite: "覆盖", edit: "编辑",
};

/** diff 的一行归到哪一类。render_diff 的格式是「行号 +/- 内容」。 */
function diffLineKind(line: string): "add" | "del" | "ctx" {
  const m = line.match(/^\s*\d*\s*([+-])\s/);
    if (m) return m[1] === "+" ? "add" : "del";
  return "ctx";
}

/**
 * 审批决定的四种归宿。**超时和未决必须和"拒绝"分开显示** ——
 * 都画成红叉的话，"没人理它所以没执行"会被读成"有人看过并否决了"，
 * 这是事后复盘时最要命的一种误读。
 */
const DECISIONS: Record<string, { icon: string; label: string; cls: string }> = {
  approve: { icon: "✓", label: "已批准", cls: "cs-ok" },
  session: { icon: "✓", label: "本会话内都批准", cls: "cs-ok" },
  deny: { icon: "✕", label: "已拒绝", cls: "cs-err" },
  abort: { icon: "✕", label: "已中止", cls: "cs-err" },
  timeout: { icon: "⏱", label: "超时未处理，已自动拒绝", cls: "cs-warn" },
  pending: { icon: "◌", label: "未处理", cls: "cs-dim" },
};

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
  fileChanges = [],
  approvals,
  sessionId,
  model,
  readOnly,
  usage,
}: {
  /** 本会话里 Agent 给出的正文，按先后顺序。 */
  answers: string[];
  todos: RailTodo[];
  /** Agent 本会话改过的文件（含 diff）。 */
  fileChanges?: IvyeaFileChange[];
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

  // 「文件」按路径去重（同一个文件改三次算一个文件）；「改动」数的是改动次数。
  const files = useMemo(() => {
    const by = new Map<string, IvyeaFileChange[]>();
    for (const c of fileChanges) {
      const list = by.get(c.path) || [];
      list.push(c);
      by.set(c.path, list);
    }
    return [...by.entries()].map(([path, list]) => ({ path, list }));
  }, [fileChanges]);

  const counts: Record<TabKey, number> = {
    report: answers.filter((a) => a.trim()).length,
    file: files.length,
    diff: fileChanges.length,
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

  // 一条产物都没有时整条栏不渲染。
  //
  // 它原本无条件常驻：首页（还没开始对话）右边就挂着一条 40px 的空图标竖条，
  // 六个格子点进去全是"还没有产出"。参考图里那块位置是纯留白 —— 一个永远
  // 有六个入口、但九成时间六个都是空的侧栏，占的是注意力不是空间。
  const hasAnything =
    !!report || fileChanges.length > 0 || todos.length > 0 || approvals.length > 0 || !!sessionId;
  if (!hasAnything) return null;

  return (
    <aside className={"cc-rail" + (open ? " open" : "")}>
      <div className="cc-rail-tabs">
        {/* **只显示真有东西的那几个。**
            原来六个图标常驻，空会话里就是右边一竖排点不出内容的按钮 —— 看着像
            界面没做完。会话（session）没有计数但永远有内容，所以恒显示。 */}
        {TABS.filter((t) => t.key === "session" || counts[t.key] > 0).map((t) => (
          <button
            key={t.key}
            type="button"
            className={"cc-rail-tab" + (open === t.key ? " active" : "")}
            title={t.label}
            onClick={() => setOpen((v) => (v === t.key ? null : t.key))}
          >
            <Icon name={t.icon} size={16} />
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

          {open === "file" && (
            files.length === 0
              ? <Empty>这一会话 Agent 还没有改动过文件。写入或编辑之后，动过的文件会列在这里。</Empty>
              : (
                <ul className="cr-files">
                  {files.map((f) => {
                    const name = f.path.split(/[\\/]/).pop() || f.path;
                    const last = f.list[f.list.length - 1];
                    return (
                      <li key={f.path} title={f.path}>
                        <span className={"cr-file-act act-" + last.action}>
                          {ACTION_LABEL[last.action] || last.action}
                        </span>
                        <span className="cr-file-name">{name}</span>
                        {f.list.length > 1 && <em>改 {f.list.length} 次</em>}
                        <span className="cr-file-path">{f.path}</span>
                      </li>
                    );
                  })}
                </ul>
              )
          )}

          {open === "diff" && (
            fileChanges.length === 0
              ? <Empty>还没有改动。Agent 写入或编辑文件后，逐条的前后对比会显示在这里。</Empty>
              : (
                <div className="cr-diffs">
                  {fileChanges.map((c, i) => (
                    <div className="cr-diff" key={i}>
                      <div className="cr-diff-head" title={c.path}>
                        <span className={"cr-file-act act-" + c.action}>
                          {ACTION_LABEL[c.action] || c.action}
                        </span>
                        <span className="cr-file-name">{c.path.split(/[\\/]/).pop()}</span>
                        {/* 片段级 diff 的行号是**片段内**的相对行号，不标出来会被
                            当成文件行号去对，对不上就会以为显示错了 */}
                        {c.scope === "fragment" && <em>仅被替换的片段</em>}
                      </div>
                      <pre className="cr-diff-body scroll-thin">
                        {c.diff.split("\n").map((line, j) => (
                          <div key={j} className={"dl dl-" + diffLineKind(line)}>{line || " "}</div>
                        ))}
                      </pre>
                      {c.truncated && <div className="cr-diff-cut">改动过大，diff 已截断</div>}
                    </div>
                  ))}
                </div>
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
              ? <Empty>本轮没有需要确认的写操作。切到「审批放行」后，Agent 想改动线上数据时会在这里留痕。</Empty>
              : (
                <ul className="cc-rail-approvals">
                  {approvals.map((a, i) => {
                    const d = DECISIONS[a.decision] || DECISIONS.pending;
                    return (
                      <li key={i}>
                        <span className={d.cls} title={d.label}>{d.icon}</span>
                        <span>{a.title}</span>
                        <em>{a.at
                          ? new Date(a.at).toLocaleString("zh-CN",
                              { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })
                          : d.label}</em>
                      </li>
                    );
                  })}
                </ul>
              )
          )}

          {open === "session" && (
            <dl className="cc-rail-meta">
              <dt>会话</dt><dd>{sessionId || "未开始"}</dd>
              <dt>模型</dt><dd>{model || "—"}</dd>
              {/* 只读与否由 start 事件回报（read_only），它是**服务端的事实**，
                  比输入框上那枚芯片更可信 —— 芯片是"我想怎么跑"，这里是"实际怎么跑的"。 */}
              <dt>模式</dt><dd>{readOnly === false ? "可写（审批放行 / 完全放行）" : "只读"}</dd>
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
