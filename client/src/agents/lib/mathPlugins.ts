import { useEffect, useState } from 'react';
import type { Pluggable } from 'unified';

/**
 * 按需加载 KaTeX 的 markdown 插件。
 *
 * katex 一整套（引擎 + 字体表）约 258 kB，静态引入会把它钉在 Agents 的首屏块里。
 * 而绝大多数会话里一条数学公式都不会出现 —— 没有公式时这套插件本来也什么都不做，
 * 所以延后到**真的看见公式**再加载，行为完全一致。
 */

type Plugins = { remark: Pluggable[]; rehype: Pluggable[] };

const EMPTY: Plugins = { remark: [], rehype: [] };

// $...$、$$...$$、\(...\)、\[...\]。宁可多命中（多加载一次 258 kB）也不能漏，
// 漏了就是公式永远渲染不出来。
const MATH = /(\$\$[\s\S]+?\$\$)|(\$[^$\n]+\$)|(\\\()|(\\\[)/;

let cached: Plugins | null = null;
let loading: Promise<Plugins> | null = null;

function load(): Promise<Plugins> {
  if (cached) return Promise.resolve(cached);
  if (!loading) {
    loading = Promise.all([import('remark-math'), import('rehype-katex')])
      .then(([remarkMath, rehypeKatex]) => {
        cached = { remark: [remarkMath.default], rehype: [rehypeKatex.default] };
        return cached;
      })
      .catch(() => {
        // 加载失败就退回不带公式渲染 —— 整段 markdown 照常显示，
        // 只是公式停留在源码形式。比整条消息白掉好得多。
        loading = null;
        return EMPTY;
      });
  }
  return loading;
}

/** 内容里有公式才返回 KaTeX 插件；没有、或还在加载中，返回空数组。 */
export function useMathPlugins(source: string): Plugins {
  const needed = MATH.test(source);
  const [plugins, setPlugins] = useState<Plugins>(cached && needed ? cached : EMPTY);

  useEffect(() => {
    if (!needed) return;
    if (cached) { setPlugins(cached); return; }
    let alive = true;
    void load().then((p) => { if (alive) setPlugins(p); });
    return () => { alive = false; };
  }, [needed]);

  return needed ? plugins : EMPTY;
}
