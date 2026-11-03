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
import FilePreview, { fmtSize, fmtTime } from "./FilePreview";
import { consoleFileRawUrl, type ConsoleFile, type IvyeaFileChange } from "../../api/ivyeaAgent";
import { saveText } from "../../lib/saveBlob";

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
  files: serverFiles = [],
  approvals,
  sessionId,
  model,
  readOnly,
  usage,
}: {
  /** 本会话里 Agent 给出的正文，按先后顺序。 */
  answers: string[];
  todos: RailTodo[];
  /** Agent 本会话改过的文件（含 diff）。实时事件，页面一刷新就没了。 */
  fileChanges?: IvyeaFileChange[];
  /**
   * 服务端记下的产物索引（含大小 / 修改时间 / 还在不在）。
   * 它是"刷新之后还找得到"的那一半 —— 实时事件只在跑那一轮的标签页里有。
   */
  files?: ConsoleFile[];
  approvals: RailApproval[];
  sessionId: string;
  model?: string;
  readOnly?: boolean;
  usage?: { prompt_tokens?: number; completion_tokens?: number; [k: string]: any } | null;
}) {
  const [open, setOpen] = useState<TabKey | null>(null);
  const [copied, setCopied] = useState(false);
  const [preview, setPreview] = useState<ConsoleFile | null>(null);

  // 本会话的正文拼成一份报告：多轮之间用分隔线断开，便于整段带走。
  const report = useMemo(
    () => answers.filter((a) => a.trim()).join("\n\n---\n\n"),
    [answers],
  );

  /*
   * 「文件」按路径去重（同一个文件改三次算一个文件）；「改动」数的是改动次数。
   *
   * 两个数据源要**并起来**，缺一不可：
   * - 实时的 `file_change`：这一轮刚写的，服务端可能还没落库；
   * - 服务端索引 `serverFiles`：历史会话、刷新之后**唯一**还在的那份，
   *   而且只有它带得出大小 / 修改时间 / 文件还在不在，以及下载要用的 id。
   * 只用前者，刷新就空；只用后者，刚写完那一下要等一次请求才出现。
   */
  const files = useMemo(() => {
    const by = new Map<string, { path: string; list: IvyeaFileChange[]; meta?: ConsoleFile }>();
    for (const c of fileChanges) {
      const cur = by.get(c.path) || { path: c.path, list: [] };
      cur.list.push(c);
      by.set(c.path, cur);
    }
    for (const f of serverFiles) {
      const cur = by.get(f.path) || { path: f.path, list: [] };
      cur.meta = f;
      by.set(f.path, cur);
    }
    return [...by.values()].sort((a, b) => (b.meta?.last_seen || 0) - (a.meta?.last_seen || 0));
  }, [fileChanges, serverFiles]);

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
    saveText(report, `ivyea-${sessionId || "console"}.md`, "text/markdown;charset=utf-8");
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
              ? <Empty>这一会话 Agent 还没有写出文件。写入或编辑之后，文件会列在这里，可以直接预览或下载。</Empty>
              : (
                <ul className="cr-files">
                  {files.map((f) => {
                    const name = f.meta?.name || f.path.split(/[\\/]/).pop() || f.path;
                    const last = f.list[f.list.length - 1];
                    const action = last?.action || f.meta?.action || "";
                    const times = Math.max(f.list.length, f.meta?.changes || 0);
                    const meta = f.meta;
                    // 没有 meta = 服务端索引还没同步过来（刚写完的那一瞬）。
                    // 这时按钮先禁用并说明，比画一个点了报错的按钮好。
                    const gone = !!meta && !meta.exists;
                    return (
                      <li key={f.path} title={f.path}>
                        <span className={"cr-file-act act-" + action}>
                          {ACTION_LABEL[action] || action || "写入"}
                        </span>
                        <span className="cr-file-name">
                          {/* 名字自己截断，右边那几个小标签**不参与挤压** ——
                              一个长文件名不该把"已不在""改 3 次"顶出可视区，
                              那几个字恰恰是决定"还点不点得动"的信息 */}
                          <b className="cr-file-label">{name}</b>
                          {/* 这一行只留"会改变你要不要点它"的标签。
                              大小和时间挪到下面那行 —— 实测它们会把文件名挤成
                              「广…」「草…」，而名字恰恰是扫读时唯一要看的东西。 */}
                          {times > 1 && <em>改 {times} 次</em>}
                          {gone && <em className="cr-file-gone">已不在</em>}
                        </span>
                        <span className="cr-file-acts">
                          <button
                            type="button"
                            className="cr-file-btn"
                            disabled={!meta?.previewable}
                            title={
                              !meta ? "刚写完，稍等一下再试"
                                : gone ? "文件已不在磁盘上"
                                : meta.previewable ? "预览" : "这个类型没法在网页里看，下载吧"
                            }
                            onClick={() => meta && setPreview(meta)}
                          >
                            <Icon name="preview" size={13} />
                          </button>
                          <a
                            className={"cr-file-btn" + (meta?.exists ? "" : " disabled")}
                            href={meta?.exists ? consoleFileRawUrl(meta.id, true) : undefined}
                            download={meta?.name}
                            title={
                              !meta ? "刚写完，稍等一下再试"
                                : gone ? "文件已不在磁盘上" : "下载"
                            }
                            onClick={(e) => { if (!meta?.exists) e.preventDefault(); }}
                          >
                            <Icon name="download" size={13} />
                          </a>
                        </span>
                        {/* 路径与时间同一行：文件名那行要留给"是哪个文件 + 能拿它做什么"。
                            \u202A…\u202C 是 LTR 嵌入 —— 容器是 rtl（为了截断截**头**、
                            保住文件名那一端），不裹的话开头那个 "/" 会被 bidi
                            甩到行尾，显示成 `root/工作区/a.md/`。 */}
                        <span className="cr-file-path">
                          {"\u202A" + f.path + "\u202C"}
                          {/* 时间同样要裹 LTR：容器是 rtl，不裹的话
                              "2026-09-10 00:10" 会被 bidi 反成 "00:10 2026-09-10" */}
                          {meta?.exists && (
                            <b className="cr-file-time">
                              {"\u202A" + [fmtSize(meta.size), meta.mtime > 0 ? fmtTime(meta.mtime) : ""]
                                .filter(Boolean).join(" · ") + "\u202C"}
                            </b>
                          )}
                        </span>
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
      {preview && <FilePreview file={preview} onClose={() => setPreview(null)} />}
    </aside>
  );
}
