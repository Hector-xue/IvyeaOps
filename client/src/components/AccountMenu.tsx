import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import CostChip from "./CostChip";
import { THEMES, themeLabel } from "../lib/themes";

/**
 * 侧栏左下角的账户区 + 向上弹出的设置菜单。
 *
 * ── 它替掉了什么 ─────────────────────────────────────────────────────────
 * 改造前顶栏常驻 8 个带框按钮（用量 / 时钟 / 外壳切换 / 手册 / 引导 / 刷新 /
 * 主题 / 退出），左下角另有「小绿点 + 版本号 + ↻检查」一条。加起来 10 个控件
 * 天天挂在眼前，而其中 8 个的实际使用频率是"一个月一次"。它们全部收进这里。
 *
 * ── 版本号和「检查更新」为什么落在这儿 ───────────────────────────────────
 * 这两样东西的性质是**不常看，但必须能被发现**。放顶栏当按钮 = 每天付出注意力
 * 成本换一年一次的收益；纯藏进二级菜单 = 用户永远不知道有新版本。
 * 所以拆成两半：
 *   · 版本号常驻在账户行的**副标题**里（顺眼扫到，不占独立控件位）；
 *   · 有新版本时头像挂**红点**，菜单里那一行也变红 —— 红点是"有事发生"的
 *     最低成本表达，用户不需要认识它就知道该点进去看看。
 * 点进去走的还是原来那条 startUpdate()，更新逻辑一行没改。
 *
 * ── 收起态 ───────────────────────────────────────────────────────────────
 * 侧栏收成 52px 时只剩头像和红点，菜单照常能开 —— 收起侧栏的人恰恰是最不想
 * 看见文字的人，但他一样要能换主题、要能看到有更新。
 */

export type AccountMenuProps = {
  collapsed: boolean;
  username: string;
  isAdmin: boolean;
  /** 当前主题 id + 切换回调（整块主题选择器从顶栏搬进这里） */
  theme: string;
  onSelectTheme: (id: string) => void;
  /** 版本与更新 —— 全部由 MainLayout 拥有，这里只负责呈现和转发 */
  versionLabel: string;
  hasUpdate: boolean;
  updateTitle: string;
  updating: boolean;
  onUpdate: () => void;
  /** 外壳布局 */
  isConsoleShell: boolean;
  onToggleShell: () => void;
  /** 使用手册；本页引导（当前板块没有引导时传 null，菜单里就不出现这一项） */
  onManual: () => void;
  onTour: (() => void) | null;
  onLogout: () => void;
  /** 移动端点任意菜单项后要把抽屉收掉 */
  onNavigated?: () => void;
};

/** 头像里的字母：中文取首字，英文取前两位。 */
function initials(name: string): string {
  const n = (name || "?").trim();
  if (!n) return "?";
  return /[一-龥]/.test(n[0]) ? n[0] : n.slice(0, 2).toUpperCase();
}

export default function AccountMenu(props: AccountMenuProps) {
  const {
    collapsed, username, isAdmin, theme, onSelectTheme,
    versionLabel, hasUpdate, updateTitle, updating, onUpdate,
    isConsoleShell, onToggleShell, onManual, onTour, onLogout, onNavigated,
  } = props;

  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [themesOpen, setThemesOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  // 点外面收起 + Esc 收起。和顶栏原来那个 themePicker 用的是同一种写法。
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // 菜单收起时把二级面板也收掉，免得下次打开还停在主题列表上。
  useEffect(() => { if (!open) setThemesOpen(false); }, [open]);

  const run = (fn: () => void) => () => {
    setOpen(false);
    onNavigated?.();
    fn();
  };

  return (
    <div className="sb-acct-wrap" ref={wrapRef}>
      {open && (
        <div className="sb-menu" role="menu">
          {themesOpen ? (
            <>
              <button className="sb-menu-item sb-menu-back" onClick={() => setThemesOpen(false)}>
                <i className="sb-menu-ic">‹</i><span>主题</span>
              </button>
              <div className="sb-menu-sep" />
              <div className="sb-menu-themes scroll-thin">
                {THEMES.map((t) => (
                  <button
                    key={t.id}
                    className={"sb-menu-item" + (t.id === theme ? " active" : "")}
                    onClick={() => { onSelectTheme(t.id); setThemesOpen(false); }}
                  >
                    <span className="theme-picker-dot" style={{ background: t.accent }} />
                    <span className="theme-picker-icon">{t.icon}</span>
                    <span className="sb-menu-label">{t.name}</span>
                    {t.id === theme && <span className="sb-menu-tail">✓</span>}
                  </button>
                ))}
              </div>
            </>
          ) : (
            <>
              <button className="sb-menu-item" onClick={() => setThemesOpen(true)}>
                <i className="sb-menu-ic">◐</i>
                <span className="sb-menu-label">主题</span>
                <span className="sb-menu-tail">{themeLabel(theme)} ›</span>
              </button>
              <button className="sb-menu-item" onClick={run(() => navigate("/hub-settings"))}>
                <i className="sb-menu-ic">Aa</i>
                <span className="sb-menu-label">字体与字号</span>
              </button>
              <div className="sb-menu-sep" />
              <button className="sb-menu-item" onClick={run(onToggleShell)}>
                <i className="sb-menu-ic">▤</i>
                <span className="sb-menu-label">布局</span>
                <span className="sb-menu-tail">{isConsoleShell ? "任务台" : "经典"} ⇄</span>
              </button>
              {isAdmin && (
                <button className="sb-menu-item" onClick={run(() => navigate("/hub-settings"))}>
                  <i className="sb-menu-ic">⚙</i>
                  <span className="sb-menu-label">系统配置</span>
                </button>
              )}
              <button className="sb-menu-item" onClick={run(onManual)}>
                <i className="sb-menu-ic">📖</i>
                <span className="sb-menu-label">使用手册</span>
              </button>
              {onTour && (
                <button className="sb-menu-item" onClick={run(onTour)}>
                  <i className="sb-menu-ic">?</i>
                  <span className="sb-menu-label">本页引导</span>
                </button>
              )}
              <button
                className="sb-menu-item"
                onClick={run(() => {
                  const app = (window as unknown as { OpsApp?: { reload?: () => void } }).OpsApp;
                  if (app?.reload) app.reload(); else window.location.reload();
                })}
              >
                <i className="sb-menu-ic">↻</i>
                <span className="sb-menu-label">刷新界面</span>
              </button>
              <div className="sb-menu-sep" />
              {/* 用量：完整的那张芯片放在这里，账户行副标题只放一个数。 */}
              {isAdmin && (
                <div className="sb-menu-cost">
                  <span className="sb-menu-label">本月 AI 用量</span>
                  <CostChip />
                </div>
              )}
              {/* 版本与更新。非管理员没有更新权限，只看版本号。 */}
              <button
                className={"sb-menu-item" + (hasUpdate ? " has-update" : "")}
                onClick={isAdmin ? run(onUpdate) : undefined}
                disabled={!isAdmin || updating}
                title={updateTitle}
              >
                <i className="sb-menu-ic">{hasUpdate ? "↑" : "◇"}</i>
                <span className="sb-menu-label">版本 {versionLabel}</span>
                <span className="sb-menu-tail">
                  {updating ? "更新中…" : hasUpdate ? "有新版本 →" : isAdmin ? "检查更新" : ""}
                </span>
              </button>
              <div className="sb-menu-sep" />
              <button className="sb-menu-item" onClick={run(onLogout)}>
                <i className="sb-menu-ic">↩</i>
                <span className="sb-menu-label">退出登录</span>
              </button>
            </>
          )}
        </div>
      )}

      <button
        className={"sb-acct" + (open ? " open" : "")}
        onClick={() => setOpen((v) => !v)}
        title={collapsed ? `${username || "账户"} · ${versionLabel}` : "设置"}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <span className="sb-acct-avatar">
          {initials(username)}
          {hasUpdate && <span className="sb-acct-dot" aria-label="发现新版本" />}
        </span>
        <span className="sb-acct-text">
          <span className="sb-acct-name">{username || "账户"}</span>
          <span className="sb-acct-sub">
            {versionLabel}
            {isAdmin && <> · <CostChip variant="inline" /></>}
          </span>
        </span>
        <i className="sb-acct-gear">⚙</i>
      </button>
    </div>
  );
}
