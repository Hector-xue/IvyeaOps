import { Suspense, lazy, useEffect, useState } from 'react';
import type { CommandPaletteProps } from './CommandPalette';

/**
 * 命令面板的挂载宿主。
 *
 * 面板本体连着 cmdk、Radix Dialog 和几个数据源 hook，一共约 55 kB，而它只有按了
 * Cmd/Ctrl+K 才会出现 —— 很多会话从头到尾都没按过。这里只留一个键盘监听，
 * **第一次按下时才把本体拉进来**，并直接以打开状态挂上，用户感觉不到差别。
 *
 * 挂上之后本宿主就不再监听：后续的开关交给面板自己那套逻辑，避免两个监听
 * 同时 toggle 互相抵消。
 */
const CommandPalette = lazy(() => import('./CommandPalette'));

export default function CommandPaletteHost(props: Omit<CommandPaletteProps, 'defaultOpen'>) {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    if (mounted) return;
    const onKeyDown = (e: KeyboardEvent) => {
      const isCmdK =
        (e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'k';
      if (!isCmdK) return;
      e.preventDefault();
      setMounted(true);
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [mounted]);

  if (!mounted) return null;

  return (
    <Suspense fallback={null}>
      <CommandPalette {...props} defaultOpen />
    </Suspense>
  );
}
