import {
  Activity, ArrowUpCircle, BarChart3, Blocks, BookOpen, Bot, CirclePlus, ClipboardCheck,
  Database, FileDiff, FileText, FolderOpen, Globe, HelpCircle, Home, Image as ImageIcon,
  Languages, LayoutGrid, LayoutPanelLeft, LayoutTemplate, ListChecks, LogOut,
  MessageCircleQuestion, Newspaper, Palette, PanelLeftClose, PanelLeftOpen, Plus,
  Settings, Ship, Sparkles, Target, Terminal, TrendingDown, Type, Users,
  Timer, History, Flag, Search, X, Pin,
  ArrowUp, Cpu, Eye, Paperclip, Square, ShieldCheck, Zap,
  type LucideIcon,
} from "lucide-react";

/**
 * 全站图标表。
 *
 * ── 为什么从字符字形换成 SVG ──────────────────────────────────────────────
 * 改造前所有图标都是**字符字形**（◆ ◈ ✓ ⏱ ⊞ ⊕），它们有三个躲不掉的问题：
 *   ① 粗细跟着正文字体走 —— 换个字体，一半图标变细一半变粗；
 *   ② 基线是文字基线，和旁边的中文标签对不齐，永远差那么一两个像素；
 *   ③ 它们是**字体里的字**，字体里没有就是豆腐块。⏱ ☑ ⚑ 这几个在 Windows
 *      的默认中文字体里就是空心方框 —— 用户截图里那几个 □ 就是这么来的。
 * lucide 是 24×24 网格上统一 2px 描边的 SVG，三个问题一次全没了。
 * 它本来就在依赖里（agents 子树在用），换过来不新增任何包。
 *
 * ── 为什么是"名字 → 组件"的一张表，而不是各处直接 import ─────────────────
 * 板块注册表（lib/navRegistry）是纯数据，不该 import React 组件；而且图标名要能
 * 存进配置、传过 props、出现在 JSON 里。所以注册表里存**名字**，这里做唯一的
 * 名字→组件映射。认不出的名字回落到一个中性图标，绝不崩、也绝不留空白。
 */
const ICONS: Record<string, LucideIcon> = {
  // ── 一级项 ──
  // 「新建任务」是侧栏唯一的主动作，用⊕（"加一个新的"）比✎（"编辑"）更直白 ——
  // 和参考实现（DeepSeek Harness 的「新会话」）一致。
  "new-task": CirclePlus,
  console: MessageCircleQuestion,
  capability: Blocks,
  approval: ClipboardCheck,
  schedule: Timer,
  // ── 工具板块 ──
  home: Home,
  market: Globe,
  playbook: Target,
  listing: LayoutTemplate,
  translate: Languages,
  analysis: BarChart3,
  lingxing: Database,
  skill: Sparkles,
  assistant: MessageCircleQuestion,
  imagegen: ImageIcon,
  brain: BookOpen,
  agents: Bot,
  terminal: Terminal,
  monitor: Activity,
  freight: Ship,
  users: Users,
  settings: Settings,
  news: Newspaper,
  // ── 外壳控件 ──
  "all-tools": LayoutGrid,
  "panel-close": PanelLeftClose,
  "panel-open": PanelLeftOpen,
  plus: Plus,
  search: Search,
  close: X,
  pin: Pin,
  // ── 账户菜单 ──
  theme: Palette,
  font: Type,
  layout: LayoutPanelLeft,
  manual: BookOpen,
  help: HelpCircle,
  version: ArrowUpCircle,
  logout: LogOut,
  // ── 右侧产物栏 ──
  report: FileText,
  file: FolderOpen,
  diff: FileDiff,
  todo: ListChecks,
  flag: Flag,
  history: History,
  // ── 任务台输入器 ──
  attach: Paperclip,
  send: ArrowUp,
  stop: Square,
  model: Cpu,
  // 审批三档，一档一个形状：看 / 盾 / 闪电。颜色会被主题改，形状不会。
  "mode-readonly": Eye,
  "mode-ask": ShieldCheck,
  "mode-full": Zap,
  // ── 场景 ──
  "ad-waste": TrendingDown,
};

/** 认不出的名字用它。留空白比画错更难排查。 */
const FALLBACK = Sparkles;

export type IconProps = {
  name: string;
  size?: number;
  /** 描边粗细。默认 1.75 —— lucide 默认 2 在 14~16px 尺寸下偏重，压不住中文标签。 */
  strokeWidth?: number;
  className?: string;
};

export default function Icon({ name, size = 16, strokeWidth = 1.75, className }: IconProps) {
  const Cmp = ICONS[name] || FALLBACK;
  return <Cmp size={size} strokeWidth={strokeWidth} className={className} aria-hidden />;
}

/** 名字是否登记过 —— 迁移期用来找漏网的字符字形。 */
export function hasIcon(name: string): boolean {
  return name in ICONS;
}
