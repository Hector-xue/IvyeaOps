// 板块的两条个人偏好：钉在侧栏的、最近去过的。
//
// **只存 localStorage，不上后端。** 它们和主题、字号是同一类东西 —— 纯显示
// 偏好、绑设备、丢了也不影响任何数据。为它们加一张表、一套接口和一次迁移，
// 收益和成本完全不成比例。
//
// ⚠️ 和 `api/skillTools` 里的 pin 不是一回事。那个钉的是**技能工具**（有后端、
// 跨设备、进「我的工具」分组）；这里钉的是**板块**。两者共用侧栏的同一片区域
// 但来源不同，别把它们并成一个 —— 上一版就是因为名字像而差点混用。

const PIN_KEY = "ivyea-ops.pinned-boards";
const RECENT_KEY = "ivyea-ops.recent-boards";
const RECENT_MAX = 8;

/** pin/recent 变动后广播，侧栏和浮层据此刷新。 */
export const BOARD_PREFS_EVENT = "ivyea-ops:board-prefs-changed";

function read(key: string): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(raw) ? raw.filter((v): v is string => typeof v === "string") : [];
  } catch {
    return [];   // 隐私模式 / 脏数据：当作空，不要让侧栏挂掉
  }
}

function write(key: string, list: string[]): void {
  try { localStorage.setItem(key, JSON.stringify(list)); } catch { /* ignore */ }
  try { window.dispatchEvent(new Event(BOARD_PREFS_EVENT)); } catch { /* ignore */ }
}

export function getPinnedBoards(): string[] {
  return read(PIN_KEY);
}

export function isBoardPinned(to: string): boolean {
  return read(PIN_KEY).includes(to);
}

/** 钉 / 取消钉。返回操作后是否处于钉住状态。 */
export function toggleBoardPin(to: string): boolean {
  const list = read(PIN_KEY);
  const i = list.indexOf(to);
  if (i >= 0) { list.splice(i, 1); write(PIN_KEY, list); return false; }
  list.push(to);
  write(PIN_KEY, list);
  return true;
}

export function getRecentBoards(): string[] {
  return read(RECENT_KEY);
}

/** 记一次访问。最近去过的排最前，去重，最多 RECENT_MAX 条。 */
export function pushRecentBoard(to: string): void {
  const list = read(RECENT_KEY).filter((v) => v !== to);
  list.unshift(to);
  write(RECENT_KEY, list.slice(0, RECENT_MAX));
}
