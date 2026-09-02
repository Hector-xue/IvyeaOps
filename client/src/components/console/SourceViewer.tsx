/**
 * 「引用来源 → 查看原文」的站内查看器。
 *
 * 回答结尾的引用里，官方卡带真实 https 地址（点了跳外网），而治理卡和用户上传的
 * 文档**在互联网上没有原文** —— 它们的地址是 `ivyea://` / `ivyea-upload://`。
 * 渲染器出于安全不放行这类协议（伪协议防线不能开口子），此前它们就成了点不动的
 * 死文本。原文其实一直在系统里，这个组件负责把它取出来摆到眼前。
 *
 * 刻意做成**只读浮层**而不是跳转到知识库页面：用户此刻在读一段结论，想核对的是
 * "这句话的依据长什么样"，把他从对话里带走、再让他自己找回来，代价比看一眼大得多。
 */
import { useCallback, useEffect, useState } from "react";
import Icon from "../Icon";
import { ivyeaKnowledgeCard, ivyeaKnowledgeFile } from "../../api/ivyeaAgent";
import {
  OPEN_SOURCE_EVENT,
  type CitationSource,
} from "../../lib/citationSource";
import { MarkdownReport } from "../../lib/reportFormat";

type State =
  | { phase: "idle" }
  | { phase: "loading"; src: CitationSource }
  | { phase: "ok"; src: CitationSource; title: string; body: string }
  | { phase: "error"; src: CitationSource; message: string };

export default function SourceViewer() {
  const [state, setState] = useState<State>({ phase: "idle" });

  const load = useCallback(async (src: CitationSource) => {
    setState({ phase: "loading", src });
    try {
      if (src.kind === "upload") {
        const r = await ivyeaKnowledgeFile(src.path);
        if (!r.content) throw new Error("这份文档是空的，或者已经从知识库里删掉了");
        setState({
          phase: "ok", src,
          title: r.name || src.path.split("/").pop() || src.path,
          body: r.content,
        });
        return;
      }
      const r = await ivyeaKnowledgeCard(src.id);
      if (!r.content) throw new Error("这张知识卡没有正文");
      setState({ phase: "ok", src, title: r.title, body: r.content });
    } catch (e) {
      setState({
        phase: "error",
        src,
        // 说清楚"取不到"而不是静默失败：这条来源本来就是用来核对结论的，
        // 取不到时用户至少要知道该去哪儿找（原始标识在标题里给出）。
        message: e instanceof Error ? e.message : String(e),
      });
    }
  }, []);

  useEffect(() => {
    const onOpen = (e: Event) => {
      const src = (e as CustomEvent<CitationSource>).detail;
      if (src) void load(src);
    };
    window.addEventListener(OPEN_SOURCE_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_SOURCE_EVENT, onOpen);
  }, [load]);

  // Esc 关闭：浮层不给出路会一直挡着底下的对话
  useEffect(() => {
    if (state.phase === "idle") return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setState({ phase: "idle" });
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [state.phase]);

  if (state.phase === "idle") return null;
  const close = () => setState({ phase: "idle" });

  return (
    <div className="src-mask" onMouseDown={(e) => e.target === e.currentTarget && close()}>
      <div className="src-card" role="dialog" aria-modal="true" aria-label="引用来源原文">
        <div className="src-head">
          <span className="src-title">
            {state.phase === "ok" ? state.title : "引用来源"}
          </span>
          <button type="button" className="src-close" onClick={close} title="关闭（Esc）">
            <Icon name="close" size={14} />
          </button>
        </div>
        <div className="src-body scroll-thin">
          {state.phase === "loading" && <p className="src-hint">正在取原文…</p>}
          {state.phase === "error" && (
            <>
              <p className="src-hint">取不到这份原文：{state.message}</p>
              <p className="src-hint src-raw">{state.src.raw}</p>
            </>
          )}
          {state.phase === "ok" && <MarkdownReport text={state.body} />}
        </div>
      </div>
    </div>
  );
}
