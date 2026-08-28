import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchCockpitStatus } from "../../../api/cockpit";

/**
 * 经营侧面板（促销日历 / 广告看板）的失败态。
 *
 * 这两块的数据全部来自领星，没配凭证、没开总开关时后端会抛
 * `领星集成未启用（总开关关闭）` 之类的 400 —— 以前界面就把这行字原样贴出来，
 * **一个没有去处的错误**：用户既不知道该去哪配，也不知道配了要多久生效。
 *
 * 所以这里把"配置缺失"和"真的出错了"分开：
 *
 * - 判定**不靠匹配错误文案**（文案会改、会翻译、会被网关改写），而是回头问一次
 *   `/cockpit/status` 的 `lingxing_enabled` —— 那是同一个后端对同一件事的判断。
 * - 判定不出来（status 也挂了）就退回显示原始错误，绝不假装是"没配置"把真故障藏掉。
 */
export default function LingXingGate({ error }: { error: string }) {
  const [notReady, setNotReady] = useState<boolean | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    let alive = true;
    if (!error) { setNotReady(null); return; }
    fetchCockpitStatus()
      .then(s => { if (alive) setNotReady(!s.lingxing_enabled); })
      .catch(() => { if (alive) setNotReady(false); });   // 问不出来 → 当成真故障
    return () => { alive = false; };
  }, [error]);

  if (!error) return null;

  // 还没问出结果：先显示原始错误，别闪一下"未配置"再改口。
  if (notReady !== true) return <div className="cp-error">{error}</div>;

  return (
    <div className="cp-empty">
      <div className="cp-empty-title">还没接上领星</div>
      <div className="cp-empty-desc">
        促销日历和广告看板读的是你自己店铺的经营数据，需要先填领星 OpenAPI 凭证并打开数据总开关。
        <br />配好之后回到这里点「立即刷新」即可；想让它「打开就有数」，在同一页把「驾驶舱预热」也打开。
      </div>
      <div style={{ marginTop: 12 }}>
        {/* 阶段 1 合并后这里改成打开板块内的配置对话框，不再整页跳转。 */}
        <button className="cp-btn primary" onClick={() => navigate("/lingxing?tab=config")}>
          去配置领星
        </button>
      </div>
    </div>
  );
}
