/**
 * 板块注册表 —— 全站唯一的板块真相源。
 *
 * 改造前这些信息散在四张互相平行、必须手工保持同步的表里：
 *   - MainLayout 的 `NAV`              侧边栏分组与 RBAC
 *   - MainLayout 的 `PATH_LABEL`       顶栏面包屑
 *   - MainLayout 的 `KEEP_ALIVE_BOARDS` 常驻挂载的长任务板块
 *   - lib/tours 的 `TOURS`             各板块引导（仍留在原处，这里只判断有无）
 *
 * 现在一个板块 = 一条 `BoardEntry`。两套外壳（新的 Agent 优先三栏 / 旧的分组
 * 侧边栏）都从这张表派生，所以「回退到旧壳」永远和新壳看到同一批板块、同一套
 * 权限语义 —— 不会出现一边能进、另一边进不去的偏差。
 */

/** 侧边栏分区。console/capability/automation 是新壳的一级项；tools/admin 收进「更多工具」。 */
export type NavGroup = "console" | "capability" | "automation" | "tools" | "admin";

/** 旧壳的分组标题 —— 回退时按它还原成改造前那四段。 */
export type LegacySection = "工具" | "AI & 系统" | "小工具" | "管理";

/** 任务台首页的场景芯片：点一下把 prompt 填进输入框。 */
export type SceneChip = {
  icon: string;
  label: string;
  prompt: string;
};

export type BoardEntry = {
  /** 链接目标，可带 query（如 /brain?tab=governance）。 */
  to: string;
  /** 用于路由匹配和面包屑的纯 pathname；省略时取 `to` 的 ? 之前部分。 */
  path?: string;
  /**
   * 图标名，对应 components/Icon.tsx 里那张表（不是字符字形）。
   * 这里存名字而不是组件：注册表是纯数据，不该 import React。
   */
  icon: string;
  label: string;
  group: NavGroup;
  /** 顶栏面包屑文案（如 "~/首页"）；省略则显示 "~/"。 */
  pathLabel?: string;
  /** true = 仅管理员可见（非管理员需在 permissions 里拿到 `key`）。语义与改造前的 canSee 完全一致。 */
  admin?: boolean;
  /** 可授权的模块 key，对应后端 MODULE_CATALOG。 */
  key?: string;
  /** 长任务板块：首次访问后常驻挂载、切走仅隐藏，保住进行中的轮询/流式任务。 */
  keepAlive?: boolean;
  /** 终端 / 外部智能体：常驻挂载以保住 WebSocket 与会话状态。 */
  persistent?: boolean;
  /** 需要整页布局（.content-fullpage）。 */
  fullPage?: boolean;
  /** false = 该板块属于后续阶段，尚未建成，两套外壳都不渲染。 */
  ready?: boolean;
  /** 旧壳分组；缺省表示旧壳里本来就没有这一项（新增的一级项）。 */
  legacySection?: LegacySection;
  /** 出现在任务台 hero 的场景芯片。 */
  scene?: SceneChip;
};

/**
 * 全部板块。**tools/admin 两组的相对顺序与改造前的 NAV 逐条一致** —— 回退旧壳时
 * 侧边栏必须和改造前长得一模一样。
 */
export const BOARDS: BoardEntry[] = [
  // ── 新壳一级项 ────────────────────────────────────────────────────────────
  {
    to: "/console", icon: "console", label: "任务台", group: "console",
    pathLabel: "~/任务台", fullPage: true, ready: true,
  },
  {
    to: "/capabilities", icon: "capability", label: "能力市场", group: "capability",
    pathLabel: "~/能力市场", ready: true,
  },
  {
    to: "/approvals", icon: "approval", label: "待审批", group: "automation",
    pathLabel: "~/待审批", admin: false, key: "agents", ready: true,
  },
  {
    to: "/schedules", icon: "schedule", label: "定时任务", group: "automation",
    pathLabel: "~/定时任务", admin: false, key: "agents", ready: true,
  },

  // ── 工具（旧壳「工具」段，顺序不变）───────────────────────────────────────
  // 运营驾驶舱。改造前它挂在 "/"；现在 "/" 变成按外壳模式分流的落地跳转，
  // 驾驶舱有了自己的固定地址，两套外壳都能稳定链到它。
  {
    to: "/dashboard", icon: "home", label: "首页", group: "tools", legacySection: "工具",
    pathLabel: "~/首页", ready: true,
  },
  {
    to: "/market", icon: "market", label: "市场调研", group: "tools", legacySection: "工具",
    pathLabel: "~/市场调研", keepAlive: true, ready: true,
    scene: {
      icon: "market", label: "市场调研",
      prompt: "帮我对「」这个关键词做一份美国站市场调研报告，用市场调研板块的真实数据。",
    },
  },
  {
    to: "/playbook", icon: "playbook", label: "打法推荐", group: "tools", legacySection: "工具",
    pathLabel: "~/打法推荐", keepAlive: true, ready: true,
    scene: {
      icon: "playbook", label: "打法推荐",
      prompt: "帮我给「」这个产品出一份美国站的站内打法方案，售价按 USD 计。",
    },
  },
  {
    to: "/listing", icon: "listing", label: "Listing工作台", group: "tools", legacySection: "工具",
    pathLabel: "~/Listing工作台", admin: true, key: "listing", ready: true,
    scene: {
      icon: "listing", label: "Listing 诊断",
      prompt: "ASIN  这条 Listing 为什么不转化？帮我拉真实数据做质量诊断。",
    },
  },
  {
    to: "/image-translate", icon: "translate", label: "一键图片翻译", group: "tools", legacySection: "工具",
    pathLabel: "~/一键图片翻译", admin: true, key: "image-translate", ready: true,
  },
  {
    to: "/tools", icon: "analysis", label: "分析工具", group: "tools", legacySection: "工具",
    pathLabel: "~/分析工具", admin: true, key: "tools", keepAlive: true, ready: true,
    scene: {
      icon: "analysis", label: "关键词竞争",
      prompt: "帮我分析「」这个关键词在美国站的竞争格局，并给出切入建议。",
    },
  },
  {
    to: "/lingxing", icon: "lingxing", label: "领星 ERP", group: "tools", legacySection: "工具",
    pathLabel: "~/领星ERP", admin: true, ready: true,
    scene: {
      icon: "ad-waste", label: "广告浪费诊断",
      prompt: "拉一下最近 7 天的领星广告大盘，找出高花费零转化的低效项并给出可执行的调整方案。",
    },
  },
  {
    to: "/skill-hub", icon: "skill", label: "Skill 中心", group: "tools", legacySection: "工具",
    pathLabel: "~/Skill中心", admin: true, key: "skill-hub", ready: true,
  },

  // ── AI & 系统（旧壳第二段）────────────────────────────────────────────────
  // AI 问答 / AI 生图不再是独立板块 —— 问答就是任务台不带工具的那一档，作图由
  // 任务台的 image_generate 工具调同一条链路（两条路由重定向到 /console）。
  {
    to: "/brain?tab=governance", path: "/brain", icon: "brain", label: "知识库工作台",
    group: "tools", legacySection: "AI & 系统",
    pathLabel: "~/知识库工作台", admin: true, key: "brain", ready: true,
  },
  {
    to: "/agents", icon: "agents", label: "外部智能体", group: "tools", legacySection: "AI & 系统",
    pathLabel: "~/外部智能体", admin: true, key: "agents",
    persistent: true, fullPage: true, ready: true,
  },
  {
    to: "/terminal", icon: "terminal", label: "服务器终端", group: "tools", legacySection: "AI & 系统",
    pathLabel: "~/服务器终端", admin: true, key: "terminal", persistent: true, ready: true,
  },
  {
    to: "/servmon", icon: "monitor", label: "服务器监控", group: "tools", legacySection: "AI & 系统",
    pathLabel: "~/服务器监控", admin: true, key: "servmon", ready: true,
  },

  // ── 小工具（旧壳第三段）───────────────────────────────────────────────────
  {
    to: "/freight", icon: "freight", label: "头程比价", group: "tools", legacySection: "小工具",
    pathLabel: "~/头程比价", ready: true,
  },

  // ── 管理（旧壳第四段）─────────────────────────────────────────────────────
  {
    to: "/users", icon: "users", label: "用户管理", group: "admin", legacySection: "管理",
    pathLabel: "~/用户管理", admin: true, ready: true,
  },
  {
    to: "/hub-settings", icon: "settings", label: "系统配置", group: "admin", legacySection: "管理",
    pathLabel: "~/系统配置", admin: true, ready: true,
  },
  {
    to: "/news", icon: "news", label: "资讯", group: "admin", legacySection: "管理",
    pathLabel: "~/资讯", admin: true, key: "news", ready: true,
  },
];

/** `to` 去掉 query 后的 pathname。 */
export function boardPath(b: BoardEntry): string {
  return b.path ?? b.to.split("?")[0];
}

/**
 * 有路由但不在侧边栏里的板块（从别处跳进去）。只为面包屑存在。
 * 改造前这些也只在 PATH_LABEL 里出现，行为一致。
 */
const EXTRA_PATH_LABELS: Record<string, string> = {
  "/idea-skill": "~/想法工坊",
  "/skill-tools": "~/运营商店",
  "/skill": "~/SkillStudio",
  "/deep-analysis": "~/深入分析",
};

const PATH_LABELS: Record<string, string> = (() => {
  const map: Record<string, string> = { ...EXTRA_PATH_LABELS };
  for (const b of BOARDS) {
    if (b.pathLabel) map[boardPath(b)] = b.pathLabel;
  }
  return map;
})();

/**
 * 顶栏面包屑。精确匹配优先；没登记过就按最长前缀回退到父板块 ——
 * 之前 /skill/browse 这类子路由一律显示成 "~/"，看着像走丢了。
 */
export function pathLabel(pathname: string): string {
  const exact = PATH_LABELS[pathname];
  if (exact) return exact;
  let best = "";
  for (const key of Object.keys(PATH_LABELS)) {
    if (key !== "/" && pathname.startsWith(key + "/") && key.length > best.length) best = key;
  }
  return best ? PATH_LABELS[best] : "~/";
}

const READY = BOARDS.filter((b) => b.ready !== false);

/** 长任务板块：首次访问后常驻挂载、切走仅隐藏。 */
export const KEEP_ALIVE_PATHS: string[] = READY.filter((b) => b.keepAlive).map(boardPath);

/** 常驻挂载以保住 WebSocket / 会话状态的板块（终端、外部智能体）。 */
export const PERSISTENT_PATHS: string[] = READY.filter((b) => b.persistent).map(boardPath);

const FULL_PAGE = new Set(READY.filter((b) => b.fullPage).map(boardPath));

/** 该路径是否需要整页布局（.content-fullpage）。 */
export function isFullPage(pathname: string): boolean {
  return FULL_PAGE.has(pathname);
}

/** 可见性判定 —— 语义与改造前 MainLayout 的 canSee 逐字一致。 */
export type Visibility = { isAdmin: boolean; permissions: string[] };

export function canSee(b: BoardEntry, v: Visibility): boolean {
  return v.isAdmin || !b.admin || (!!b.key && v.permissions.includes(b.key));
}

export type NavSection = { title: string; items: BoardEntry[] };

/** 旧壳侧边栏：改造前那四段，顺序与内容不变。 */
export function classicSections(v: Visibility): NavSection[] {
  const order: LegacySection[] = ["工具", "AI & 系统", "小工具", "管理"];
  return order
    .map((title) => ({
      title,
      items: READY.filter((b) => b.legacySection === title && canSee(b, v)),
    }))
    .filter((s) => s.items.length > 0);
}

/** 新壳侧边栏的一级项（任务台 / 能力市场 / 定时任务）。 */
export function primaryItems(v: Visibility): BoardEntry[] {
  const groups: NavGroup[] = ["console", "capability", "automation"];
  return READY.filter((b) => groups.includes(b.group) && canSee(b, v));
}

/**
 * 新壳「更多工具」折叠组 —— 收纳全部现有板块。内部仍按旧壳的四段分小节，
 * 老用户按原来的记忆就能找到东西。
 */
export function toolSections(v: Visibility): NavSection[] {
  const order: LegacySection[] = ["工具", "AI & 系统", "小工具", "管理"];
  return order
    .map((title) => ({
      title,
      items: READY.filter(
        (b) => (b.group === "tools" || b.group === "admin") && b.legacySection === title && canSee(b, v),
      ),
    }))
    .filter((s) => s.items.length > 0);
}

/** 任务台 hero 的场景芯片。 */
export function sceneChips(v: Visibility): (SceneChip & { to: string })[] {
  return READY.filter((b) => b.scene && canSee(b, v)).map((b) => ({ ...b.scene!, to: b.to }));
}

/** 「新建任务」按钮广播的事件名 —— 任务台监听它来开一轮新会话。 */
export const CONSOLE_NEW_EVENT = "ivyea-ops:console-new";

// ---------------------------------------------------------------------------
// 外壳模式
//
// "console" = Agent 优先的新外壳；"classic" = 改造前的分组侧边栏。两者渲染的板块
// 完全一致（都从上面这张表派生），只是组织方式不同 —— 所以回退永远不会让某个
// 板块消失，也不会改变谁能看见它。存在本地，出问题一个开关就能退回去，不用发版。
// ---------------------------------------------------------------------------
export type ShellMode = "console" | "classic";
export const SHELL_KEY = "ivyea-ops.shell";

export function readShellMode(): ShellMode {
  try {
    return localStorage.getItem(SHELL_KEY) === "classic" ? "classic" : "console";
  } catch {
    return "console";
  }
}

export function writeShellMode(mode: ShellMode): void {
  try { localStorage.setItem(SHELL_KEY, mode); } catch { /* ignore */ }
}

/**
 * 登录后的落地页。新外壳落到任务台；退回经典外壳的人还是落到运营驾驶舱 ——
 * 和改造前看到的第一屏一模一样。
 */
export function landingPath(): string {
  return readShellMode() === "classic" ? "/dashboard" : "/console";
}
