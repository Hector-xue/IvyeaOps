/**
 * 全局图片灯箱 —— 点开看原图。
 *
 * 为什么不写成 React 组件：这东西要同时服务两个**互相隔离**的渲染树 ——
 * workbench(#root，纯 workbench.css)和 /agents(#ccui-root，作用域化的
 * Tailwind)。写成组件就得在两个 root 各挂一份 host，共享模块级 state 时两份会
 * 同时弹出来；样式也得写两套(一套 CSS 变量、一套 Tailwind 类)。
 *
 * 直接操作 DOM 反而最省事：谁都能 `openLightbox(...)`，样式自带、层级自定，
 * 不关心调用方在哪棵树里。
 */

export interface LightboxItem {
  src: string;
  alt?: string;
}

const STYLE_ID = "ivl-style";
const Z = 10060;               // 高于 workbench 现有最高层(10051)和 agents 的 z-[9999]

/** 样式注一次就够。放 head 里，不依赖任何 CSS 文件的加载顺序。 */
function ensureStyle(): void {
  if (document.getElementById(STYLE_ID)) return;
  const el = document.createElement("style");
  el.id = STYLE_ID;
  el.textContent = `
.ivl-mask{position:fixed;inset:0;z-index:${Z};display:flex;align-items:center;justify-content:center;
  background:rgba(12,14,18,.92);backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px);
  opacity:0;transition:opacity .16s ease;touch-action:none;overscroll-behavior:contain}
.ivl-mask.ivl-in{opacity:1}
.ivl-img{max-width:92vw;max-height:88vh;object-fit:contain;border-radius:6px;
  box-shadow:0 24px 80px rgba(0,0,0,.55);cursor:zoom-in;user-select:none;-webkit-user-drag:none;
  transform-origin:center center;transition:transform .12s ease-out;will-change:transform}
.ivl-img.ivl-zoomed{cursor:grab}
.ivl-img.ivl-drag{cursor:grabbing;transition:none}
.ivl-bar{position:fixed;top:12px;right:12px;display:flex;align-items:center;gap:6px;z-index:${Z + 1}}
.ivl-btn{display:inline-flex;align-items:center;justify-content:center;min-width:34px;height:34px;
  padding:0 9px;border:1px solid rgba(255,255,255,.16);border-radius:9px;background:rgba(255,255,255,.09);
  color:#fff;font-size:13px;line-height:1;cursor:pointer;transition:background .12s ease,border-color .12s ease}
.ivl-btn:hover{background:rgba(255,255,255,.2);border-color:rgba(255,255,255,.32)}
.ivl-btn:focus-visible{outline:2px solid #7aa2ff;outline-offset:2px}
.ivl-nav{position:fixed;top:50%;transform:translateY(-50%);width:44px;height:44px;font-size:20px;z-index:${Z + 1}}
.ivl-prev{left:14px}
.ivl-next{right:14px}
.ivl-meta{position:fixed;left:0;right:0;bottom:14px;display:flex;justify-content:center;pointer-events:none;z-index:${Z + 1}}
.ivl-meta span{max-width:82vw;padding:5px 12px;border-radius:999px;background:rgba(0,0,0,.5);
  color:rgba(255,255,255,.86);font-size:12px;line-height:1.5;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
@media (max-width:640px){
  .ivl-nav{width:38px;height:38px}
  .ivl-img{max-width:96vw;max-height:80vh}
}
@media (prefers-reduced-motion:reduce){
  .ivl-mask,.ivl-img{transition:none}
}`;
  document.head.appendChild(el);
}

/** data: / blob: 都不能直接 window.open(现代浏览器拦顶层 data: 导航)，统一转 blob。 */
async function openInNewTab(src: string): Promise<void> {
  if (/^https?:/i.test(src)) {
    window.open(src, "_blank", "noopener");
    return;
  }
  try {
    const blob = await (await fetch(src)).blob();
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank", "noopener");
    // 新标签把 blob 读完之前不能撤销；给足时间，撤不掉也只是留一份内存引用。
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch {
    window.open(src, "_blank", "noopener");
  }
}

/**
 * 下载原图。跨域的 http(s) 图先取成 blob 再存 —— `<a download>` 对跨域资源会被
 * 浏览器忽略掉 download 属性，直接变成"在当前标签打开这张图"，用户以为点坏了。
 * 取不到（对方没开 CORS）就退回新标签，让用户自己另存。
 */
async function download(item: LightboxItem, idx: number): Promise<void> {
  const name = fileNameFor(item, idx);
  const save = (href: string, revoke?: string) => {
    const a = document.createElement("a");
    a.href = href;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    if (revoke) setTimeout(() => URL.revokeObjectURL(revoke), 10_000);
  };
  if (!/^https?:/i.test(item.src)) {      // data: / blob: / 同源路径，直接存
    save(item.src);
    return;
  }
  try {
    const blob = await (await fetch(item.src, { referrerPolicy: "no-referrer" })).blob();
    const url = URL.createObjectURL(blob);
    save(url, url);
  } catch {
    window.open(item.src, "_blank", "noopener");
  }
}

function fileNameFor(item: LightboxItem, idx: number): string {
  const fromAlt = (item.alt || "").trim().replace(/[\\/:*?"<>|]+/g, "_").slice(0, 60);
  const m = item.src.match(/\/([^/?#]+\.(?:png|jpe?g|gif|webp|svg|bmp|avif))(?:[?#]|$)/i);
  if (m) return m[1];
  const ext = (item.src.match(/^data:image\/([a-z0-9+.-]+)/i)?.[1] || "png").replace("jpeg", "jpg");
  return `${fromAlt || `image-${idx + 1}`}.${ext}`;
}

let closeCurrent: (() => void) | null = null;

/** 打开灯箱。传一组图时可以左右切换；`index` 是初始那张。 */
export function openLightbox(items: LightboxItem[] | LightboxItem | string, index = 0): void {
  const list: LightboxItem[] = (
    typeof items === "string" ? [{ src: items }] : Array.isArray(items) ? items : [items]
  ).filter((it) => it && it.src);
  if (!list.length) return;

  closeCurrent?.();          // 同时只留一个灯箱
  ensureStyle();

  let cur = Math.min(Math.max(index, 0), list.length - 1);
  let scale = 1;
  let tx = 0;
  let ty = 0;

  const mask = document.createElement("div");
  mask.className = "ivl-mask";
  mask.setAttribute("role", "dialog");
  mask.setAttribute("aria-modal", "true");
  mask.setAttribute("aria-label", "查看原图");

  const img = document.createElement("img");
  img.className = "ivl-img";
  img.draggable = false;
  // 和正文里的图同一个理由：第三方图床看见 Referer 会 403，原图就打不开了。
  img.referrerPolicy = "no-referrer";

  const bar = document.createElement("div");
  bar.className = "ivl-bar";
  const mkBtn = (label: string, title: string, onClick: () => void, cls = "ivl-btn") => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = cls;
    b.textContent = label;
    b.title = title;
    b.setAttribute("aria-label", title);
    b.addEventListener("click", (e) => { e.stopPropagation(); onClick(); });
    return b;
  };

  const meta = document.createElement("div");
  meta.className = "ivl-meta";
  const metaText = document.createElement("span");
  meta.appendChild(metaText);

  function applyTransform(): void {
    img.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
    img.classList.toggle("ivl-zoomed", scale > 1);
  }

  function resetZoom(): void {
    scale = 1; tx = 0; ty = 0;
    applyTransform();
  }

  function zoomTo(next: number): void {
    scale = Math.min(Math.max(next, 1), 8);
    if (scale === 1) { tx = 0; ty = 0; }
    applyTransform();
  }

  function show(i: number): void {
    cur = (i + list.length) % list.length;
    const it = list[cur];
    img.src = it.src;
    img.alt = it.alt || "原图";
    resetZoom();
    const caption = (it.alt || "").trim();
    const counter = list.length > 1 ? `${cur + 1} / ${list.length}` : "";
    metaText.textContent = [caption, counter].filter(Boolean).join("　·　");
    meta.style.display = metaText.textContent ? "flex" : "none";
  }

  function close(): void {
    if (closeCurrent !== close) return;
    closeCurrent = null;
    mask.classList.remove("ivl-in");
    document.removeEventListener("keydown", onKey, true);
    document.body.style.overflow = prevOverflow;
    setTimeout(() => mask.remove(), 160);
    prevFocus?.focus?.();
  }

  function onKey(e: KeyboardEvent): void {
    if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); close(); return; }
    if (list.length > 1 && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
      e.preventDefault();
      show(cur + (e.key === "ArrowLeft" ? -1 : 1));
      return;
    }
    if (e.key === "+" || e.key === "=") { e.preventDefault(); zoomTo(scale * 1.4); }
    else if (e.key === "-" || e.key === "_") { e.preventDefault(); zoomTo(scale / 1.4); }
    else if (e.key === "0") { e.preventDefault(); resetZoom(); }
  }

  // ── 缩放/拖拽 ──────────────────────────────────────────────────────────
  img.addEventListener("wheel", (e) => {
    e.preventDefault();
    zoomTo(scale * (e.deltaY < 0 ? 1.18 : 1 / 1.18));
  }, { passive: false });

  img.addEventListener("dblclick", (e) => {
    e.preventDefault();
    zoomTo(scale > 1 ? 1 : 2.5);
  });

  img.addEventListener("click", (e) => {
    e.stopPropagation();                       // 点图本身不该关掉灯箱
    if (scale === 1) zoomTo(2.5);
  });

  const pointers = new Map<number, { x: number; y: number }>();
  let dragFrom: { x: number; y: number; tx: number; ty: number } | null = null;
  let pinchFrom: { dist: number; scale: number } | null = null;

  const dist = () => {
    const [a, b] = [...pointers.values()];
    return Math.hypot(a.x - b.x, a.y - b.y);
  };

  img.addEventListener("pointerdown", (e) => {
    img.setPointerCapture(e.pointerId);
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.size === 2) {
      pinchFrom = { dist: dist(), scale };
      dragFrom = null;
    } else if (scale > 1) {
      dragFrom = { x: e.clientX, y: e.clientY, tx, ty };
      img.classList.add("ivl-drag");
    }
  });

  img.addEventListener("pointermove", (e) => {
    if (!pointers.has(e.pointerId)) return;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pinchFrom && pointers.size === 2) {
      const d = dist();
      if (pinchFrom.dist > 0) zoomTo(pinchFrom.scale * (d / pinchFrom.dist));
      return;
    }
    if (dragFrom) {
      tx = dragFrom.tx + (e.clientX - dragFrom.x);
      ty = dragFrom.ty + (e.clientY - dragFrom.y);
      applyTransform();
    }
  });

  const endPointer = (e: PointerEvent) => {
    pointers.delete(e.pointerId);
    if (pointers.size < 2) pinchFrom = null;
    if (pointers.size === 0) {
      dragFrom = null;
      img.classList.remove("ivl-drag");
    }
  };
  img.addEventListener("pointerup", endPointer);
  img.addEventListener("pointercancel", endPointer);

  // ── 组装 ───────────────────────────────────────────────────────────────
  if (list.length > 1) {
    const prev = mkBtn("‹", "上一张", () => show(cur - 1), "ivl-btn ivl-nav ivl-prev");
    const next = mkBtn("›", "下一张", () => show(cur + 1), "ivl-btn ivl-nav ivl-next");
    mask.appendChild(prev);
    mask.appendChild(next);
  }

  bar.appendChild(mkBtn("−", "缩小", () => zoomTo(scale / 1.4)));
  bar.appendChild(mkBtn("＋", "放大", () => zoomTo(scale * 1.4)));
  bar.appendChild(mkBtn("↺", "复位", resetZoom));
  bar.appendChild(mkBtn("↗", "在新标签打开", () => { void openInNewTab(list[cur].src); }));
  bar.appendChild(mkBtn("↓", "下载原图", () => { void download(list[cur], cur); }));
  bar.appendChild(mkBtn("✕", "关闭", close));

  mask.appendChild(img);
  mask.appendChild(bar);
  mask.appendChild(meta);
  mask.addEventListener("click", close);       // 点空白关闭；点图那份 handler 已 stopPropagation

  const prevFocus = document.activeElement as HTMLElement | null;
  const prevOverflow = document.body.style.overflow;
  document.body.style.overflow = "hidden";     // 背景不跟着滚
  document.body.appendChild(mask);
  show(cur);
  requestAnimationFrame(() => mask.classList.add("ivl-in"));
  document.addEventListener("keydown", onKey, true);
  closeCurrent = close;
  (mask.querySelector(".ivl-bar .ivl-btn:last-child") as HTMLElement | null)?.focus();
}

/**
 * 事件委托版：给一个容器挂上，容器里所有 `<img>` 点击都能看原图，同容器的图自动
 * 成为一组(可左右翻)。调用方不用给每张图单独接 onClick。
 */
export function lightboxDelegate(
  opts: { skip?: (el: HTMLImageElement) => boolean } = {},
): (e: { target: EventTarget | null; currentTarget: EventTarget | null; defaultPrevented?: boolean }) => void {
  return (e) => {
    if (e.defaultPrevented) return;
    const el = e.target as HTMLElement | null;
    if (!el || el.tagName !== "IMG") return;
    const img = el as HTMLImageElement;
    if (opts.skip?.(img)) return;
    const root = (e.currentTarget as HTMLElement) || document.body;
    const all = [...root.querySelectorAll("img")].filter(
      (n): n is HTMLImageElement => n instanceof HTMLImageElement && !opts.skip?.(n),
    );
    const idx = Math.max(all.indexOf(img), 0);
    openLightbox(all.map((n) => ({ src: n.currentSrc || n.src, alt: n.alt })), idx);
  };
}
