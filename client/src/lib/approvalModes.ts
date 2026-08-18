/**
 * 审批三档 —— 全站唯一的定义处。
 *
 *   只读     只分析、只给方案，一个字都不往外写
 *   审批放行 可以写，但每一次写入都停下来弹确认卡，你点了才落
 *   完全放行 这一轮你已经一次性授权了，写操作不再逐条问你
 *
 * 界面档位（readonly/ask/full）和**线上语义**（none/remote/auto）是两套词，
 * 别混：存进预设、发给 agent 的一律是线上语义，界面上显示的一律是档位。
 * 这两套词此前散在 Composer、任务台、能力市场三处各写一遍，改一处漏两处 ——
 * 所以收到这里，谁要用谁来取。
 *
 * 对应的 agent 契约（ivyea_agent/service.py::_approval_mode）：
 *   none   → plan_mode=true  ，execute=false
 *   remote → plan_mode=false ，execute=true ，写前弹网页确认卡
 *   auto   → plan_mode=false ，execute=true ，perm.accept_edits=true（不弹卡）
 */

/** 界面上的三个档位。 */
export type ApprovalMode = "readonly" | "ask" | "full";

/** 发给 agent / 存进预设的值。 */
export type ApprovalWire = "none" | "remote" | "auto";

export type ApprovalModeInfo = {
  value: ApprovalMode;
  wire: ApprovalWire;
  label: string;
  hint: string;
  /** 越往后越危险，用来决定芯片的配色（普通 / 提醒 / 警示）。 */
  tone: "calm" | "warn" | "danger";
};

export const APPROVAL_MODES: ApprovalModeInfo[] = [
  {
    value: "readonly", wire: "none", tone: "calm",
    label: "只读",
    hint: "只分析、只给方案，绝不改动任何数据",
  },
  {
    value: "ask", wire: "remote", tone: "warn",
    label: "审批放行",
    hint: "需要写入时停下来问你，确认一条执行一条",
  },
  {
    value: "full", wire: "auto", tone: "danger",
    label: "完全放行",
    hint: "本轮写操作不再逐条确认，Agent 直接执行",
  },
];

export const APPROVAL_INFO: Record<ApprovalMode, ApprovalModeInfo> =
  APPROVAL_MODES.reduce((acc, m) => { acc[m.value] = m; return acc; },
    {} as Record<ApprovalMode, ApprovalModeInfo>);

/**
 * 档位 → chat payload。
 *
 * plan_mode 和 approval **必须成对**：agent 那边 execute = approval 放开 && !plan_mode，
 * 只改一个的话开关看着变了、行为一点没变。
 */
export function approvalPayload(mode: ApprovalMode): { plan_mode: boolean; approval: ApprovalWire } {
  const info = APPROVAL_INFO[mode] || APPROVAL_INFO.readonly;
  return { plan_mode: info.wire === "none", approval: info.wire };
}

/** 线上语义 → 界面档位。认不出来一律按只读，判错的方向必须是"少做"。 */
export function approvalFromWire(wire?: string): ApprovalMode {
  return APPROVAL_MODES.find((m) => m.wire === wire)?.value || "readonly";
}

/** 给人看的名字。历史数据里可能存着 none/remote，所以两套词都认。 */
export function approvalLabel(mode?: string): string {
  const hit = APPROVAL_MODES.find((m) => m.value === mode || m.wire === mode);
  return hit ? hit.label : APPROVAL_INFO.readonly.label;
}
