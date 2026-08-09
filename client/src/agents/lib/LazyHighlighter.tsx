import { useEffect, useState, type CSSProperties, type ComponentType } from 'react';

/**
 * 代码高亮的按需版本。
 *
 * Prism 引擎 + 22 种语法（见 lib/prismLight）约 190 kB，是 Agents 首屏块里最大的
 * 单块第三方代码。它只在消息里出现代码块时才有用，所以延后加载：**加载完成前
 * 先用同样的排版把代码原样显示出来**，加载完再换成高亮版。
 *
 * 关键是"先显示"而不是留白 —— 代码内容本身立刻可读、可复制，高亮只是锦上添花。
 * 换成 Suspense 空白占位反而更糟：用户会看到一块正在跳动的空洞。
 */

type Highlighter = ComponentType<{
  language?: string;
  style?: unknown;
  customStyle?: CSSProperties;
  codeTagProps?: { style?: CSSProperties };
  children?: string;
}>;

type Loaded = { Comp: Highlighter; theme: unknown };

let cached: Loaded | null = null;
let loading: Promise<Loaded | null> | null = null;

function load(): Promise<Loaded | null> {
  if (cached) return Promise.resolve(cached);
  if (!loading) {
    loading = Promise.all([
      import('./prismLight'),
      import('react-syntax-highlighter/dist/esm/styles/prism'),
    ])
      .then(([mod, styles]) => {
        cached = { Comp: mod.default as unknown as Highlighter, theme: styles.oneDark };
        return cached;
      })
      .catch(() => {
        // 加载失败就一直用无高亮的纯文本版本。代码照样看得见、复制得走，
        // 比整块内容消失好得多。
        loading = null;
        return null;
      });
  }
  return loading;
}

export default function LazyHighlighter({
  language,
  code,
  customStyle,
  codeTagProps,
}: {
  language: string;
  code: string;
  customStyle?: CSSProperties;
  codeTagProps?: { style?: CSSProperties };
}) {
  const [loaded, setLoaded] = useState<Loaded | null>(cached);

  useEffect(() => {
    if (cached) { setLoaded(cached); return; }
    let alive = true;
    void load().then((l) => { if (alive && l) setLoaded(l); });
    return () => { alive = false; };
  }, []);

  if (!loaded) {
    // 未高亮的兜底。样式刻意与 oneDark 主题的底色、圆角、字号对齐，
    // 这样切换过去时不会整块跳一下。
    return (
      <pre
        style={{
          margin: 0,
          borderRadius: '0.5rem',
          fontSize: '0.875rem',
          background: '#282c34',
          color: '#abb2bf',
          overflowX: 'auto',
          ...customStyle,
        }}
      >
        <code style={codeTagProps?.style}>{code}</code>
      </pre>
    );
  }

  const { Comp, theme } = loaded;
  return (
    <Comp language={language} style={theme} customStyle={customStyle} codeTagProps={codeTagProps}>
      {code}
    </Comp>
  );
}
