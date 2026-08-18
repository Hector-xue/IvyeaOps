#!/usr/bin/env python3
"""验证台截图/取值工具（CDP 设备模拟版）—— 只给本地验证用，不参与产品构建。

    python3 harness/shot.py <url> <宽> <高> <out.png|-> ["JS 表达式"]

    python3 harness/shot.py "http://127.0.0.1:5199/?t=quiet-light&r=/console" 390 844 mob.png \
        "JSON.stringify({w:document.documentElement.clientWidth, sw:document.documentElement.scrollWidth})"

**为什么不能直接用 `google-chrome --headless --window-size=390,844 --screenshot`**：
Chrome 的窗口有最小宽度（Linux 上约 500px），你要 390 它给 500 —— 于是布局按
500px 排、图片按 390px 裁，截出来右边少一块，看着像"手机端横向溢出"，其实是
量错了。实测踩过：据此报了一个根本不存在的溢出 bug。这里走 CDP 的
Emulation.setDeviceMetricsOverride，宽度是真的。
"""
import json, tempfile, subprocess, sys, time, urllib.request, websocket, base64, os, signal

def main(url, w, h, out, expr=None, mobile=True, wait=3.5):
    port = 9333
    prof = os.path.join(tempfile.gettempdir(), "ivyea-harness-cdp-profile")
    p = subprocess.Popen(["google-chrome","--headless","--disable-gpu","--no-sandbox",
        f"--remote-debugging-port={port}", f"--user-data-dir={prof}",
        "--window-size=900,1000","--hide-scrollbars","--remote-allow-origins=*","about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json")); break
            except Exception: time.sleep(.25)
        tabs = [t for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json"))
                if t.get("type") == "page"]
        tab = tabs[0]
        ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=30)
        i = [0]
        def send(method, **params):
            i[0] += 1
            ws.send(json.dumps({"id": i[0], "method": method, "params": params}))
            while True:
                m = json.loads(ws.recv())
                if m.get("id") == i[0]: return m.get("result", {})
        send("Page.enable"); send("Runtime.enable")
        send("Emulation.setDeviceMetricsOverride", width=w, height=h,
             deviceScaleFactor=1, mobile=mobile, screenWidth=w, screenHeight=h)
        send("Page.navigate", url=url)
        time.sleep(wait)
        if expr:
            r = send("Runtime.evaluate", expression=expr, returnByValue=True)
            print(json.dumps(r.get("result", {}).get("value"), ensure_ascii=False, indent=1))
        if out:
            r = send("Page.captureScreenshot", format="png", captureBeyondViewport=False)
            open(out, "wb").write(base64.b64decode(r["data"]))
            print("saved", out, w, "x", h)
    finally:
        p.send_signal(signal.SIGTERM); p.wait(timeout=10)

if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0], int(a[1]), int(a[2]), a[3] if len(a) > 3 and a[3] != "-" else None,
         expr=a[4] if len(a) > 4 else None)
