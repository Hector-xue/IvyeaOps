/* 深度分析结构化结果视图 — 关键词/竞品/流量三个工具的结果渲染。
 * 面板（实时结果）与 DeepHistory（历史回看）共用，保证两处呈现一致。
 * 原则：不截断数据（长文本折行/滚动），嵌套结构可折叠，原始 JSON 兜底。 */

const cellStyle: React.CSSProperties = { padding: "4px 6px" };

function isPrimitive(v: unknown): v is string | number | boolean {
  return typeof v === "string" || typeof v === "number" || typeof v === "boolean";
}

/** Sorftime 把每个答案包成 { doc: {字段说明}, data: … }：doc 是给模型看的字段表，
 *  真正的内容在 data。取行数据统一走这里，否则 `.length` 永远是 undefined，
 *  表格会一声不响地空着。 */
function rowsOf(value: unknown): any[] {
  if (Array.isArray(value)) return value;
  if (value && typeof value === "object") {
    const node: any = (value as any).doc && "data" in (value as any) ? (value as any).data : value;
    if (Array.isArray(node)) return node;
    for (const key of ["data", "items", "results", "list", "top100_products", "analysis_results"]) {
      if (Array.isArray(node?.[key])) return node[key];
    }
  }
  return [];
}

/** 通用键值表：原始值整行显示（折行），字符串数组按行列出，嵌套对象折叠。 */
function KVTable({ obj }: { obj: Record<string, unknown> }) {
  const entries = Object.entries(obj).filter(([, v]) => v !== null && v !== undefined && v !== "");
  if (entries.length === 0) return null;
  return (
    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "var(--fs-10)" }}>
      <tbody>
        {entries.map(([k, v]) => (
          <tr key={k} style={{ borderBottom: "1px solid var(--b)" }}>
            <td style={{ ...cellStyle, width: 140, color: "var(--t3)", verticalAlign: "top", whiteSpace: "nowrap" }}>{k}</td>
            <td style={{ ...cellStyle, color: "var(--t)", wordBreak: "break-word", lineHeight: 1.6 }}>
              {isPrimitive(v) ? String(v)
                : Array.isArray(v) && v.every(isPrimitive) ? (
                  <div>{v.map((it, i) => <div key={i}>{String(it)}</div>)}</div>
                ) : (
                  <details>
                    <summary style={{ fontSize: "var(--fs-9)", color: "var(--t3)", cursor: "pointer" }}>
                      展开（{Array.isArray(v) ? `${v.length} 项` : "对象"}）
                    </summary>
                    <pre style={{ fontSize: "var(--fs-9)", maxHeight: 220, overflow: "auto", padding: 6, background: "var(--bg)", borderRadius: 4, marginTop: 4, whiteSpace: "pre-wrap" }}>
                      {JSON.stringify(v, null, 2)}
                    </pre>
                  </details>
                )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <div style={{ fontSize: "var(--fs-10)", color: "var(--t2)", marginBottom: 6 }}>{children}</div>;
}

function ErrorList({ errors }: { errors?: string[] }) {
  if (!errors?.length) return null;
  return (
    <div style={{ marginBottom: 10, fontSize: "var(--fs-10)", color: "var(--amber)" }}>
      {errors.map((e, i) => <div key={i}>⚠ {e}</div>)}
    </div>
  );
}

function JsonDetails({ data }: { data: unknown }) {
  return (
    <details style={{ marginTop: 12 }}>
      <summary style={{ fontSize: "var(--fs-10)", color: "var(--t3)", cursor: "pointer" }}>查看原始 JSON</summary>
      <pre style={{ fontSize: "var(--fs-9)", maxHeight: 300, overflow: "auto", padding: 8, background: "var(--bg)", borderRadius: 4, marginTop: 4 }}>
        {JSON.stringify(data, null, 2)}
      </pre>
    </details>
  );
}

/** 长文本块：不截断，超高滚动。 */
function LongText({ text }: { text: string }) {
  return (
    <div style={{ fontSize: "var(--fs-10)", lineHeight: 1.7, color: "var(--t)", maxHeight: 260, overflowY: "auto", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
      {text}
    </div>
  );
}

/** 字符串 / 数组 / 对象三态兜底渲染（流量词、竞品词这类形状不稳定的字段）。 */
function FlexiList({ value, max = 20 }: { value: unknown; max?: number }) {
  if (typeof value === "string") return <LongText text={value} />;
  if (Array.isArray(value)) {
    return (
      <div style={{ fontSize: "var(--fs-10)" }}>
        {value.slice(0, max).map((t: any, i: number) => (
          <div key={i} style={{ padding: "3px 6px", background: "var(--bg3)", borderRadius: 4, marginBottom: 3, wordBreak: "break-word" }}>
            {isPrimitive(t) ? String(t) : t.keyword || t.关键词 || JSON.stringify(t)}
          </div>
        ))}
        {value.length > max && <div style={{ fontSize: "var(--fs-9)", color: "var(--t3)" }}>… 共 {value.length} 项，其余见原始 JSON</div>}
      </div>
    );
  }
  if (value && typeof value === "object") return <KVTable obj={value as Record<string, unknown>} />;
  return null;
}

/* ── 1. 关键词竞争分析 ── */

export function KeywordResult({ data, keyword }: { data: any; keyword: string }) {
  const trend = data?.trend?.data ?? data?.trend ?? {};
  const extendsList = rowsOf(data?.extends);
  const searchResults = rowsOf(data?.search_results);
  const detail = data?.detail;
  const hasDetail = detail && detail !== "没有相关数据";

  return (
    <div className="card" style={{ background: "var(--bg2)" }}>
      <div style={{ fontSize: "var(--fs-12)", fontWeight: 600, marginBottom: 10 }}>「{keyword}」关键词分析</div>
      <ErrorList errors={data?.errors} />

      {hasDetail && (
        <div style={{ marginBottom: 12 }}>
          <SectionLabel>关键词详情</SectionLabel>
          {typeof detail === "string"
            ? <div style={{ padding: 10, background: "var(--bg3)", borderRadius: 4 }}><LongText text={detail} /></div>
            : <KVTable obj={(detail.data ?? detail) as Record<string, unknown>} />}
        </div>
      )}

      {trend && Object.keys(trend).length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <SectionLabel>趋势数据</SectionLabel>
          <KVTable obj={trend} />
        </div>
      )}

      {extendsList.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <SectionLabel>扩展关键词（{extendsList.length}）</SectionLabel>
          <div style={{ fontSize: "var(--fs-10)", overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--b)" }}>
                  <th style={{ textAlign: "left", ...cellStyle }}>关键词</th>
                  <th style={{ textAlign: "right", ...cellStyle }}>搜索量</th>
                  <th style={{ textAlign: "right", ...cellStyle }}>竞争度</th>
                </tr>
              </thead>
              <tbody>
                {extendsList.slice(0, 15).map((kw: any, i: number) => (
                  <tr key={i} style={{ borderBottom: "1px solid var(--b)" }}>
                    <td style={{ ...cellStyle, fontFamily: "monospace", wordBreak: "break-word" }}>
                      {typeof kw === "string" ? kw : kw.keyword || kw.关键词 || JSON.stringify(kw)}
                    </td>
                    <td style={{ ...cellStyle, textAlign: "right" }}>{typeof kw === "object" ? (kw.monthly_search_volume || kw.searchVolume || kw.搜索量 || "-") : "-"}</td>
                    <td style={{ ...cellStyle, textAlign: "right" }}>{typeof kw === "object" ? (kw.competition || kw.竞争度 || "-") : "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {searchResults.length > 0 && (
        <div>
          <SectionLabel>搜索结果 Top ASIN（{searchResults.length}）</SectionLabel>
          <div style={{ fontSize: "var(--fs-10)", overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--b)" }}>
                  <th style={{ textAlign: "left", ...cellStyle }}>#</th>
                  <th style={{ textAlign: "left", ...cellStyle }}>ASIN</th>
                  <th style={{ textAlign: "left", ...cellStyle }}>标题</th>
                  <th style={{ textAlign: "right", ...cellStyle }}>价格</th>
                </tr>
              </thead>
              <tbody>
                {searchResults.slice(0, 10).map((item: any, i: number) => (
                  <tr key={i} style={{ borderBottom: "1px solid var(--b)" }}>
                    <td style={{ ...cellStyle, color: "var(--t3)" }}>{i + 1}</td>
                    <td style={{ ...cellStyle, fontFamily: "monospace", fontSize: "var(--fs-9)" }}>{item.asin || item.ASIN || "-"}</td>
                    <td style={{ ...cellStyle, maxWidth: 320, wordBreak: "break-word" }}>{item.title || item.标题 || "-"}</td>
                    <td style={{ ...cellStyle, textAlign: "right" }}>{item.price || item.价格 || "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {!hasDetail && extendsList.length === 0 && searchResults.length === 0 && (
        <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)", padding: 10 }}>暂无数据。Sorftime 可能未收录该关键词。</div>
      )}

      <JsonDetails data={data} />
    </div>
  );
}

/* ── 2. 竞品反查 ── */

export function CompetitorResult({ data, asin }: { data: any; asin: string }) {
  return (
    <div className="card" style={{ background: "var(--bg2)" }}>
      <div style={{ fontSize: "var(--fs-12)", fontWeight: 600, marginBottom: 10 }}>「{asin}」关键词信号分析</div>
      <ErrorList errors={data?.errors} />

      {data?.traffic_terms && (
        <div style={{ marginBottom: 12 }}>
          <SectionLabel>流量关键词</SectionLabel>
          <FlexiList value={rowsOf(data.traffic_terms)} />
        </div>
      )}

      {data?.competitor_keywords && (
        <div style={{ marginBottom: 12 }}>
          <SectionLabel>竞品关键词</SectionLabel>
          {rowsOf(data.competitor_keywords).length > 0 ? (
            <div style={{ fontSize: "var(--fs-10)", overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--b)" }}>
                    <th style={{ textAlign: "left", ...cellStyle }}>关键词</th>
                    <th style={{ textAlign: "right", ...cellStyle }}>排名</th>
                    <th style={{ textAlign: "right", ...cellStyle }}>流量占比</th>
                  </tr>
                </thead>
                <tbody>
                  {rowsOf(data.competitor_keywords).slice(0, 15).map((kw: any, i: number) => (
                    <tr key={i} style={{ borderBottom: "1px solid var(--b)" }}>
                      <td style={{ ...cellStyle, fontFamily: "monospace", wordBreak: "break-word" }}>
                        {typeof kw === "string" ? kw : kw.keyword || kw.关键词 || "-"}
                      </td>
                      <td style={{ ...cellStyle, textAlign: "right" }}>{typeof kw === "object" ? (kw.rank || kw.排名 || "-") : "-"}</td>
                      <td style={{ ...cellStyle, textAlign: "right" }}>{typeof kw === "object" ? (kw.share || kw.流量占比 || "-") : "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <FlexiList value={rowsOf(data.competitor_keywords)} />
          )}
        </div>
      )}

      {data?.product_detail && (
        <div style={{ marginBottom: 12 }}>
          <SectionLabel>产品信息</SectionLabel>
          {typeof data.product_detail === "string"
            ? <LongText text={data.product_detail} />
            : <KVTable obj={data.product_detail} />}
        </div>
      )}

      <JsonDetails data={data} />
    </div>
  );
}

/* ── 3. 流量异动诊断 ── */

export function TrafficResult({ data, asin }: { data: any; asin: string }) {
  return (
    <div className="card" style={{ background: "var(--bg2)" }}>
      <div style={{ fontSize: "var(--fs-12)", fontWeight: 600, marginBottom: 10 }}>「{asin}」流量诊断报告</div>
      <ErrorList errors={data?.errors} />

      {data?.traffic_terms && (
        <div style={{ marginBottom: 12 }}>
          <SectionLabel>流量关键词</SectionLabel>
          <FlexiList value={rowsOf(data.traffic_terms)} max={15} />
        </div>
      )}

      {data?.trend && (
        <div style={{ marginBottom: 12 }}>
          <SectionLabel>销量/流量趋势</SectionLabel>
          {typeof data.trend === "string" ? <LongText text={data.trend} /> : <KVTable obj={data.trend} />}
        </div>
      )}

      {data?.report && (
        <div style={{ marginBottom: 12 }}>
          <SectionLabel>产品报告</SectionLabel>
          {typeof data.report === "string" ? <LongText text={data.report} /> : <KVTable obj={data.report} />}
        </div>
      )}

      <JsonDetails data={data} />
    </div>
  );
}
