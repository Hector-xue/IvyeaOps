import { useEffect, useRef, useState } from "react";
import { TerminalSession, terminalWebSocketUrl } from "../api/terminalLive";
import "xterm/css/xterm.css";
import XtermActionToolbar from "./XtermActionToolbar";
import { onThemeChange, xtermTheme } from "../lib/termTheme";
import {
  enableNativeSelectionMode,
  getSelectedTerminalText,
  getVisibleTerminalText,
} from "./xtermSelection";

type Props = {
  session: TerminalSession;
  onExit?: () => void;
  onLiveOutput?: () => void;
};

const OSC_COLOR_REPLY_RE = /\x1b\](10|11);(?:rgb:[0-9a-fA-F/]+|\?)(?:\x07|\x1b\\)/g;
const BARE_OSC_COLOR_REPLY_RE = /\](10|11);rgb:[0-9a-fA-F/]+/g;

function stripTerminalAutoReplies(data: string): string {
  if (!data) return data;
  let next = data.replace(OSC_COLOR_REPLY_RE, "");
  next = next.replace(BARE_OSC_COLOR_REPLY_RE, "");
  return next;
}

export default function TerminalLivePane({ session, onExit, onLiveOutput }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<any>(null);
  const fitRef = useRef<any>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const selectModeRef = useRef(false);
  const feedbackTimerRef = useRef<number | null>(null);
  const mobileEnhancementCleanupRef = useRef<(() => void) | null>(null);
  const suppressResizeUntilRef = useRef(0);
  const delayedResizeTimerRef = useRef<number | null>(null);
  const onExitRef = useRef<Props["onExit"]>(onExit);
  const onLiveOutputRef = useRef<Props["onLiveOutput"]>(onLiveOutput);
  const [connected, setConnected] = useState(false);
  const [closing, setClosing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectMode, setSelectMode] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [isMobile, setIsMobile] = useState(false);

  selectModeRef.current = selectMode;
  onExitRef.current = onExit;
  onLiveOutputRef.current = onLiveOutput;

  const setupMobileEnhancements = () => {
    mobileEnhancementCleanupRef.current?.();
    mobileEnhancementCleanupRef.current = null;

    const host = containerRef.current;
    if (!host || !isMobile) return false;

    const viewport = host.querySelector<HTMLElement>(".xterm-viewport");
    if (!viewport) return false;

    const screen = host.querySelector<HTMLElement>(".xterm-screen") || host;
    const helper = host.querySelector<HTMLElement>(".xterm-helper-textarea");
    const prevHelperPointer = helper?.style.pointerEvents || "";
    const prevHostTouch = host.style.touchAction;
    const prevViewportTouch = viewport.style.touchAction;
    const prevViewportMomentum = viewport.style.getPropertyValue("-webkit-overflow-scrolling");
    const prevViewportOverscroll = viewport.style.overscrollBehaviorY;
    const prevViewportOverflowY = viewport.style.overflowY;
    const prevScreenTouch = screen.style.touchAction;

    host.style.touchAction = "pan-y";
    viewport.style.touchAction = "pan-y";
    viewport.style.setProperty("-webkit-overflow-scrolling", "touch");
    viewport.style.overscrollBehaviorY = "contain";
    viewport.style.overflowY = "auto";
    screen.style.touchAction = "pan-y";
    if (helper) helper.style.pointerEvents = "none";

    const SCROLL_GAIN = 1.35;
    const DRAG_LOCK_THRESHOLD = 2;
    const TAP_MOVE_THRESHOLD = 8;
    const TAP_MAX_DURATION_MS = 280;
    const SUPPRESS_CLICK_AFTER_SCROLL_MS = 420;
    const SUPPRESS_RESIZE_AFTER_SCROLL_MS = 520;
    let lastY = 0;
    let lastX = 0;
    let startY = 0;
    let startX = 0;
    let touchStartAt = 0;
    let draggingScroll = false;
    let suppressClickUntil = 0;

    const focusTerminal = () => termRef.current?.focus?.();

    const onTouchStart = (event: TouchEvent) => {
      if (selectModeRef.current || !event.touches.length) return;
      const touch = event.touches[0];
      lastY = touch.clientY;
      lastX = touch.clientX;
      startY = touch.clientY;
      startX = touch.clientX;
      touchStartAt = Date.now();
      draggingScroll = false;
    };

    const onTouchMove = (event: TouchEvent) => {
      if (selectModeRef.current || !event.touches.length) return;
      const touch = event.touches[0];
      const dy = touch.clientY - lastY;
      const dx = touch.clientX - lastX;
      if (!draggingScroll && Math.abs(dy) < DRAG_LOCK_THRESHOLD) {
        return;
      }
      if (!draggingScroll && Math.abs(dy) <= Math.abs(dx) * 0.75) {
        return;
      }
      draggingScroll = true;
      const prevTop = viewport.scrollTop;
      if (viewport.scrollHeight > viewport.clientHeight) {
        viewport.scrollTop -= dy * SCROLL_GAIN;
      }
      const scrolledDom = Math.abs(viewport.scrollTop - prevTop) > 0.5;
      if (!scrolledDom) {
        const fallbackLines = Math.max(1, Math.round(Math.abs(dy) / 10));
        termRef.current?.scrollLines?.(dy > 0 ? -fallbackLines : fallbackLines);
      }
      suppressClickUntil = Date.now() + SUPPRESS_CLICK_AFTER_SCROLL_MS;
      suppressResizeUntilRef.current = Date.now() + SUPPRESS_RESIZE_AFTER_SCROLL_MS;
      event.preventDefault();
      event.stopImmediatePropagation();
      lastY = touch.clientY;
      lastX = touch.clientX;
    };

    const onTouchEnd = (event: TouchEvent) => {
      if (selectModeRef.current) return;
      const touch = event.changedTouches?.[0];
      if (!touch) {
        if (draggingScroll) {
          suppressClickUntil = Date.now() + SUPPRESS_CLICK_AFTER_SCROLL_MS;
          suppressResizeUntilRef.current = Date.now() + SUPPRESS_RESIZE_AFTER_SCROLL_MS;
        }
        draggingScroll = false;
        return;
      }
      const movedX = Math.abs(touch.clientX - startX);
      const movedY = Math.abs(touch.clientY - startY);
      const duration = Date.now() - touchStartAt;
      const isTap = !draggingScroll && movedX <= TAP_MOVE_THRESHOLD && movedY <= TAP_MOVE_THRESHOLD && duration <= TAP_MAX_DURATION_MS;
      if (isTap) {
        focusTerminal();
      } else {
        suppressClickUntil = Date.now() + SUPPRESS_CLICK_AFTER_SCROLL_MS;
        suppressResizeUntilRef.current = Date.now() + SUPPRESS_RESIZE_AFTER_SCROLL_MS;
      }
      draggingScroll = false;
    };

    const onClick = (event: MouseEvent) => {
      if (selectModeRef.current) return;
      if (Date.now() < suppressClickUntil) {
        event.preventDefault();
        event.stopImmediatePropagation();
        return;
      }
      focusTerminal();
    };

    host.addEventListener("touchstart", onTouchStart, { passive: true, capture: true });
    host.addEventListener("touchmove", onTouchMove, { passive: false, capture: true });
    host.addEventListener("touchend", onTouchEnd, { passive: true, capture: true });
    host.addEventListener("click", onClick, true);

    mobileEnhancementCleanupRef.current = () => {
      host.removeEventListener("touchstart", onTouchStart, true);
      host.removeEventListener("touchmove", onTouchMove, true);
      host.removeEventListener("touchend", onTouchEnd, true);
      host.removeEventListener("click", onClick, true);
      host.style.touchAction = prevHostTouch;
      viewport.style.touchAction = prevViewportTouch;
      if (prevViewportMomentum) {
        viewport.style.setProperty("-webkit-overflow-scrolling", prevViewportMomentum);
      } else {
        viewport.style.removeProperty("-webkit-overflow-scrolling");
      }
      viewport.style.overscrollBehaviorY = prevViewportOverscroll;
      viewport.style.overflowY = prevViewportOverflowY;
      screen.style.touchAction = prevScreenTouch;
      if (helper) helper.style.pointerEvents = prevHelperPointer;
    };
    return true;
  };

  const flash = (text: string) => {
    if (feedbackTimerRef.current) window.clearTimeout(feedbackTimerRef.current);
    setFeedback(text);
    feedbackTimerRef.current = window.setTimeout(() => setFeedback(null), 1600);
  };

  useEffect(() => {
    return () => {
      if (feedbackTimerRef.current) window.clearTimeout(feedbackTimerRef.current);
    };
  }, []);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 760px)");
    const apply = (matches: boolean) => setIsMobile(matches);
    apply(media.matches);
    const onChange = (event: MediaQueryListEvent) => apply(event.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    let disposed = false;
    let term: any = null;
    let unsubTheme: (() => void) | null = null;

    (async () => {
      const xterm = await import("xterm");
      const fitAddon = await import("xterm-addon-fit");
      if (disposed || !containerRef.current) return;

      term = new xterm.Terminal({
        fontFamily: "'JetBrains Mono','Fira Code','SF Mono',Menlo,Consolas,monospace",
        fontSize: 12,
        // xterm 把字符画在 canvas 上，CSS 变量进不来 —— 这里在运行时把当前主题
        // 读成对象喂给它，并在主题切换时重算（见下面的 onThemeChange）。
        theme: xtermTheme(),
        cursorBlink: true,
        scrollback: 8000,
        convertEol: false,
        allowTransparency: false,
      });
      const fit = new fitAddon.FitAddon();
      term.loadAddon(fit);
      term.open(containerRef.current);
      requestAnimationFrame(() => {
        try {
          fit.fit();
          term.focus();
          setupMobileEnhancements();
        } catch {
          // ignore
        }
      });
      termRef.current = term;
      fitRef.current = fit;

      // 主题切换时把配色重算一遍。xterm 支持运行时改 options.theme，
      // 不需要重建终端，所以正在跑的会话不会被打断。
      unsubTheme = onThemeChange((next) => {
        const t = termRef.current;
        if (t) t.options.theme = next;
      });

      term.onData((data: string) => {
        if (selectModeRef.current) return;
        const ws = wsRef.current;
        const filtered = stripTerminalAutoReplies(data);
        if (!filtered) return;
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "input", data: filtered }));
        }
      });

      const onResize = () => {
        const now = Date.now();
        const suppressUntil = suppressResizeUntilRef.current;
        if (isMobile && now < suppressUntil) {
          if (delayedResizeTimerRef.current !== null) {
            window.clearTimeout(delayedResizeTimerRef.current);
          }
          delayedResizeTimerRef.current = window.setTimeout(() => {
            delayedResizeTimerRef.current = null;
            onResize();
          }, Math.max(32, suppressUntil - now + 16));
          return;
        }
        try {
          fit.fit();
          const ws = wsRef.current;
          if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
          }
        } catch {
          // ignore
        }
      };
      window.addEventListener("resize", onResize);
      const ro = new ResizeObserver(() => requestAnimationFrame(onResize));
      if (containerRef.current) ro.observe(containerRef.current);

      connect();

      term._ivyeaOpsCleanup = () => {
        window.removeEventListener("resize", onResize);
        ro.disconnect();
        if (delayedResizeTimerRef.current !== null) {
          window.clearTimeout(delayedResizeTimerRef.current);
          delayedResizeTimerRef.current = null;
        }
        mobileEnhancementCleanupRef.current?.();
        mobileEnhancementCleanupRef.current = null;
      };
    })();

    return () => {
      disposed = true;
      unsubTheme?.();
      const ws = wsRef.current;
      if (ws) {
        try {
          ws.close();
        } catch {
          // ignore
        }
        wsRef.current = null;
      }
      const t = termRef.current;
      if (t) {
        try {
          t._ivyeaOpsCleanup?.();
          t.dispose();
        } catch {
          // ignore
        }
        termRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.id]);

  useEffect(() => {
    const host = containerRef.current;
    if (!host || !selectMode) return;
    return enableNativeSelectionMode(host);
  }, [selectMode]);

  useEffect(() => {
    if (setupMobileEnhancements()) return () => {
      mobileEnhancementCleanupRef.current?.();
      mobileEnhancementCleanupRef.current = null;
    };
    if (!isMobile || !containerRef.current) return;
    const observer = new MutationObserver(() => {
      setupMobileEnhancements();
    });
    observer.observe(containerRef.current, { childList: true, subtree: true });
    return () => {
      observer.disconnect();
      mobileEnhancementCleanupRef.current?.();
      mobileEnhancementCleanupRef.current = null;
    };
  }, [isMobile, session.id]);

  const connect = () => {
    setError(null);
    const url = terminalWebSocketUrl(session.id);
    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
    } catch (e: any) {
      setError(e?.message || "无法连接 WebSocket");
      return;
    }
    wsRef.current = ws;
    ws.onopen = () => {
      setConnected(true);
      setClosing(false);
      const term = termRef.current;
      if (term) {
        ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
      }
    };
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        const term = termRef.current;
        if (!term) return;
        if (msg.type === "snapshot") {
          term.reset();
          term.write(msg.data || "");
          onLiveOutputRef.current?.();
        } else if (msg.type === "output") {
          term.write(msg.data || "");
          onLiveOutputRef.current?.();
        } else if (msg.type === "exit") {
          term.write(`\r\n\x1b[31m[终端已退出 code=${msg.code ?? "?"}]\x1b[0m\r\n`);
          onExitRef.current?.();
          onLiveOutputRef.current?.();
        } else if (msg.type === "error") {
          term.write(`\r\n\x1b[31m[error] ${msg.detail}\x1b[0m\r\n`);
        }
      } catch {
        termRef.current?.write(String(ev.data));
      }
    };
    ws.onclose = () => {
      setConnected(false);
      setClosing(true);
    };
    ws.onerror = () => {
      setError("WebSocket 错误");
    };
  };

  const reconnect = () => {
    const ws = wsRef.current;
    if (ws && ws.readyState <= WebSocket.OPEN) {
      try {
        ws.close();
      } catch {
        // ignore
      }
    }
    const term = termRef.current;
    if (term) term.clear();
    connect();
  };

  const sendRawInput = (data: string) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "input", data }));
    }
  };

  const copyText = async (text: string, emptyTip: string) => {
    if (!text) {
      flash(emptyTip);
      return;
    }
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        window.prompt("复制以下内容：", text);
      }
      flash("已复制");
    } catch {
      window.prompt("复制以下内容：", text);
      flash("已打开复制窗口");
    }
  };

  const handlePaste = async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (!text) {
        flash("剪贴板为空");
        return;
      }
      sendRawInput(text);
      flash("已粘贴");
    } catch {
      flash("浏览器拒绝了剪贴板读取");
    }
  };

  return (
    <div className={"cli-pane terminal-live-pane" + (selectMode ? " select-mode" : "")}>
      <div className="cli-live-shell">
        <div className="cli-bar">
          <span className={"cli-status " + (connected ? "live" : "dead")}>
            {connected ? "● 已连接" : closing ? "○ 已断开" : "○ 连接中..."}
          </span>
          <span style={{ color: "var(--t3)" }}>
            shell={session.shell || "/bin/bash"} · cwd={session.workdir || "~"}
          </span>
          {feedback && <span className="xterm-feedback">{feedback}</span>}
        </div>
        <div ref={containerRef} className="cli-host" />
      </div>
      <div className="terminal-toolbar-dock">
        <XtermActionToolbar
          mobileMode={isMobile}
          selectMode={selectMode}
          onToggleSelectMode={() => {
            setSelectMode((prev) => {
              const next = !prev;
              flash(next ? "已进入复制模式" : "已恢复输入模式");
              return next;
            });
          }}
          onCopySelection={() => copyText(getSelectedTerminalText(termRef.current), "暂无选中文本")}
          onCopyVisible={() => copyText(getVisibleTerminalText(containerRef.current), "当前屏幕没有可复制内容")}
          onPaste={handlePaste}
          onSendShortcut={(data) => {
            sendRawInput(data);
            flash(`已发送 ${data === "\u001b" ? "Esc" : data === "\t" ? "Tab" : data === "\u0003" ? "Ctrl+C" : data === "\r" ? "Enter" : "快捷键"}`);
          }}
          extra={!connected ? (
            <button
              className="tbtn"
              onClick={reconnect}
              style={{ color: "var(--acc)", borderColor: "rgba(74,222,128,.4)" }}
            >
              ↻ 重连
            </button>
          ) : null}
        />
      </div>
      {error && <div className="terminal-live-error">⚠ {error}</div>}
    </div>
  );
}
