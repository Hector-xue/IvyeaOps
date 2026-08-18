/**
 * 输入框底下那一行（上下文进度条 + 会话统计条）的容器。
 *
 * 它只负责一件事，但这件事必须由 JS 做：**不管里面有多少内容，永远只占一行。**
 *
 * 为什么不能交给 CSS：这一行的内容长度是会变的（跑完一轮多出四五项、耗时从
 * "9.8秒"长到"11分3秒"、token 从 178 变成 12.4K），而它下面就是输入框 ——
 * 一旦折行，输入区整块被往上顶，页面在跑任务的过程中自己跳一下。`flex-wrap:nowrap`
 * 只能让它溢出（右边那几项被裁掉，而最右边恰恰是 token 用量），`text-overflow`
 * 对 flex 子项不生效。所以：**量一下，装不下就把字缩小**，直到装得下为止。
 *
 * 缩放范围 0.76em → 0.56em（静谧主题下 12.2px → 9px）。到底了还装不下就只能裁，
 * 但那需要窄到手机竖屏还开着一堆统计项，实测 390px 下 7 项也能塞进去。
 */
import { useCallback, useLayoutEffect, useRef, type ReactNode } from "react";

/** 起始字号（相对输入框正文），和改动前的 .cc-stats 一致。 */
const MAX_EM = 0.76;
/** 缩到这里就不再缩了 —— 再小就不是"小字"而是"看不清"。 */
const MIN_EM = 0.56;
const STEP = 0.02;

export default function DockMeta({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  /** 上一次量的"内容长度|可用宽度"。一样就跳过 —— 见 fit 里的说明。 */
  const lastRef = useRef("");

  /**
   * 每次都**从最大字号重新试**，而不是在上一次的基础上加减：内容会变短
   * （比如切到一条没有用量的历史会话），从上次的小字号往回长需要额外一套
   * "试着放大→放不下再退回"的逻辑，而那正是抖动的来源。从头试是幂等的。
   */
  const fit = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    // 跑任务时这一行每 250ms 重渲染一次（耗时在跳），而 11.2秒→11.4秒 的宽度是一样的。
    // 用"字符数 + 可用宽度"当指纹跳过无谓的测量：下面每缩一档都要读一次
    // scrollWidth，那是一次强制重排。
    const sign = (el.textContent || "").length + "|" + el.clientWidth;
    if (sign === lastRef.current) return;
    lastRef.current = sign;
    let em = MAX_EM;
    el.style.setProperty("--meta-fs", `${em}em`);
    // scrollWidth > clientWidth 就是溢出了。留 1px 余量：亚像素宽度下两者
    // 会差 0.x，不留余量会白缩一档。
    let guard = 0;
    while (el.scrollWidth - el.clientWidth > 1 && em > MIN_EM && guard++ < 32) {
      em = Math.round((em - STEP) * 100) / 100;
      el.style.setProperty("--meta-fs", `${em}em`);
    }
  }, []);

  // useLayoutEffect：必须在浏览器画之前量完并改完，否则用户会看到"先大一下再缩回去"。
  // 没有依赖数组 —— 内容每次重渲染都可能变长变短（耗时每 250ms 跳一次），
  // 而这里量的是一个只有几个子元素的行，代价可以忽略。
  useLayoutEffect(fit);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    // 侧栏拖宽、右侧产物栏展开、窗口缩放都会改这一行的可用宽度，
    // 而它们都不引起这个组件重渲染。
    const ro = new ResizeObserver(() => { lastRef.current = ""; fit(); });
    ro.observe(el);
    return () => ro.disconnect();
  }, [fit]);

  return <div className="cc-dock-meta" ref={ref}>{children}</div>;
}
