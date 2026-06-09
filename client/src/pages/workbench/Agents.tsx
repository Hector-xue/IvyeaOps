// claudecodeui 原生移植的 ops 包裹页。
//
// 关键:agents 用**独立的 React root**(createRoot)挂到 #agents-root DOM 节点,
// 从而脱离 ops 的 React 树 —— 不继承 ops 的 BrowserRouter / 各 context,
// agents 自己的 MemoryRouter 成为顶层 Router,避免 "nested <Router>" invariant。
// 仍是同一页面、同一 JS bundle、同一份(作用域化的)CSS —— 不是 iframe。
import { useEffect, useRef, Component, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import AgentsApp from '../../agents/App';
import { applyIvyeaOpsTheme } from '../../agents/utils/ivyeaOpsTheme';
import '../../agents/index.css';

class CcuiBoundary extends Component<{ children: ReactNode }, { err: Error | null }> {
  state = { err: null as Error | null };
  static getDerivedStateFromError(err: Error) { return { err }; }
  componentDidCatch(err: Error) { console.error('[Agents render error]', err); }
  render() {
    if (this.state.err) {
      return (
        <pre style={{ padding: 16, margin: 0, color: '#ff9090', whiteSpace: 'pre-wrap', fontSize: 12, lineHeight: 1.5, overflow: 'auto', height: '100%', fontFamily: 'monospace' }}>
          {'Agents 渲染错误:\n\n' + this.state.err.message + '\n\n' + (this.state.err.stack || '')}
        </pre>
      );
    }
    return this.props.children;
  }
}

export default function Agents() {
  const hostRef = useRef<HTMLDivElement>(null);
  const rootRef = useRef<Root | null>(null);
  const appHostRef = useRef<HTMLDivElement | null>(null);
  const portalHostRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!hostRef.current) return;
    const host = hostRef.current;
    const appHost = document.createElement('div');
    const portalHost = document.createElement('div');

    appHost.className = 'agents-app-root';
    appHost.style.width = '100%';
    appHost.style.height = '100%';
    portalHost.id = 'agents-portal-root';
    host.appendChild(appHost);
    host.appendChild(portalHost);
    appHostRef.current = appHost;
    portalHostRef.current = portalHost;

    // 主题:初始注入 ops 当前主题,并监听 ops 主题切换 —— 注入到 #agents-root 容器
    const syncTheme = () => {
      const theme = localStorage.getItem('ivyea-ops.theme') || 'dark';
      applyIvyeaOpsTheme(theme, host);
    };
    syncTheme();
    const onThemeChange = (e: Event) => {
      const t = (e as CustomEvent<string>).detail;
      applyIvyeaOpsTheme(typeof t === 'string' ? t : (localStorage.getItem('ivyea-ops.theme') || 'dark'), host);
    };
    window.addEventListener('ivyea-ops:theme-changed', onThemeChange);

    if (!rootRef.current) {
      rootRef.current = createRoot(appHost);
    }
    rootRef.current.render(
      <CcuiBoundary>
        <AgentsApp />
      </CcuiBoundary>,
    );
    return () => {
      window.removeEventListener('ivyea-ops:theme-changed', onThemeChange);
      const r = rootRef.current;
      rootRef.current = null;
      r?.unmount();
      if (portalHost.parentNode === host) host.removeChild(portalHost);
      if (appHost.parentNode === host) host.removeChild(appHost);
      appHostRef.current = null;
      portalHostRef.current = null;
    };
  }, []);

  return (
    <div
      id="agents-root"
      ref={hostRef}
      style={{ position: 'relative', width: '100%', height: '100%', overflow: 'hidden', transform: 'translateZ(0)' }}
    />
  );
}
