import { useEffect, useRef, type ReactNode } from "react";

/**
 * 应用级对话框外壳 —— 系统配置、使用手册这类"看一眼就走"的内容用它。
 *
 * 为什么不再整页跳转：这两样都不是"去了另一个地方"，而是"在当前工作上方看一眼"。
 * 整页跳转（哪怕是铺满屏幕的不透明浮层）会让人丢掉上下文 —— 看完还得想办法回到
 * 刚才那一页。对话框把这件事讲清楚：背后那一页还在，关掉就回去了。
 *
 * 结构对标常见的设置面板：左边一列目录，右边内容，左上角关闭。
 *
 * 无障碍与手感上必须做到的几件：Esc 关、点背景关、打开时锁住背景滚动、
 * 关闭后把焦点还给触发它的那个元素（否则键盘用户会被扔回页面顶部）。
 */
export default function AppDialog({
  title,
  icon,
  nav,
  children,
  onClose,
  width = 1040,
}: {
  title: string;
  icon?: ReactNode;
  /** 左侧目录。不传就只有内容区（窄对话框）。 */
  nav?: ReactNode;
  children: ReactNode;
  onClose: () => void;
  width?: number;
}) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey, true);

    // 锁背景滚动。**记下原值再恢复**，不能无脑写 "" —— 别的地方（移动端抽屉）
    // 也会锁，直接清空会把它们的锁一起解掉。
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    panelRef.current?.focus();
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.body.style.overflow = prev;
      opener?.focus?.();
    };
  }, [onClose]);

  return (
    <div className="app-dialog-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div
        className={"app-dialog" + (nav ? "" : " app-dialog-plain")}
        style={{ maxWidth: width }}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        ref={panelRef}
        tabIndex={-1}
      >
        {nav && (
          <div className="app-dialog-nav">
            <button className="app-dialog-close" onClick={onClose} title="关闭（Esc）" aria-label="关闭">✕</button>
            {nav}
          </div>
        )}
        <div className="app-dialog-main">
          <div className="app-dialog-head">
            {icon && <span className="app-dialog-icon">{icon}</span>}
            <span className="app-dialog-title">{title}</span>
            {!nav && (
              <button className="app-dialog-close app-dialog-close-inline" onClick={onClose}
                      title="关闭（Esc）" aria-label="关闭">✕</button>
            )}
          </div>
          <div className="app-dialog-body">{children}</div>
        </div>
      </div>
    </div>
  );
}
