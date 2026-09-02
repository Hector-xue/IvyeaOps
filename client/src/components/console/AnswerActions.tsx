/**
 * 回答末尾的动作行 —— 复制 / 重新生成。
 *
 * ── 为什么要有这一行 ──────────────────────────────────────────────────────
 * 改造前，一段回答的最后一行文字之后**什么都没有**，紧接着就是输入框。用户的原话
 * 是"正文末尾和输入框的衔接处"看着不对，参照的是 ChatGPT 和 DeepSeek Harness ——
 * 那两家在正文和输入框之间都有同一样东西：一排小小的动作图标。
 *
 * 它解决的不是装饰问题，是**收尾**问题：一段话读完了，眼睛需要一个"这里结束了"
 * 的落点，否则正文和输入框就是直接撞在一起。顺带还把"想复制这段结论"从"去右边
 * 产物栏找复制全文"变成手边就有。
 *
 * ── 「重新生成」为什么是追加一轮，不是替换 ───────────────────────────────
 * 任务台的一轮是**带执行过程和审批记录**的：工具调过、文件写过、审批批过。把它
 * 就地替换掉，等于把已经发生的事从界面上抹掉，而那些事在服务端是真的发生了。
 * 所以这里是"用同一个问题再跑一轮"，旧的那轮留在上面。按钮的 title 里写清楚了。
 */
import { useState } from "react";
import Icon from "../Icon";

export default function AnswerActions({
  text, onRegenerate, meta,
}: {
  /** 这一轮的正文（markdown 原文）。 */
  text: string;
  /** 没有上一条用户提问时（比如恢复出来的半截会话）就不给这个按钮。 */
  onRegenerate?: () => void;
  /**
   * 这一轮的收尾时刻与时长（「结束于 09:31 · 用时 9.0秒」）。
   *
   * 它原来自己占一行，紧贴在这排按钮下面。可这两样说的是同一件事的两半 ——
   * 「这一轮完了：拿走 / 再来一次 / 花了多久」—— 拆成两行只是在回答和输入框
   * 之间又垫高一截，还多出一条和按钮左缘对不齐的基线。
   */
  meta?: string;
}) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // 非 HTTPS / 无剪贴板权限时退回"选中这段"，让用户自己按 Ctrl+C ——
      // 比弹一个"复制失败"有用（ArtifactRail 的复制全文也是这么退的）。
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.cssText = "position:fixed;left:-9999px";
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); } catch { /* 只能到这儿了 */ }
      ta.remove();
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  return (
    <div className="cc-acts">
      <button type="button" className={"cc-act" + (copied ? " done" : "")}
              onClick={() => void copy()} title="复制这段回答的原文（Markdown）">
        <Icon name="copy" size={14} />
        <span>{copied ? "已复制" : "复制"}</span>
      </button>
      {onRegenerate && (
        <button type="button" className="cc-act" onClick={onRegenerate}
                title="用同一个问题再跑一轮。上面这一轮会留着 —— 它的执行过程和审批记录是真发生过的，不该被抹掉">
          <Icon name="regenerate" size={14} />
          <span>重新生成</span>
        </button>
      )}
      {!!meta && <span className="cc-acts-meta">{meta}</span>}
    </div>
  );
}
