/**
 * 「一键收起执行过程」。
 *
 * 为什么需要它：一轮里会渲染**多个**过程块（步骤按工具批次分组），一个会话又有很多轮，
 * 于是"把执行过程收起来"这件事要一个一个点过去。Windows 上一轮跑几十步的时候，
 * 屏幕上能有十几个块 —— 逐个点是用户实际抱怨过的事。
 *
 * 摆在输入框底下那一行（DockMeta）里，因为那是**唯一一处从头到尾都在视野里**的地方：
 * 放在滚动区顶部的话，人往下翻着看过程时它就滚没了，恰恰是最想收起来的时候够不着。
 */
import { useSyncExternalStore } from "react";
import Icon from "../Icon";
import { getCollapseAll, setCollapseAll, subscribeFeedCollapse } from "../../lib/feedCollapse";

export default function CollapseAllFeeds() {
  const collapsed = useSyncExternalStore(subscribeFeedCollapse, getCollapseAll, () => false);
  return (
    <button
      type="button"
      className="cc-collapse-all"
      onClick={() => setCollapseAll(!collapsed)}
      title={collapsed
        ? "展开所有轮次的执行过程"
        : "收起所有轮次的执行过程（之后单独点某一块仍可单独展开）"}
    >
      <Icon name={collapsed ? "chev-down" : "chev-up"} size={12} />
      {collapsed ? "展开过程" : "收起过程"}
    </button>
  );
}
