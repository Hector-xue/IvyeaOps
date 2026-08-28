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

/** `/api/lingxing/status` 的形状。 */
export type LingXingStatus = {
  master_enabled: boolean;
  operate_active: boolean;
  openapi_configured: boolean;
  ticket_counts?: { awaiting_human?: number; reviewing?: number; executing?: number };
};
