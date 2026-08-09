/**
 * 门道社区市场 —— 「能力市场」的第一个子页，也是默认打开的那个。
 *
 * 为什么排第一：打开「能力市场」看到的第一屏，应该是"我能拿到什么新东西"，
 * 而不是"我本地已经有什么" —— 后者用户本来就知道。社区是这个产品唯一的外部
 * 供给来源，把它排在最前面，顺带也给门道社区带流量。
 *
 * ``embedded`` 为真时不画自己的大标题（外层 tab 已经有了），只留一行统计与
 * 去社区的入口。
 *
 * 三个刻意的取舍
 * --------------
 * * **B 类（含可执行代码）照常列出，但不给安装按钮。** 之前的做法是干脆过滤掉，
 *   结果是用户以为社区里根本没有代码类技能 —— 而那恰恰是最有价值的一批。现在
 *   列出来并写清楚"为什么现在还装不了"，比假装它不存在诚实。
 * * **中文简介优先。** 技能库里绝大多数 SKILL.md 本来就带 description_zh，
 *   只显示英文等于把已经写好的中文丢掉。
 * * **下载量摆在卡片上。** 在一个陌生目录里，"多少人装过"是普通用户唯一能用的
 *   信号。
 */
import { useCallback, useEffect, useState } from "react";
import {
  marketBrowse, marketDetail, marketInstall, marketPreview, marketStatus,
  marketUninstall, type MarketDetail, type MarketItem, type MarketStatus,
} from "../../api/client";
import { errText } from "../../lib/errText";
import { lockBodyScroll } from "../../lib/scrollLock";

const MENDAO_URL = "https://mendao.ivyea.com";

function Badge({ children, tone = "" }: { children: React.ReactNode; tone?: string }) {
  return <span className={"mk-badge" + (tone ? " mk-badge-" + tone : "")}>{children}</span>;
}

export default function CommunityMarket({ embedded = false }: { embedded?: boolean }) {
  const [status, setStatus] = useState<MarketStatus | null>(null);
  const [items, setItems] = useState<MarketItem[]>([]);
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<MarketDetail | null>(null);
  const [installing, setInstalling] = useState("");

  const load = useCallback(async (query = "") => {
    setBusy(true);
    try {
      const st = await marketStatus();
      setStatus(st);
      if (!st.enabled) return;
      const res = await marketBrowse({ q: query, sort: "hot" });
      setItems(res.items || []);
      setTotal(res.total || 0);
      setErr("");
    } catch (e) {
      setErr(errText(e, "连不上门道社区"));
    } finally { setBusy(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);

  // 弹层开着的时候：锁掉背景滚动（否则滚轮会穿透到底下那一长列卡片），
  // 并让 Esc 能关 —— 一个只能靠点空白关闭的弹层，在键盘上是死的。
  useEffect(() => {
    if (!open) return;
    const unlock = lockBodyScroll();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(null); };
    document.addEventListener("keydown", onKey);
    return () => { unlock(); document.removeEventListener("keydown", onKey); };
  }, [open]);

  const install = async (item: MarketItem) => {
    setInstalling(item.slug);
    setErr("");
    try {
      const version = item.latest || "";
      const pv = await marketPreview(item.slug, version);
      if (!pv.manifest.installable) {
        setErr(`这个技能没通过本地安全检查：${pv.manifest.blockers.join("；")}`);
        return;
      }
      await marketInstall(item.slug, version, pv.confirm_token);
      await load(q);
    } catch (e) {
      setErr(errText(e, "安装失败"));
    } finally { setInstalling(""); }
  };

  if (status && !status.enabled) {
    return (
      <div className={embedded ? "mk-wrap mk-wrap-embed" : "mk-wrap"}>
        <div className="mk-empty">
          <b>门道社区市场还没开启</b>
          <p>
            它会向门道社区发起请求，而 IvyeaOps 的默认立场是<b>数据不出你的机器</b>，
            所以不替你打开。开启后也只在你主动浏览或安装时联网 —— 请求匿名、
            不带机器标识、不回传任何使用统计。
          </p>
          <a className="mk-btn mk-btn-primary" href="/hub-settings">去系统配置开启</a>
        </div>
      </div>
    );
  }

  return (
    <div className={embedded ? "mk-wrap mk-wrap-embed" : "mk-wrap"}>
      <div className="mk-head">
        <div>
          {!embedded && <h2>门道社区市场</h2>}
          <p>
            {total} 个技能 · 别人做好的方法，装过来就能用。
            <a href={MENDAO_URL} target="_blank" rel="noreferrer"> 去门道社区看看 →</a>
          </p>
        </div>
        <input className="mk-search" placeholder="搜索技能…" value={q}
               onChange={(e) => setQ(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") void load(q); }} />
      </div>

      {/* 免责声明。**社区内容没有经过官方审计**，这件事必须写在用户看得见的地方，
          而不是埋在某个协议页里 —— 静态检查挡得住直接调用，挡不住足够绕的写法。
          说清楚边界，比让人以为「上架 = 已审核」安全得多。 */}
      <div className="mk-disclaimer">
        <b>免责声明</b>：这里的技能由社区成员上传，<b>未经官方审计</b>。安装前
        IvyeaOps 会在本地做静态检查（提示词注入、索取凭据、危险模块调用）并把能力清单
        摆给你看，但<b>这不等于安全保证</b>。请自行评估后使用；因使用市场内容造成的
        损失，门道与 IvyeaOps 不承担责任。
      </div>

      {err && <div className="mk-err">{err}</div>}
      {busy && items.length === 0 && <div className="mk-empty">加载中…</div>}

      <div className="mk-grid">
        {items.map((s) => {
          const isB = s.class === "B";
          const installed = status?.installed?.[s.slug];
          return (
            <div className={"mk-card" + (isB ? " mk-card-b" : "")} key={s.slug}>
              <div className="mk-card-top">
                <b>{s.title || s.slug}</b>
                {s.origin === "shared" && <Badge>社区分享</Badge>}
                {isB && <Badge tone="warn">含可执行代码</Badge>}
                {installed && <Badge tone="ok">已安装</Badge>}
              </div>
              {/* 中文优先，没有才退回英文 */}
              <div className="mk-card-desc">{s.summary_zh || s.summary || s.slug}</div>
              <div className="mk-card-meta">
                <span title="有多少人装过 —— 在一个陌生目录里，这是普通用户唯一能用的信号">
                  ↓ {s.install_count ?? 0}
                </span>
                {s.category && <span>{s.category}</span>}
                {s.original_author && <span title="原作者">{s.original_author.slice(0, 26)}</span>}
              </div>
              <div className="mk-card-actions">
                <button className="mk-btn" onClick={async () => {
                  try { setOpen(await marketDetail(s.slug)); }
                  catch (e) { setErr(errText(e, "读不到详情")); }
                }}>看详情</button>
                {isB ? (
                  // **不给安装按钮，并说明原因。** 给一个点了必然失败的按钮，
                  // 比不给更糟。
                  <span className="mk-note"
                        title="沙箱执行做好之前不开放，以免陌生代码直接在你机器上运行">
                    暂不支持安装
                  </span>
                ) : installed ? (
                  <button className="mk-btn" onClick={async () => {
                    await marketUninstall(s.slug); await load(q);
                  }}>卸载</button>
                ) : (
                  <button className="mk-btn mk-btn-primary" disabled={!!installing}
                          onClick={() => install(s)}>
                    {installing === s.slug ? "安装中…" : "安装"}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {open && (
        <div className="mk-modal" role="dialog" aria-modal="true"
             onClick={() => setOpen(null)}>
          <div className="mk-modal-body" onClick={(e) => e.stopPropagation()}>
            {/* 头部钉住不滚：正文可能很长，滚下去之后还要能一眼找到关闭 */}
            <div className="mk-modal-head">
              <b>{open.title || open.slug}</b>
              <button className="mk-btn" onClick={() => setOpen(null)}>关闭</button>
            </div>
            <div className="mk-modal-scroll">
            <div className="mk-modal-meta">
              <span>↓ {open.install_count ?? 0} 次安装</span>
              <span>{open.class === "B" ? "含可执行代码" : "纯提示词"}</span>
              {open.license && <span>{open.license}</span>}
              {open.original_author && <span>作者：{open.original_author}</span>}
              {open.source_url && open.source_url.startsWith("http") && (
                <a href={open.source_url} target="_blank" rel="noreferrer">出处</a>
              )}
            </div>
            {open.summary_zh && <p className="mk-modal-sum">{open.summary_zh}</p>}
            {/* 正文原样显示。**不把可执行脚本铺出来** —— 详情页只回答"这东西是
                干什么的"；把代码贴上去会给人一种"我已经审过了"的错觉。 */}
            <pre className="mk-modal-md">{open.body_md || "（这个技能没有提供说明正文）"}</pre>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
