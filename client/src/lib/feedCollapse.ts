/**
 * 执行过程的**全局折叠默认值**。
 *
 * 起因：一轮里会渲染**多个** ActivityFeed（步骤按工具批次分组），一个会话又有很多轮，
 * 于是"收起执行过程"这件事要一个个点过去。Windows 上一次跑几十步的时候尤其难受。
 *
 * 设计上刻意分成两层，而不是简单地把 collapsed 提升成全局状态：
 *
 *   - **默认值**（这里）：一键收起/展开改的是它，所有"用户没单独动过"的过程块跟着走。
 *   - **单块覆盖**：用户点了某一块的收起/展开，那一块从此听自己的，不再被全局值带跑。
 *     否则会出现"我特意展开了正在跑的这块，结果按一下全局收起全没了，再也找不回来"。
 *
 * 覆盖值刻意**不持久化**：它是"我现在想看这一块"的临时意图，跨会话留着只会让人
 * 莫名其妙地看到某几块是展开的。全局默认值则持久化 —— 那是长期偏好。
 */

const KEY = "ivyea.console.feedCollapsed";

type Listener = () => void;

let collapsedAll = load();
/** feedKey -> 用户单独设定的值。null 表示跟随全局。 */
const overrides = new Map<string, boolean>();
const listeners = new Set<Listener>();

function load(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false; // 隐私模式/禁用存储：按展开处理，不影响使用
  }
}

function emit(): void {
  for (const fn of listeners) fn();
}

export function subscribeFeedCollapse(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** 全局默认值（一键收起/展开读写的就是它） */
export function getCollapseAll(): boolean {
  return collapsedAll;
}

/**
 * 一键收起 / 展开。
 *
 * 会**清掉所有单块覆盖** —— 用户按下这个按钮时的意图是"整页都给我收起来"，
 * 如果之前单独展开过的几块还顽固地开着，这个按钮看起来就是失灵的。
 */
export function setCollapseAll(next: boolean): void {
  collapsedAll = next;
  overrides.clear();
  try {
    localStorage.setItem(KEY, next ? "1" : "0");
  } catch {
    /* 存不下不影响本次会话 */
  }
  emit();
}

/** 某一块当前该不该收起 */
export function isFeedCollapsed(key: string): boolean {
  const own = overrides.get(key);
  return own === undefined ? collapsedAll : own;
}

/** 单块切换：从此这一块听自己的 */
export function toggleFeed(key: string): void {
  overrides.set(key, !isFeedCollapsed(key));
  emit();
}

/** 供测试复位 */
export function __resetFeedCollapse(): void {
  collapsedAll = false;
  overrides.clear();
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
  emit();
}
