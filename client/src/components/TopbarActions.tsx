import { useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { TOPBAR_SLOT_ID } from "../lib/topbarSlot";

/**
 * 把内容挂到顶栏右侧的板块动作位。
 *
 * ```tsx
 * <TopbarActions>
 *   <button className="tbtn" onClick={…}>⊕ 新建任务</button>
 * </TopbarActions>
 * ```
 *
 * 挂位不存在时（登录页、Setup 这些不走 MainLayout 的路由）什么都不渲染，
 * 而不是抛错 —— 板块不需要知道自己有没有被套在外壳里。
 *
 * **首帧要等一拍。** MainLayout 和板块在同一棵树里，板块的 effect 先于父级
 * 挂载完成时 `getElementById` 可能还拿不到挂位；用一次 state 更新把 portal
 * 推到下一帧，比在父级手工排序可靠。
 */
export default function TopbarActions({ children }: { children: ReactNode }) {
  const [host, setHost] = useState<HTMLElement | null>(null);

  useEffect(() => {
    setHost(document.getElementById(TOPBAR_SLOT_ID));
  }, []);

  if (!host) return null;
  return createPortal(children, host);
}
