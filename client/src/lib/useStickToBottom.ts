/**
 * 流式对话的「跟随滚动」——按**用户意图**判定，不按滚动位置判定。
 *
 * 为什么不能用"离底部还有多远"来判：
 *   scroll 事件是**异步派发**的（每帧最多一次），而流式输出每秒来几十个 token、
 *   每个都同步执行一次 `scrollTop = scrollHeight`。真实顺序是——
 *     用户滚上去 → 浏览器排队一个 scroll 事件
 *     → 下一个 token 抢先把 scrollTop 拍回底部
 *     → 排队的 scroll 事件这才派发，量到的 gap 已经是 0
 *   于是"用户在往上翻"这件事永远测不出来，手一松就被扯回底部，根本读不了。
 *
 * 所以这里反过来：
 *   **关掉跟随**认输入事件（wheel / touchmove / PageUp…）——它们是同步派发的，
 *   抢得赢 token；
 *   **打开跟随**只认两件事：用户自己滚到底、或点「回到底部」。而且 scroll 事件
 *   带程序性滚动护栏 —— 我们自己写进去的那次滚动不参与判定，否则等于自己把
 *   自己判回"贴底"。
 *
 * 跟随本身用 rAF 合并：一帧最多写一次 scrollTop，长报告流式时省掉几十次布局。
 */
import { useCallback, useEffect, useRef, useState } from "react";

function bottomGap(el: HTMLElement) {
  return el.scrollHeight - el.scrollTop - el.clientHeight;
}
/**
 * 往上滚时的"还算贴着底"容差 —— 只留给分数像素和惯性回弹，**不能放宽**。
 *
 * 放宽就等于回到出事前那套：一格滚轮才 100px 左右，比"一屏 20%"（大屏上 140px+）
 * 还小，于是"用户往上翻了一格"被判成"还贴着底"，下一个 token 又把他拍回底部 ——
 * 滑一下弹一下，永远翻不上去。用户报的就是这个。
 */
const RESUME_GAP = 24;
/**
 * 往下滚时的"到底了"判据，可以宽。流还在长，用户不可能正正好停在 0 像素处；
 * 卡死 0 的话他滚到底也恢复不了跟随，只能去点按钮。
 */
function detachThreshold(el: HTMLElement) {
  return Math.max(40, el.clientHeight * 0.2);
}

export type StickToBottom = {
  /** 当前是否跟随（= 贴底）。脱离时用它渲染「回到底部」。 */
  atBottom: boolean;
  /** 手动回到底部并恢复跟随。 */
  scrollToBottom: () => void;
  /**
   * 调用方自己的程序性滚动（如"把刚发出的问题顶到视野上方"）。
   * 必须走这里而不是直接写 scrollTop —— 否则那一下会被 scroll 监听当成
   * "用户翻上去了"，跟随就此关掉，后面的回答一个字都不跟。
   */
  scrollTo: (top: number) => void;
  /** 只改跟随状态，不动滚动位置。 */
  setFollow: (on: boolean) => void;
};

/**
 * @param ref  滚动容器（overflow-y:auto 的那一个）
 * @param deps 内容变化的依赖 —— 变了就尝试跟随一次
 */
export function useStickToBottom(
  ref: React.RefObject<HTMLElement | null>,
  deps: unknown[],
): StickToBottom {
  const stick = useRef(true);
  const [atBottom, setAtBottom] = useState(true);
  // 我们自己写进去的 scrollTop 与写入时刻。用来认出"这次 scroll 事件是我干的"。
  const selfTop = useRef(-1);
  const selfAt = useRef(0);
  const raf = useRef(0);

  const setFollow = useCallback((next: boolean) => {
    if (stick.current === next) return;
    stick.current = next;
    setAtBottom(next);
  }, []);

  /** 程序性滚动：先记账再写，这样 scroll 监听能认出来不是用户干的。 */
  const scrollTo = useCallback((top: number) => {
    const el = ref.current;
    if (!el) return;
    selfTop.current = top;
    selfAt.current = Date.now();
    el.scrollTop = top;
  }, [ref]);

  const jump = useCallback(() => {
    const el = ref.current;
    if (el) scrollTo(el.scrollHeight);      // 浏览器会夹到最大值，比对时留容差
  }, [ref, scrollTo]);

  const scrollToBottom = useCallback(() => {
    setFollow(true);
    jump();
  }, [jump, setFollow]);

  // ── 用户意图：同步派发的输入事件，抢在下一个 token 之前把跟随关掉 ──────────
  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    // 往上翻 = 立刻停跟随，**不看翻了多远**。这一下必须在这里做掉：它是同步
    // 派发的，抢在下一个 token 之前；等 scroll 事件就来不及了。
    // 往下翻不在这儿判 —— 滚轮事件是在滚动**生效之前**派发的，这时候量到的还是
    // 旧位置。交给下面的 onScroll 按方向判，晚一帧恢复跟随没有任何代价。
    const onWheel = (e: WheelEvent) => { if (e.deltaY < 0) setFollow(false); };
    let touchY = 0;
    const onTouchStart = (e: TouchEvent) => { touchY = e.touches[0]?.clientY ?? 0; };
    const onTouchMove = (e: TouchEvent) => {
      const y = e.touches[0]?.clientY ?? 0;
      if (y > touchY + 2) setFollow(false);            // 手指下滑 = 内容上移 = 往回看
      touchY = y;
    };
    const UP = new Set(["PageUp", "ArrowUp", "Home"]);
    const onKeyDown = (e: KeyboardEvent) => {
      if (UP.has(e.key)) setFollow(false);
      else if (e.key === "End") scrollToBottom();
    };
    // scroll 事件按**方向**判，不按距离判：
    //   往下滚且已经接近底部 → 恢复跟随（流在长，用户很难正正好停在 0 像素处，
    //                                  所以这里可以宽一点）；
    //   往上滚 → 停跟随（拖滚动条、中键自动滚这类没有输入事件可认的操作走这条）。
    // 反过来按距离判就会把"用户刚往上翻了一格（<20% 屏高）"判成"还贴着底"，
    // 于是下一个 token 又把他拍回去 —— 用户报的"滑一下弹一下"就是这么来的。
    let lastTop = el.scrollTop;
    const onScroll = () => {
      const prev = lastTop;
      lastTop = el.scrollTop;
      const clamped = Math.min(selfTop.current, Math.max(0, el.scrollHeight - el.clientHeight));
      const mine = Date.now() - selfAt.current < 120 && Math.abs(el.scrollTop - clamped) <= 2;
      if (mine) return;                                // 这一下是我们自己滚的，不算用户表态
      const gap = bottomGap(el);
      if (el.scrollTop > prev && gap <= detachThreshold(el)) setFollow(true);
      else if (el.scrollTop < prev && gap > RESUME_GAP) setFollow(false);
    };

    el.addEventListener("wheel", onWheel, { passive: true });
    el.addEventListener("touchstart", onTouchStart, { passive: true });
    el.addEventListener("touchmove", onTouchMove, { passive: true });
    el.addEventListener("keydown", onKeyDown);
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("touchstart", onTouchStart);
      el.removeEventListener("touchmove", onTouchMove);
      el.removeEventListener("keydown", onKeyDown);
      el.removeEventListener("scroll", onScroll);
    };
    // ref.current 进依赖：容器可能被换掉（悬浮球在"对话/历史"两个 tab 各挂一个
    // div 到同一个 ref）。只依赖 ref 的话监听会留在已经卸载的那个节点上。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ref.current, setFollow, scrollToBottom]);

  // ── 内容变了就跟一次（rAF 合并）────────────────────────────────────────────
  useEffect(() => {
    if (!stick.current || !ref.current || raf.current) return;
    raf.current = window.requestAnimationFrame(() => {
      raf.current = 0;
      if (stick.current) jump();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => () => { if (raf.current) window.cancelAnimationFrame(raf.current); }, []);

  return { atBottom, scrollToBottom, scrollTo, setFollow };
}

export default useStickToBottom;
