import type { ReactNode } from "react";
import AppDialog from "./AppDialog";

/**
 * 版本更新说明 —— 升级后第一次打开时弹一次，看过就不再弹。
 *
 * **为什么要有这个东西**：这一版把「领星 ERP」整个板块并进了运营驾驶舱。老用户
 * 第二天打开，侧边栏里那一项没了。重定向能保证书签不 404，却没法告诉一个正在找
 * 它的人"它搬到哪儿去了" —— 那个人只会得出"功能被删了"的结论。
 *
 * 三条设计约束：
 *
 * 1. **不可随手关闭**（`dismissible={false}`）。这是错过就不会再自己出现的东西，
 *    点背景误关一次，用户就永远看不到搬家通知了。必须点「知道了」。
 * 2. **全新安装不弹。** 一个从没见过老界面的人，"领星搬到驾驶舱了"是句废话，而
 *    开局先甩一个必须点掉的弹窗是很差的第一印象。判据是浏览器里有没有用过这个站
 *    的痕迹（见 `looksLikeReturningUser`）。
 * 3. **锚在公告 id 上，不锚在版本号上。** 版本号可能因为发布节奏调整而变（patch
 *    还是 minor 常常是最后才定的），锚在版本号上会导致要么重弹、要么漏弹。
 */

export type Release = {
  /** 公告 id。一经发布不要改 —— 它就是"看过没有"的那把钥匙。 */
  id: string;
  title: string;
  date: string;
  /** true = 只弹给管理员（讲的是管理员才看得到的板块）。 */
  adminOnly?: boolean;
  body: ReactNode;
};

/** 最新的排在最前。目前只弹最新这一条。 */
export const RELEASES: Release[] = [
  {
    id: "2026-08-lingxing-into-cockpit",
    title: "领星 ERP 搬进了「运营驾驶舱」",
    date: "2026-08-29",
    adminOnly: true,
    body: (
      <>
        <p className="wn-lead">
          侧边栏里的「领星 ERP」不见了 —— 它不是被删掉，是并进了「<b>运营驾驶舱</b>」。
          老地址和老书签会自动跳过去，停在你原来那个标签上。
        </p>

        <h4>为什么合</h4>
        <p>
          这两个板块本来就是同一件事的两半：驾驶舱负责"看"，领星负责"数据从哪来、
          怎么落地"。最别扭的是工单 —— 在广告看板点「调预算」生成的工单，
          <b>以前必须切到另一个板块才能确认</b>，而两边连一个跳转都没有。
        </p>

        <h4>现在去哪找</h4>
        <ul>
          <li>
            驾驶舱的标签行分成了两组：<b>市场</b>（看别人，数据来自 Sorftime）和
            <b>自家店铺</b>（看自己，数据来自领星）。
          </li>
          <li>
            自家店铺组 = <b>促销日历 · 广告看板 · 优化建议 · 工单</b>。
            工单待确认的张数<b>常驻在标签上</b>，不进那一页也看得见。
          </li>
          <li>
            <b>数据浏览 / 审计 / 配置</b>收进了右上角的「<b>⚙ 领星工具</b>」。
            这三块低频，不占标签行。
          </li>
          <li>
            切到自家店铺组时，右上角那条会从"数据源 + 站点"换成"连接状态 + 店铺" ——
            这半边是按<b>店铺</b>取数的，跟站点无关。
          </li>
        </ul>

        <h4>顺带这几样</h4>
        <ul>
          <li>
            <b>领星的「大盘」标签撤掉了</b>，由「广告看板」承接 —— 后者本来就是它的
            超集。大盘独有的本币金额和分店对比表都补了进去。
          </li>
          <li>
            <b>「驾驶舱预热」和「小幅止血快车道」终于有开关了</b>（⚙ 领星工具 → 配置）。
            这两个功能的后端一直在跑，但界面上从来没有地方能打开它们。两个都<b>默认关</b>。
            开了预热之后，广告看板和促销日历"打开就有数"，不用每次现拉。
          </li>
          <li>
            广告看板<b>跨币种时不再把不同货币直接相加当金额显示</b> —— 那种合计只能看
            涨跌趋势，界面上现在会明说，准确数字看分店对比表。
          </li>
        </ul>

        <p className="wn-foot">
          写操作的安全模型一道没动：护栏 → 复核 → 你亲手确认 → 执行并存回滚快照，
          「操作开关」默认关、带自动失效。
        </p>
      </>
    ),
  },
];

const LS_KEY = "ivyea-ops.whatsnew";

/**
 * 这个浏览器以前用过这个站吗？
 *
 * 用来把"升级上来的老用户"和"刚装好第一次打开"分开。判据是这个站自己写下的那些
 * 使用痕迹 —— 主题、上次停在哪个标签、看过哪些引导、领星板块的界面状态。
 * 任意一个存在，就说明这不是第一次打开。
 *
 * **宁可漏弹也不要误弹**：清过站点数据、换了浏览器的老用户会被当成新用户而错过这
 * 条通知，代价是他自己去驾驶舱里找一下；反过来给全新安装的人开局甩一个必须点掉的
 * 弹窗，代价是第一印象。
 */
function looksLikeReturningUser(): boolean {
  const marks = [
    "lingxing.ui.v1",          // 用过领星板块 —— 最精准的一个
    "ivyea-ops-home-tab",      // 用过驾驶舱
    "ivyea-ops.theme",         // 换过主题
    "ivyea-ops.shell",         // 切过外壳模式
    "ivyea-tour:/dashboard",   // 看过驾驶舱引导
    "ivyea-tour:/console",     // 看过任务台引导
  ];
  try {
    return marks.some((k) => localStorage.getItem(k) !== null);
  } catch {
    return false;   // 读不到 localStorage（隐私模式）→ 当作新用户，不打扰
  }
}

function readSeen(): string[] {
  try { return JSON.parse(localStorage.getItem(LS_KEY) || "[]"); } catch { return []; }
}

function markSeen(id: string): void {
  try {
    const seen = readSeen();
    if (!seen.includes(id)) localStorage.setItem(LS_KEY, JSON.stringify([...seen, id]));
  } catch { /* 写不进去就下次再弹，不影响使用 */ }
}

/**
 * 现在该弹哪一条？没有就返回 null。
 *
 * 全新安装时**不弹但要记成已读** —— 否则等他用一阵子、浏览器里有了痕迹之后，
 * 这条早就过时的搬家通知会突然冒出来。
 */
export function pendingRelease(isAdmin: boolean): Release | null {
  const seen = readSeen();
  const candidates = RELEASES.filter((r) => !seen.includes(r.id) && (!r.adminOnly || isAdmin));
  if (candidates.length === 0) return null;
  if (!looksLikeReturningUser()) {
    for (const r of candidates) markSeen(r.id);
    return null;
  }
  return candidates[0];
}

export default function WhatsNew({ release, onClose }: { release: Release; onClose: () => void }) {
  const done = () => { markSeen(release.id); onClose(); };
  return (
    <AppDialog title={release.title} icon="✦" width={680} dismissible={false} onClose={done}>
      {/* 正文自己滚，按钮固定在下边 —— **这不是排版偏好，是能不能关掉的问题**：
          .app-dialog-body 是 overflow:hidden，正文一长就被直接切掉、滚都滚不动，
          而这个弹窗唯一的出口就是那个按钮。 */}
      <div className="wn">
        <div className="wn-scroll">
          <div className="wn-date">更新于 {release.date}</div>
          {release.body}
        </div>
        <div className="wn-actions">
          <button className="cp-btn primary" onClick={done}>知道了</button>
        </div>
      </div>
    </AppDialog>
  );
}
