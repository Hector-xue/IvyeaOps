"""主题全量扫描：把每个界面都跑一遍，找出"没跟上主题"的元素。

用法（需要本地起一个 ops 前端，见 --base）：
    python3 client/e2e/theme_audit.py --themes mendao-light,mendao-dark
    python3 client/e2e/theme_audit.py --themes dark --checks shadow,radius   # 回归基线

为什么要这个东西：ops 有 32 条路由 + 约 31 个**非路由的 tab 子页**，靠人点一遍
既慢又必然漏。而"换主题"最典型的失败不是整页错，是某个组件写死了颜色 ——
它在别的主题下看着还行，换一套就瞎。肉眼扫不出来，计算样式能。

**为什么用 --remote-debugging-port 而不是仓库里已有的 pipe 方案**：
e2e/knowledge-governance.mjs 那套 `--remote-debugging-pipe` 在当前 Chrome 上
已经跑不通了（Page.navigate 必超时，与本次改动无关，改动前后一模一样）。
端口方案实测可用。端口用 0 让内核随机分配，避免连上上一次跑残的僵尸实例。

检测项（--checks 选）：
  radius    圆角残留（flat 皮肤下白名单之外不该有）
  shadow    阴影残留
  vars      **无效变量探针** —— <html> 上的 token 若是裸 HSL 三元组就报错。
            这一项零成本，专抓 agents 那个 chunk 污染 :root 的旧病复发。
  contrast  正文对比度 < 4.5（专抓"写死浅色配深色底"那类组件）
  fontsize  计算字号 < 10.5px（门道的最小档）
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "server"))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class CDP:
    def __init__(self, port: int):
        import websockets.sync.client as wsc

        for _ in range(80):
            try:
                pages = json.loads(urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/list", timeout=2).read())
                page = next(p for p in pages if p["type"] == "page")
                self.ws = wsc.connect(page["webSocketDebuggerUrl"],
                                      max_size=256 * 1024 * 1024)
                self.n = 0
                return
            except Exception:
                time.sleep(0.5)
        raise RuntimeError("连不上 CDP")

    def send(self, method: str, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def eval(self, expr: str):
        r = self.send("Runtime.evaluate", expression=expr, awaitPromise=True,
                      returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("text", "eval failed"))
        return r.get("result", {}).get("value")


# 注入页面的检测器。写成一大段 JS 是因为要遍历上万个节点，
# 逐个 CDP 往返会慢两个数量级。
DETECTOR = r"""
(() => {
  const CHECKS = new Set(__CHECKS__);
  const out = { radius: [], shadow: [], vars: [], contrast: [], fontsize: [] };
  // 功能性的圆：和 styles/mendao-skin.css 的白名单一一对应，改那边要改这边
  const ROUND_OK = ['.animate-spin', '.spin', '.lx-spin-dot', '.cs-run', '.cc-run',
                    '.vs-switch > span', '.confirm-icon-ring'];
  const path = (el) => {
    const bits = [];
    for (let n = el; n && n.nodeType === 1 && bits.length < 4; n = n.parentElement) {
      let s = n.tagName.toLowerCase();
      if (n.id) { s += '#' + n.id; bits.unshift(s); break; }
      const cls = (n.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean).slice(0, 2);
      if (cls.length) s += '.' + cls.join('.');
      bits.unshift(s);
    }
    return bits.join(' > ');
  };
  const parse = (c) => {
    const m = /rgba?\(([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:[,/\s]+([\d.]+))?/.exec(c || '');
    return m ? [+m[1], +m[2], +m[3], m[4] === undefined ? 1 : +m[4]] : null;
  };
  const lum = ([r, g, b]) => {
    const f = (v) => { v /= 255; return v <= .03928 ? v / 12.92 : Math.pow((v + .055) / 1.055, 2.4); };
    return .2126 * f(r) + .7152 * f(g) + .0722 * f(b);
  };
  // 往上穿透透明背景，找到真正压在下面的那层颜色
  const effBg = (el) => {
    for (let n = el; n; n = n.parentElement) {
      const c = parse(getComputedStyle(n).backgroundColor);
      if (c && c[3] > .5) return c;
    }
    return [255, 255, 255];
  };

  if (CHECKS.has('vars')) {
    const cs = getComputedStyle(document.documentElement);
    for (const name of ['--ring', '--acc', '--bg', '--bg1', '--t', '--b']) {
      const v = cs.getPropertyValue(name).trim();
      // 裸 HSL 三元组（`221.2 83.2% 53.3%`）= 被 agents 的 shadcn 变量污染了
      if (/^[\d.]+\s+[\d.]+%\s+[\d.]+%$/.test(v)) out.vars.push({ name, value: v });
      else if (!v) out.vars.push({ name, value: '(未定义)' });
    }
  }

  const els = document.querySelectorAll('*');
  for (const el of els) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;               // 不可见的不算
    const cs = getComputedStyle(el);

    if (CHECKS.has('radius')) {
      const br = cs.borderRadius;
      if (br && br !== '0px' && !ROUND_OK.some((s) => el.matches(s))) {
        out.radius.push({ sel: path(el), value: br });
      }
    }
    if (CHECKS.has('shadow') && cs.boxShadow && cs.boxShadow !== 'none') {
      out.shadow.push({ sel: path(el), value: cs.boxShadow.slice(0, 60) });
    }
    if (CHECKS.has('fontsize')) {
      const fs = parseFloat(cs.fontSize);
      const txt = (el.textContent || '').trim();
      if (fs && fs < 10.4 && txt && el.children.length === 0) {
        out.fontsize.push({ sel: path(el), value: cs.fontSize, text: txt.slice(0, 18) });
      }
    }
    if (CHECKS.has('contrast')) {
      const txt = (el.textContent || '').trim();
      if (txt && el.children.length === 0) {
        const fg = parse(cs.color);
        if (fg && fg[3] > .5) {
          const L1 = lum(fg), L2 = lum(effBg(el));
          const ratio = (Math.max(L1, L2) + .05) / (Math.min(L1, L2) + .05);
          if (ratio < 4.5) {
            out.contrast.push({ sel: path(el), value: ratio.toFixed(2),
                                fg: cs.color, text: txt.slice(0, 18) });
          }
        }
      }
    }
  }
  // 同一个选择器只留一条，否则一张表格能刷出几百行
  for (const k of Object.keys(out)) {
    const seen = new Set();
    out[k] = out[k].filter((x) => !seen.has(x.sel || x.name) && seen.add(x.sel || x.name)).slice(0, 12);
  }
  return JSON.stringify(out);
})()
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5198")
    ap.add_argument("--themes", default="mendao-light,mendao-dark")
    ap.add_argument("--checks", default="radius,shadow,vars,contrast,fontsize")
    ap.add_argument("--only", default="", help="只跑名字里含这个词的界面")
    ap.add_argument("--shots", default="", help="截图落到哪个目录（留空不截）")
    args = ap.parse_args()

    checks = [c.strip() for c in args.checks.split(",") if c.strip()]
    surfaces = json.load(open(os.path.join(HERE, "surfaces.json"), encoding="utf-8"))
    items = [dict(s, enter="url") for s in surfaces["routes"]] + surfaces["tabs"]
    if args.only:
        items = [s for s in items if args.only in s["name"] or args.only in s["path"]]

    from app.core.config import settings
    from itsdangerous import URLSafeTimedSerializer
    token = URLSafeTimedSerializer(settings.secret_key,
                                   salt="ivyea-ops.session").dumps({"id": "admin", "r": "admin"})

    port = _free_port()
    profile = f"/tmp/theme-audit-{port}"
    chrome = subprocess.Popen(
        ["google-chrome", "--headless=new", "--disable-gpu", "--no-sandbox",
         "--hide-scrollbars", "--window-size=1600,1000",
         f"--remote-debugging-port={port}", f"--user-data-dir={profile}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    findings = 0
    try:
        cdp = CDP(port)
        for m in ("Page.enable", "Runtime.enable", "Network.enable"):
            cdp.send(m)
        cdp.send("Network.setCookie", name=settings.session_cookie_name, value=token,
                 domain="127.0.0.1", path="/")
        detector = DETECTOR.replace("__CHECKS__", json.dumps(checks))

        for theme in args.themes.split(","):
            print(f"\n{'=' * 78}\n主题 {theme}\n{'=' * 78}")
            for s in items:
                cdp.send("Page.navigate", url=f"{args.base}/")
                time.sleep(1.0)
                cdp.eval(f"localStorage.setItem('ivyea-ops.theme','{theme}')")
                cdp.eval("Object.keys(localStorage).filter(k=>k.startsWith('ivyea-tour:'))"
                         ".forEach(k=>localStorage.removeItem(k));"
                         "['/dashboard','/console','/market','/tools','/agents','/brain',"
                         "'/listing','/servmon','/hub-settings','/capabilities','/lingxing']"
                         ".forEach(p=>localStorage.setItem('ivyea-tour:'+p,'1'))")
                for k, v in (s.get("storage") or {}).items():
                    cdp.eval(f"localStorage.setItem({json.dumps(k)},{json.dumps(v)})")
                cdp.send("Page.navigate", url=f"{args.base}{s['path']}")
                time.sleep(3.2)
                if s.get("enter") == "click":
                    # 没有任何 URL 状态的那几个：点第 n 个 tab，等 DOM 静下来
                    cdp.eval(
                        "(()=>{const t=[...document.querySelectorAll("
                        "'[role=tab],.tab,[class*=-tab] button,[class*=step] button')]"
                        f".filter(e=>e.offsetParent);const e=t[{s.get('nth', 0)}];"
                        "if(e)e.click();return !!e;})()")
                    time.sleep(2.0)

                try:
                    res = json.loads(cdp.eval(detector))
                except Exception as exc:                       # noqa: BLE001
                    print(f"  {s['name']:16} ⚠ 检测器没跑起来：{exc}")
                    continue
                hits = {k: v for k, v in res.items() if v}
                if not hits:
                    print(f"  {s['name']:16} ✓")
                    continue
                findings += sum(len(v) for v in hits.values())
                print(f"  {s['name']:16} {' '.join(f'{k}×{len(v)}' for k, v in hits.items())}")
                for k, rows in hits.items():
                    for row in rows[:4]:
                        extra = f" 「{row['text']}」" if row.get("text") else ""
                        print(f"      [{k}] {row.get('sel', row.get('name'))} = "
                              f"{row['value']}{extra}")
                if args.shots:
                    os.makedirs(args.shots, exist_ok=True)
                    png = cdp.send("Page.captureScreenshot", format="png")["data"]
                    safe = s["name"].replace("/", "_")
                    with open(os.path.join(args.shots, f"{theme}_{safe}.png"), "wb") as f:
                        f.write(base64.b64decode(png))
        print(f"\n合计 {findings} 条")
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=10)
        except subprocess.TimeoutExpired:
            chrome.kill()
        shutil.rmtree(profile, ignore_errors=True)
    sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()
