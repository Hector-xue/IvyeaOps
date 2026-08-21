/**
 * 订阅制模型的登录面板（Claude 订阅 / OpenAI Codex / Gemini Code Assist / Qwen /
 * GitHub Copilot）。
 *
 * ── 为什么要有这个东西 ────────────────────────────────────────────────────
 * 模型接入分两类：填 API key 的，和要走 OAuth 的订阅制。前者在上面填个输入框就完事；
 * 后者此前只有一条路 —— 去 IvyeaAgent 的命令行敲 `ivyea model auth <id> --login`。
 * 结果是"不会用 CLI 的人根本接不上自己已经付过钱的订阅"。
 *
 * ── 三种流程 ──────────────────────────────────────────────────────────────
 *   device：显示一段代码，用户去授权页面输，这边自动轮询（Qwen / Codex）
 *   paste ：给授权链接，用户把回调里的东西整段粘回来（Claude / Gemini）
 *   token ：直接填一个已有的 token（Copilot 其实不是 OAuth）
 *
 * 凭据（PKCE verifier / state / device_code / token）全程留在 agent 那边，
 * 这个组件手里只有授权链接、user_code，和用户自己粘进来的那段文字。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ivyeaAuthStatus, ivyeaAuthStart, ivyeaAuthPoll, ivyeaAuthComplete, ivyeaAuthLogout,
  authStatusLabel,
  type AuthProviderRow, type AuthStartResp,
} from "../../api/ivyeaAgent";
import { errText } from "../../lib/errText";

/** 正在进行中的一次登录。 */
type Flow = AuthStartResp & { provider: string; deadline: number };

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export default function SubscriptionLogin() {
  const [rows, setRows] = useState<AuthProviderRow[] | null>(null);
  const [loadErr, setLoadErr] = useState("");
  const [busy, setBusy] = useState("");
  const [flow, setFlow] = useState<Flow | null>(null);
  const [note, setNote] = useState("");
  const [err, setErr] = useState("");
  const [pasted, setPasted] = useState("");
  const [copied, setCopied] = useState(false);

  /*
   * 轮询用 ref 驱动、由按钮点击启动，**不放在 effect 里**。
   * 放 effect 里就得靠依赖数组和 cleanup 去管生命周期，而 setState 会让依赖变化、
   * cleanup 先跑一遍 —— 上一轮任务台的模型面板就是这么卡死在"正在取模型清单"的。
   */
  const pollingRef = useRef(false);
  useEffect(() => () => { pollingRef.current = false; }, []);

  const load = useCallback(async () => {
    setLoadErr("");
    try {
      const d = await ivyeaAuthStatus();
      setRows(d?.providers || []);
    } catch (e: any) {
      setRows([]);
      setLoadErr(errText(e, "取登录状态失败"));
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const closeFlow = () => {
    pollingRef.current = false;
    setFlow(null);
    setPasted("");
    setNote("");
    setErr("");
  };

  const runDevicePolling = async (provider: string, session: string, interval: number, deadline: number) => {
    pollingRef.current = true;
    let wait = Math.max(1, interval || 2);
    while (pollingRef.current) {
      if (Date.now() > deadline) {
        setErr("这个代码已经过期了，请重新开始。");
        pollingRef.current = false;
        return;
      }
      await sleep(wait * 1000);
      if (!pollingRef.current) return;
      try {
        const d = await ivyeaAuthPoll(provider, session);
        if (d.status === "ok") {
          pollingRef.current = false;
          closeFlow();
          await load();
          return;
        }
        if (d.status === "error") {
          pollingRef.current = false;
          setErr(d.error || "登录失败");
          return;
        }
        // pending：note 是暂时性问题（网关 5xx、网络抖动）—— 一直转圈却不说
        // 为什么，比报错更让人没底。
        setNote(d.note || "");
        wait = Math.max(1, d.interval || wait);
      } catch (e: any) {
        setNote(errText(e, "网络不稳，正在重试"));
        wait = Math.min(wait * 1.5, 10);
      }
    }
  };

  const startLogin = async (row: AuthProviderRow) => {
    if (busy) return;
    setBusy(row.id);
    setErr("");
    setNote("");
    setPasted("");
    try {
      const d = await ivyeaAuthStart(row.id);
      if (!d?.ok) {
        setErr(d?.error || "没能开始登录");
        return;
      }
      const deadline = Date.now() + (d.expires_in || 900) * 1000;
      setFlow({ ...d, provider: row.id, deadline });
      if (d.kind === "device" && d.session) {
        void runDevicePolling(row.id, d.session, d.interval || 2, deadline);
      }
    } catch (e: any) {
      setErr(errText(e, "没能开始登录"));
    } finally {
      setBusy("");
    }
  };

  const submitPasted = async () => {
    if (!flow || !pasted.trim() || busy) return;
    setBusy(flow.provider);
    setErr("");
    try {
      const d = await ivyeaAuthComplete(flow.provider, flow.session || "", pasted.trim());
      if (!d?.ok) {
        setErr(d?.error || "登录失败");
        return;
      }
      closeFlow();
      await load();
    } catch (e: any) {
      setErr(errText(e, "登录失败"));
    } finally {
      setBusy("");
    }
  };

  const logout = async (row: AuthProviderRow) => {
    if (busy) return;
    setBusy(row.id);
    try {
      await ivyeaAuthLogout(row.id);
      await load();
    } catch (e: any) {
      setErr(errText(e, "退出失败"));
    } finally {
      setBusy("");
    }
  };

  const copyCode = async () => {
    if (!flow?.user_code) return;
    try {
      await navigator.clipboard.writeText(flow.user_code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      // 剪贴板在非 https 下会被浏览器拒掉。代码本来就摆在屏幕上，手抄也就八个字符。
    }
  };

  return (
    <div className="sl-wrap">
      {/* 这句必须摆在最前面。凭据存在服务器上、由 agent 全局共用 —— 用户有权在
          按下"登录"之前就知道这件事。 */}
      <div className="sl-warn">
        这里登录的账号存在<b>服务器上</b>、由 IvyeaAgent 全局共用：所有用户的对话和所有定时任务
        都会用它的额度。另外，各家订阅的条款通常是限个人使用的，接进多人共用的工作台是否合规，
        请自行判断。
      </div>

      {rows === null && <div className="sl-note">正在读取登录状态…</div>}
      {loadErr && <div className="sl-note sl-err">{loadErr}</div>}

      {(rows || []).map((row) => {
        const st = authStatusLabel(row.status);
        return (
          <div className="sl-row" key={row.id}>
            <div className="sl-row-main">
              <span className="sl-name">{row.label}</span>
              <span className={"sl-pill tone-" + st.tone}>{st.text}</span>
            </div>
            <div className="sl-row-acts">
              <button type="button" className="sl-btn" disabled={!!busy}
                onClick={() => void startLogin(row)}>
                {row.ready ? "重新登录" : "登录"}
              </button>
              {row.ready && (
                <button type="button" className="sl-btn sl-btn-quiet" disabled={!!busy}
                  onClick={() => void logout(row)}>退出</button>
              )}
            </div>
          </div>
        );
      })}

      {flow && (
        <div className="sl-flow">
          <div className="sl-flow-hd">
            <span>{(rows || []).find((r) => r.id === flow.provider)?.label || flow.provider}</span>
            <button type="button" className="sl-x" onClick={closeFlow}>取消</button>
          </div>
          {flow.hint && <div className="sl-note">{flow.hint}</div>}

          {flow.kind === "device" && (
            <>
              <div className="sl-code-row">
                <code className="sl-code">{flow.user_code}</code>
                <button type="button" className="sl-btn sl-btn-quiet" onClick={() => void copyCode()}>
                  {copied ? "已复制" : "复制"}
                </button>
              </div>
              {flow.verification_uri && (
                <a className="sl-link" href={flow.verification_uri} target="_blank" rel="noreferrer">
                  打开授权页面 ↗
                </a>
              )}
              <div className="sl-note">授权完成后这里会自动完成，不用回来点任何按钮。</div>
            </>
          )}

          {flow.kind === "paste" && (
            <>
              {flow.url && (
                <a className="sl-link" href={flow.url} target="_blank" rel="noreferrer">
                  打开授权页面 ↗
                </a>
              )}
              <textarea
                className="sl-input" rows={3} value={pasted}
                onChange={(e) => setPasted(e.target.value)}
                placeholder={flow.provider === "anthropic-oauth"
                  ? "把页面上那段 code#state 整段粘到这里"
                  : "把浏览器地址栏里那条完整 URL 粘到这里"}
                spellCheck={false}
              />
              <button type="button" className="sl-btn" disabled={!pasted.trim() || !!busy}
                onClick={() => void submitPasted()}>
                {busy ? "提交中…" : "提交"}
              </button>
            </>
          )}

          {flow.kind === "token" && (
            <>
              <input
                className="sl-input" type="password" value={pasted}
                onChange={(e) => setPasted(e.target.value)}
                placeholder="gho_… / ghu_… / github_pat_…"
                spellCheck={false} autoComplete="new-password"
              />
              <button type="button" className="sl-btn" disabled={!pasted.trim() || !!busy}
                onClick={() => void submitPasted()}>
                {busy ? "提交中…" : "提交"}
              </button>
            </>
          )}

          {note && <div className="sl-note">{note}</div>}
          {err && <div className="sl-note sl-err">{err}</div>}
        </div>
      )}
    </div>
  );
}
