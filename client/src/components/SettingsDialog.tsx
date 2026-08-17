import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import AppDialog from "./AppDialog";

// 设置页很大（1700+ 行，含健康检查、MCP、通知…），懒加载，别拖累首屏包。
const HubSettings = lazy(() => import("../pages/workbench/HubSettings"));

/** 打开设置对话框的全局事件。detail.section 可选，用来直接落到某个分区。 */
export const OPEN_SETTINGS_EVENT = "ivyea-ops:open-settings";

export function openSettings(section?: string) {
  window.dispatchEvent(new CustomEvent(OPEN_SETTINGS_EVENT, { detail: { section } }));
}

type Item = { id: string; title: string };

/**
 * 系统配置对话框。
 *
 * 左侧目录**从已渲染的分区里扫出来**，而不是在这里再手抄一份清单：设置页有二十来个
 * 分区，而且还在增减 —— 手抄的那份一定会和页面对不上，且对不上的时候毫无征兆
 * （目录里点一下什么都不发生）。扫 DOM 换来的是"页面上有什么，目录里就有什么"。
 */
export default function SettingsDialog({ section, onClose }: { section?: string; onClose: () => void }) {
  const bodyRef = useRef<HTMLDivElement>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [active, setActive] = useState("");

  // **用相对容器的实际矩形算，不要用 offsetTop。** offsetTop 是相对"最近的已定位
  // 祖先"的，滚动容器没定位时它相对的是更外层，算出来的位置系统性偏移 —— 表现为
  // 跳过去了但差一截，以及左栏高亮总是停在上一项。
  const offsetIn = (host: HTMLElement, el: HTMLElement) =>
    el.getBoundingClientRect().top - host.getBoundingClientRect().top + host.scrollTop;

  // 跳转期间别让滚动监听改高亮 —— smooth 滚动和"内容还在长"会让它一路乱跳。
  const pinnedRef = useRef(0);

  /**
   * 跳到某个分区。**要滚到位置稳定为止**：这一页的内容是异步来的（健康检查、
   * MCP 列表…），滚过去之后上面的分区陆续变高，会把目标一路往下推，
   * 最终停在的位置和目标差着好几屏。
   */
  const jump = useCallback((id: string) => {
    const host = bodyRef.current;
    if (!host) return;
    setActive(id);
    pinnedRef.current = Date.now() + 2500;
    let last = Number.NaN;
    let stable = 0;
    const deadline = Date.now() + 2500;
    const step = () => {
      const el = host.querySelector<HTMLElement>(`[data-sec="${CSS.escape(id)}"]`);
      if (!el) return;
      const target = offsetIn(host, el) - 8;
      host.scrollTo({ top: target, behavior: "auto" });
      stable = Math.abs(target - last) < 2 ? stable + 1 : 0;
      last = target;
      if (stable >= 2 || Date.now() > deadline) {
        pinnedRef.current = 0;
        return;
      }
      window.setTimeout(step, 120);
    };
    step();
  }, []);

  // 分区是设置页加载完才有的，而它的数据是异步来的 —— 扫一次会扫到半个页面。
  // 用 MutationObserver 跟着变，扫到的目录才和页面一致。
  useEffect(() => {
    const host = bodyRef.current;
    if (!host) return;
    let raf = 0;
    const scan = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const found: Item[] = [];
        host.querySelectorAll<HTMLElement>(".hs-section").forEach((sec, i) => {
          const title = sec.querySelector(".hs-section-title")?.textContent?.trim()
            || sec.querySelector(".hs-section-hd b")?.textContent?.trim() || "";
          if (!title) return;
          const id = sec.id || `sec-${i}`;
          sec.dataset.sec = id;
          found.push({ id, title });
        });
        setItems((prev) =>
          prev.length === found.length && prev.every((p, i) => p.id === found[i].id && p.title === found[i].title)
            ? prev : found);
      });
    };
    scan();
    const mo = new MutationObserver(scan);
    mo.observe(host, { childList: true, subtree: true });
    return () => { mo.disconnect(); cancelAnimationFrame(raf); };
  }, []);

  // 深链（账户菜单的「字体与字号」）：等目录扫出来、目标真的在了再滚。
  useEffect(() => {
    if (!section || !items.length) return;
    if (items.some((x) => x.id === section)) jump(section);
  }, [section, items, jump]);

  // 滚到哪一段就高亮哪一段 —— 二十来个分区，没有这个就不知道自己在哪。
  useEffect(() => {
    const host = bodyRef.current;
    if (!host) return;
    // **要等滚动停下来再算。** smooth 滚动过程中每一帧都会触发 scroll，中途那次算出的
    // 是路过的分区 —— 表现为"跳过去了，但左栏高亮停在上一项"。
    let timer = 0;
    const compute = () => {
      if (Date.now() < pinnedRef.current) return;   // 跳转还没落定，别抢高亮
      let cur = "";
      const hostTop = host.getBoundingClientRect().top;
      host.querySelectorAll<HTMLElement>("[data-sec]").forEach((sec) => {
        if (sec.getBoundingClientRect().top - hostTop <= 80) cur = sec.dataset.sec || cur;
      });
      if (cur) setActive(cur);
    };
    const onScroll = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(compute, 120);
    };
    host.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      host.removeEventListener("scroll", onScroll);
      window.clearTimeout(timer);
    };
  }, [items.length]);

  return (
    <AppDialog
      title="系统配置"
      icon="⚙"
      onClose={onClose}
      nav={
        <nav className="app-dialog-nav-list">
          {items.map((it) => (
            <button
              key={it.id}
              className={"app-dialog-nav-item" + (active === it.id ? " active" : "")}
              onClick={() => jump(it.id)}
            >
              {it.title}
            </button>
          ))}
          {items.length === 0 && <div className="app-dialog-nav-empty">加载中…</div>}
        </nav>
      }
    >
      <div className="hs-in-dialog" ref={bodyRef}>
        <Suspense fallback={<div className="app-dialog-loading">加载设置…</div>}>
          <HubSettings focusSection={section || ""} />
        </Suspense>
      </div>
    </AppDialog>
  );
}
