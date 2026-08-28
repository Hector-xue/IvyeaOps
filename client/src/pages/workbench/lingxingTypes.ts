/**
 * 领星板块的共享类型。
 *
 * 抽出来的理由：数据浏览（`LingXingBrowse`）搬进驾驶舱的齿轮对话框之后，它和原来
 * 的外壳不再是同一个文件的父子关系，而 `Dataset` / `Status` 这两个形状两边都要用。
 * 各写一份迟早会分叉 —— 后端 `/lingxing/datasets` 加一个字段，只有一边跟上。
 */

export type Col = { key: string; label: string };

export type Param = {
  name: string;
  type?: string;
  required?: boolean;
  default?: any;
  label?: string;
};

export type Dataset = {
  key: string;
  label: string;
  group: string;
  params: Param[];
  columns: Col[];
  hint?: string;
};

/**
 * `/api/lingxing/status` 的形状 —— 逐条对着
 * `server/app/services/lingxing_service.py::status()` 抄的，别凭界面上用到的字段
 * 反推：写窄了不会报错在写的时候，而是等下一个组件来取某个字段时才炸。
 */
export type LingXingStatus = {
  key_present: boolean;
  url: string;
  openapi_configured: boolean;
  openapi_host: string;
  master_enabled: boolean;
  /** 配置里的写开关；`operate_active` 才是"此刻真的可写"（还要看没过期）。 */
  operate_enabled: boolean;
  operate_active: boolean;
  operate_expires_at: string;
  /** 写开关自动失效的倒计时（秒）。 */
  operate_remaining_seconds: number;
  require_human: boolean;
  /** 熔断原因；空串表示没熔断。 */
  circuit_reason: string;
  scope_stores: string;
  scope_asins: string;
  max_ops_per_run: number | null;
  max_change_pct: number | null;
  ticket_counts?: { awaiting_human?: number; reviewing?: number; executing?: number };
};
