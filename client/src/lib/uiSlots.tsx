import { useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * 让一个板块把自己的东西挂进外壳里的两个位置：**顶栏**（属于本页的动作）和
 * **左侧栏的弹性区**（属于本页的次级导航）。
 *
 * ── 为什么要有这个 ──────────────────────────────────────────────────────
 * 服务器终端那一页量下来是这样的：顶栏 46px 只写了「~/服务器终端」，紧接着页面
 * 自己又画一行 44px 再写一遍同样的名字加一排按钮；而左侧栏最高的那块是**任务台
 * 的会话列表**（跟终端毫无关系），真正该在侧栏的「终端列表」却单独占掉中间 260px。
 * 结果终端画面只剩视口的 57%。
 *
 * 位置错了，不是空间不够。所以：动作归顶栏，导航归侧栏，中间只留内容本身。
 *
 * ── 为什么用 portal 而不是把状态提到 MainLayout ─────────────────────────
 * 终端列表的数据、轮询和一堆操作回调都长在 Terminal 里。提到外壳去等于把外壳变成
 * 终端的状态容器，其它板块还得照抄一遍。portal 让**所有权不动**：谁的东西谁渲染，
 * 只是画到了别处。
 *
 * ── 一个必须记住的坑 ────────────────────────────────────────────────────
 * 终端和外部智能体是**常驻挂载**的板块（切走只是隐藏，不卸载），而 portal 出去的
 * 内容不受那个隐藏样式管 —— 不显式判断"当前是不是这一页"，你切到别的板块还会看到
 * 终端的按钮挂在顶栏上。所以这里的 `active` 不是可选优化，是正确性要求。
 */

const TOPBAR_SLOT_ID = "ivyea-topbar-slot";
const RAIL_SLOT_ID = "ivyea-rail-slot";

/** 外壳里的挂载点。没人挂东西时它不占任何位置（空 div，无内外边距）。 */
export function TopbarSlotHost() {
  return <div id={TOPBAR_SLOT_ID} className="tb-slot" />;
}

export function RailSlotHost() {
  return <div id={RAIL_SLOT_ID} className="sb-rail-slot" />;
}

function usePortalTarget(id: string, active: boolean) {
  const [node, setNode] = useState<HTMLElement | null>(null);
  useEffect(() => {
    if (!active) { setNode(null); return; }
    // 挂载点由外壳渲染，可能比板块晚一帧到 —— 找不到就下一帧再找。
    let raf = 0;
    const find = () => {
      const el = document.getElementById(id);
      if (el) setNode(el);
      else raf = requestAnimationFrame(find);
    };
    find();
    return () => cancelAnimationFrame(raf);
  }, [id, active]);
  return node;
}

/** 把本页的动作画进顶栏右侧。`active`=当前是不是这一页（常驻板块必须传对）。 */
export function TopbarActions({ active, children }: { active: boolean; children: ReactNode }) {
  const node = usePortalTarget(TOPBAR_SLOT_ID, active);
  return node ? createPortal(children, node) : null;
}

/** 把本页的次级导航画进左侧栏的弹性区。 */
export function SidebarRail({ active, children }: { active: boolean; children: ReactNode }) {
  const node = usePortalTarget(RAIL_SLOT_ID, active);
  return node ? createPortal(children, node) : null;
}

/** 外壳用它决定弹性区归谁：有板块接管时就不再画任务台的会话列表。 */
export const RAIL_OWNED_PATHS = new Set(["/terminal"]);
