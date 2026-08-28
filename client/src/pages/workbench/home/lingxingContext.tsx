import {
  createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode,
} from "react";
import { api } from "../../../api/client";
import { errText } from "../../../lib/errText";
import type { Dataset, LingXingStatus } from "../lingxingTypes";

/**
 * 经营侧（原「领星 ERP」板块）的共享状态。
 *
 * 合并进驾驶舱之前，这些东西散在三个组件里各拉各的：`LingXing` 每 10 秒轮
 * `/lingxing/status`，`LingXingOperate` 每 5 秒又轮一次 status 外加工单，
 * `LingXingAudit` 每 8 秒轮审计。那时它们是**互斥的 tab**，同一时刻只有一个活着，
 * 所以三份轮询从来没撞在一起过。
 *
 * 合并之后不一样了：工单徽标要常驻在 tab 行上（不进工单页也得知道有几张待确认），
 * 齿轮对话框还可能和某个 tab 同时开着。再让各组件自己轮，就是三份请求叠在一起打
 * 后端 —— 而领星 OpenAPI 是有限流的（约 340ms/次），这种叠加会挤掉真正要用配额的
 * 那些读操作。
 *
 * 所以 status / sellers / datasets 收在这里**只跑一份**，谁要谁用。
 *
 * **provider 只包经营侧**：非管理员看不到那几个 tab，也就永远不该有人替他们去打
 * 这些管理员专属接口（后端会 403，前端不该主动撞上去）。
 */

type Ctx = {
  status: LingXingStatus | null;
  /** 首次加载还没回来（用来区分"没启用"和"还不知道"）。 */
  loading: boolean;
  /** 首次加载失败的原因；空串表示没出错。 */
  error: string;
  datasets: Dataset[];
  sellers: any[];
  storeSid: string;
  setStoreSid: (sid: string) => void;
  /** 重新拉一遍 status + datasets + sellers。 */
  reload: () => Promise<void>;
  /** 打开数据总开关（只读态），成功后自动 reload。 */
  enableMaster: () => Promise<void>;
};

const LingXingCtx = createContext<Ctx | null>(null);

const LS_KEY = "lingxing.ui.v1";
function readLS(): any {
  try { return JSON.parse(localStorage.getItem(LS_KEY) || "{}"); } catch { return {}; }
}

export function useLingXing(): Ctx {
  const ctx = useContext(LingXingCtx);
  if (!ctx) throw new Error("useLingXing 必须在 <LingXingProvider> 里用");
  return ctx;
}

export function LingXingProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<LingXingStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [sellers, setSellers] = useState<any[]>([]);
  const [storeSid, setStoreSidRaw] = useState<string>(() => readLS().storeSid || "");
  // 店铺选择要记住 —— 每次进来都从第一家店重新选，等于每天手动选一遍。
  const setStoreSid = useCallback((sid: string) => {
    setStoreSidRaw(sid);
    try {
      localStorage.setItem(LS_KEY, JSON.stringify({ ...readLS(), storeSid: sid }));
    } catch { /* 隐私模式下写不进去，不影响本次会话 */ }
  }, []);

  // 卸载后别再 setState：切走时正在飞的请求回来会打到已经卸载的组件上。
  //
  // **进来时必须显式设回 true。** StrictMode 下 effect 是 mount → unmount → mount，
  // 只在 cleanup 里置 false 的话，第二次 mount 之后这个标志永远是 false —— 于是每一次
  // setState 都被跳过，面板停在"正在加载领星状态…"再也不动，而且一行报错都没有。
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const loadSellers = useCallback(async () => {
    try {
      const r = await api.post("/lingxing/read/sellers", { params: {} });
      const list = r.data.rows || [];
      if (!alive.current) return;
      setSellers(list);
      // 记住的那家店可能已经不在授权范围里了 —— 回落到第一家，而不是把选择器留空。
      setStoreSidRaw((cur) => {
        if (cur && list.some((s: any) => String(s.sid) === cur)) return cur;
        return list.length ? String(list[0].sid) : cur;
      });
    } catch { /* 总开关没开时这里必然失败，不是错误 */ }
  }, []);

  const reload = useCallback(async () => {
    try {
      const [st, dl] = await Promise.all([
        api.get("/lingxing/status"), api.get("/lingxing/datasets"),
      ]);
      if (!alive.current) return;
      setStatus(st.data);
      setDatasets(dl.data.datasets || []);
      setError("");
      if (st.data.master_enabled) void loadSellers();
    } catch (e: any) {
      // 走 errText 而不是直接读 detail：FastAPI 的 422 里 detail 是**对象数组**，
      // 直接塞进 JSX 会让整页崩成"渲染失败"，而真正的原因一个字都不会露出来。
      // （client/scripts/check-errtext.mjs 会挡住这种写法。）
      if (alive.current) setError(errText(e, "加载失败"));
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [loadSellers]);

  const enableMaster = useCallback(async () => {
    await api.patch("/settings", { settings: { lingxing_enabled: true } });
    await reload();
  }, [reload]);

  useEffect(() => {
    void reload();
    // 只刷 status：徽标和开关状态要新鲜，datasets/sellers 不会自己变。
    const t = setInterval(async () => {
      try {
        const st = await api.get("/lingxing/status");
        if (alive.current) setStatus(st.data);
      } catch { /* 瞬时失败不打扰用户，下一轮会补上 */ }
    }, 10000);
    return () => clearInterval(t);
  }, [reload]);

  return (
    <LingXingCtx.Provider value={{
      status, loading, error, datasets, sellers, storeSid, setStoreSid, reload, enableMaster,
    }}>
      {children}
    </LingXingCtx.Provider>
  );
}
