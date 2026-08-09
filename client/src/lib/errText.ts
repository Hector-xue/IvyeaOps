/**
 * 把任意后端错误归一成**一个字符串**。
 *
 * 为什么需要它：FastAPI 的 422（参数校验失败）返回的 `detail` 是**对象数组**，
 * 不是字符串 ——
 *
 * ```json
 * {"detail": [{"type": "string_pattern_mismatch", "loc": ["query", "sort"], "msg": "..."}]}
 * ```
 *
 * 而全站到处都是 `setErr(e?.response?.data?.detail)` 然后 `{err}` 直接渲染。
 * 一旦真的收到 422，React 会因为"对象不能作为子节点"整页崩掉 —— 用户看到的是
 * 「渲染失败」，而真正的原因（某个参数传错了）一个字都没露出来。一个参数写错
 * 本该是条提示，结果变成了白屏。
 *
 * 所以：**永远不要把 detail 直接塞进 JSX**，先过这里。
 */

type Detail = unknown;

function fromDetail(detail: Detail): string {
  if (typeof detail === "string") return detail;

  // 422：把每条校验错误压成「字段：说明」
  if (Array.isArray(detail)) {
    const parts = detail.map((d: any) => {
      if (typeof d === "string") return d;
      const loc = Array.isArray(d?.loc)
        // 跳过 body/query/path 这层前缀，用户不关心它在哪个部位
        ? d.loc.filter((x: unknown) => !["body", "query", "path"].includes(String(x))).join(".")
        : "";
      const msg = d?.msg || d?.message || "";
      return loc && msg ? `${loc}：${msg}` : msg || loc || "";
    }).filter(Boolean);
    if (parts.length) return parts.join("；");
  }

  if (detail && typeof detail === "object") {
    const d = detail as any;
    if (typeof d.msg === "string") return d.msg;
    if (typeof d.message === "string") return d.message;
    try {
      return JSON.stringify(detail);
    } catch {
      /* 循环引用之类，落到下面的兜底 */
    }
  }
  return "";
}

/**
 * @param err     捕获到的异常（axios 错误、Error、或任意值）
 * @param fallback 什么都提取不到时的兜底文案。**别用「未知错误」** ——
 *                 那句话对用户没有任何用处；写清楚是哪个动作失败了。
 */
export function errText(err: unknown, fallback = "操作失败"): string {
  const e = err as any;

  const detail = fromDetail(e?.response?.data?.detail);
  if (detail) return detail;

  // 我们自己的统一错误契约（见 server/app/main.py 的 _error_body）
  const contract = e?.response?.data?.error;
  if (contract?.message) {
    return contract.hint ? `${contract.message}（${contract.hint}）` : String(contract.message);
  }

  if (typeof e?.response?.data === "string" && e.response.data) return e.response.data;
  if (typeof e?.message === "string" && e.message) return e.message;
  if (typeof err === "string" && err) return err;
  return fallback;
}
