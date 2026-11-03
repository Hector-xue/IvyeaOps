/**
 * 把一个 Blob 存成本地文件。全站「下载」都走这里。
 *
 * 以前各处的写法是 `a.click(); URL.revokeObjectURL(url)` —— 点完立刻吊销链接。
 * Chrome 能容忍（它在 click 时就已经开始读这个 blob），但 Safari / Firefox 的下载是
 * 异步开始的，链接先被吊销，下载就会失败或只得到一个空文件。另外 Firefox 较老的
 * 版本要求 <a> 在文档里才响应 click。所以这里：挂进文档 → 点击 → 移除 → 延迟吊销。
 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
  // 一分钟足够任何浏览器把 blob 读完；之后再释放，避免长时间占内存。
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/** 把一段文本存成文件。 */
export function saveText(content: string, filename: string, mime = "text/plain;charset=utf-8"): void {
  saveBlob(new Blob([content], { type: mime }), filename);
}

/**
 * 通过 URL 下载一个同源文件，并指定保存的文件名。
 * `<a href download="名字">` 对同源 URL 本可以直接改名，但服务端回了 Content-Disposition
 * 时浏览器会优先用服务端的名字；先取成 blob 再存，文件名就一定是我们给的。
 */
export async function saveUrl(url: string, filename: string): Promise<void> {
  const resp = await fetch(url, { credentials: "include" });
  if (!resp.ok) throw new Error(`下载失败（HTTP ${resp.status}）`);
  saveBlob(await resp.blob(), filename);
}
