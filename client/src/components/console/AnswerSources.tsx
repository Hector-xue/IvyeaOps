/**
 * 回答底下那条「原文」链条：这一轮真的抓过的网页，一个一个能点开。
 *
 * 数据从哪来、为什么不做模糊匹配，见 lib/answerSources。这里只管怎么摆。
 */
import { useState } from "react";
import { answerSources } from "../../lib/answerSources";
import type { ConsoleStep } from "../../lib/stepLabels";

/** 一上来先露这么多条，其余折在"还有 N 个"后面 —— 一轮抓二十个网页是常事。 */
const HEAD = 8;

export default function AnswerSources({ steps }: { steps?: ConsoleStep[] }) {
  const [all, setAll] = useState(false);
  const rows = answerSources(steps);
  if (!rows.length) return null;
  const shown = all ? rows : rows.slice(0, HEAD);
  return (
    <div className="cc-sources">
      <div className="cc-sources-head">本轮抓取的网页 · {rows.length}</div>
      <ul className="cc-sources-list">
        {shown.map((s) => (
          <li key={s.url}>
            {/* noopener：新标签页拿不到 window.opener，外链的老规矩 */}
            <a href={s.url} target="_blank" rel="noopener noreferrer" title={s.url}>
              <span className="cc-sources-host">{s.host}</span>
              {s.path ? <span className="cc-sources-path">{s.path}</span> : null}
            </a>
          </li>
        ))}
      </ul>
      {rows.length > HEAD && (
        <button type="button" className="cc-sources-more" onClick={() => setAll((v) => !v)}>
          {all ? "收起" : `还有 ${rows.length - HEAD} 个`}
        </button>
      )}
    </div>
  );
}
