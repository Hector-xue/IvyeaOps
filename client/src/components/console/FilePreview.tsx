/**
 * 产物预览 —— 「先看一眼，再决定下不下」。
 *
 * 任务跑完，右边列着五个文件名：`报表.xlsx`、`结论.md`、`图.png`……
 * 要知道哪个是自己要的，此前只有一条路：全下下来，一个个打开。
 *
 * # 每种类型给什么
 *
 * | kind     | 怎么显示                                   |
 * |----------|--------------------------------------------|
 * | markdown | 渲染成正文（和答案区同一套 MarkdownReport） |
 * | csv      | 画成表格 —— 逗号分隔的原文没法看           |
 * | text     | 等宽原文（json / 代码 / 日志都走这条）      |
 * | image    | 直接显示                                   |
 * | pdf      | 浏览器自带的阅读器                          |
 * | html     | **沙箱 iframe**，见下                       |
 * | binary   | 不预览，只给下载 —— 并说清为什么            |
 *
 * # HTML 为什么要单独说
 *
 * 产物里的 .html 是 **agent 生成的、来路不完全可控的东西**。在自己的域上直接打开它，
 * 等于执行别人写的脚本。所以：
 *
 * 1. 服务端**从不 inline 返回 text/html**（只给文本），这里拿到的是字符串；
 * 2. 塞进 `sandbox="allow-same-origin"` 的 iframe —— **不给 allow-scripts**，
 *    脚本一行都跑不了；
 *    ⚠️ 不能写成 `sandbox=""`：Chromium 不给不透明来源的 srcdoc 建子帧，
 *    结果是**整片空白**（元素在、尺寸对、内容不存在），排查起来极其费劲；
 * 3. 再往 srcdoc 里注入一条 `<meta CSP default-src 'none'>` —— 预览**不联网**：
 *    一份报表不该因为被看了一眼就把"谁在什么时候看了"发给第三方。
 */
import { useEffect, useMemo, useState } from "react";
import Icon from "../Icon";
import { MarkdownReport } from "../../lib/reportFormat";
import { errText } from "../../lib/errText";
import { consoleFileRawUrl, consoleFileText, type ConsoleFile } from "../../api/ivyeaAgent";

/** 预览里的 HTML 一律断网：只允许内联样式与 data: 图片。 */
const PREVIEW_CSP =
  '<meta http-equiv="Content-Security-Policy" ' +
  "content=\"default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; font-src data:\">";

function withCsp(html: string): string {
  const i = html.search(/<head[^>]*>/i);
  if (i >= 0) {
    const end = html.indexOf(">", i) + 1;
    return html.slice(0, end) + PREVIEW_CSP + html.slice(end);
  }
  return PREVIEW_CSP + html;
}

export function fmtSize(n: number): string {
  if (!n) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function fmtTime(sec: number): string {
  if (!sec) return "";
  const d = new Date(sec * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/**
 * csv 切行切列。**不做完整的 RFC 4180 解析**：这是预览，不是导入。
 * 引号里带逗号的字段会被切错，但用户看的是"这份表大概长什么样"——
 * 真要精确读，下载下来用表格软件打开才是对的路。
 */
function parseCsv(text: string, limit = 200): string[][] {
  const sep = text.includes("\t") && !text.includes(",") ? "\t" : ",";
  return text
    .split(/\r?\n/)
    .filter((l) => l.length > 0)
    .slice(0, limit)
    .map((line) => line.split(sep));
}

export default function FilePreview({ file, onClose }: { file: ConsoleFile; onClose: () => void }) {
  const [text, setText] = useState("");
  const [truncated, setTruncated] = useState(false);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  const needsText = ["markdown", "csv", "text", "html"].includes(file.kind);

  useEffect(() => {
    if (!needsText || !file.exists) return;
    let alive = true;
    setLoading(true);
    setErr("");
    consoleFileText(file.id)
      .then((d) => {
        if (!alive) return;
        setText(d.text || "");
        setTruncated(!!d.truncated);
      })
      .catch((e) => {
        if (!alive) return;
        // 说清是哪一步失败的：文件被删了和没权限是两回事，
        // 都写成"加载失败"用户只能干瞪眼。
        // 走 errText 而不是直接读 detail —— 422 的 detail 是对象数组，
        // 直接渲染会把整页崩成「渲染失败」（见 scripts/check-errtext.mjs）。
        setErr(errText(e, "读不到这个文件"));
      })
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [file.id, file.exists, needsText]);

  // Esc 关闭：弹层不给键盘出口，等于把人困在里面
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const rows = useMemo(() => (file.kind === "csv" ? parseCsv(text) : []), [file.kind, text]);

  const body = () => {
    if (!file.exists) {
      return <div className="cfp-note">这个文件已经不在磁盘上了 —— 它被删除、移动或改名了。</div>;
    }
    if (file.kind === "image") {
      return <img className="cfp-img" src={consoleFileRawUrl(file.id, false)} alt={file.name} />;
    }
    if (file.kind === "pdf") {
      return <iframe className="cfp-frame" title={file.name} src={consoleFileRawUrl(file.id, false)} />;
    }
    if (file.kind === "binary") {
      return (
        <div className="cfp-note">
          这是二进制文件（{file.name.split(".").pop()?.toUpperCase() || "未知类型"}），
          没有能在网页里看的形态 —— 下载下来用对应的软件打开。
        </div>
      );
    }
    if (loading) return <div className="cfp-note">读取中…</div>;
    if (err) return <div className="cfp-note cfp-err">{err}</div>;
    if (file.kind === "markdown") return <div className="cfp-md"><MarkdownReport text={text} /></div>;
    if (file.kind === "html") {
      return (
        <iframe
          className="cfp-frame"
          title={file.name}
          /* 只给 same-origin、不给 scripts：脚本跑不了，同源也就无从被利用。
             空 sandbox 会让 srcdoc 根本不加载（见文件头那段）。 */
          sandbox="allow-same-origin"
          srcDoc={withCsp(text)}
        />
      );
    }
    if (file.kind === "csv") {
      return (
        <div className="cfp-table-wrap">
          <table className="cfp-table">
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  {r.map((c, j) => (i === 0 ? <th key={j}>{c}</th> : <td key={j}>{c}</td>))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }
    return <pre className="cfp-pre scroll-thin">{text}</pre>;
  };

  // 右侧抽屉而不是居中弹窗：看的是一份文档，左边的对话得留着 —— 一边对照原文
  // 一边看结论是这个面板最常见的用法，居中弹窗会把对话整个盖掉。
  return (
    <div className="cfp-bd" onClick={onClose}>
      <div className="cfp-drawer" onClick={(e) => e.stopPropagation()}>
        <div className="m-head">
          <Icon name="file" size={15} />
          <span className="m-title">{file.name}</span>
          <a
            className="cs-btn"
            href={consoleFileRawUrl(file.id, true)}
            download={file.name}
            title={file.exists ? "下载这个文件" : "文件已不在"}
            aria-disabled={!file.exists}
          >
            下载
          </a>
          <button type="button" className="cc-rail-close" onClick={onClose} title="关闭">✕</button>
        </div>
        <div className="cfp-meta" title={file.path}>
          <code>{file.path}</code>
          {file.exists && <span>{fmtSize(file.size)}</span>}
          {file.exists && <span>{fmtTime(file.mtime)}</span>}
          {truncated && <em>内容过大，只显示前 512KB</em>}
        </div>
        <div className="m-body cfp-body">{body()}</div>
      </div>
    </div>
  );
}
