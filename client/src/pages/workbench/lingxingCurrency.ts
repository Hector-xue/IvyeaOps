// Map a LingXing store to its ad-budget currency. Budgets come back in the
// marketplace's native currency with no currency code on the row, so we derive
// it from the seller's `country` (reliable, Chinese name) with the country code
// in `name`/`region` as a fallback. Unknown → no symbol (never a wrong one).

export type Cur = { sym: string; code: string };

const BY_COUNTRY_CN: Record<string, Cur> = {
  "美国": { sym: "$", code: "USD" }, "加拿大": { sym: "C$", code: "CAD" },
  "墨西哥": { sym: "MX$", code: "MXN" }, "巴西": { sym: "R$", code: "BRL" },
  "英国": { sym: "£", code: "GBP" },
  "德国": { sym: "€", code: "EUR" }, "法国": { sym: "€", code: "EUR" },
  "意大利": { sym: "€", code: "EUR" }, "西班牙": { sym: "€", code: "EUR" },
  "荷兰": { sym: "€", code: "EUR" }, "比利时": { sym: "€", code: "EUR" },
  "爱尔兰": { sym: "€", code: "EUR" },
  "瑞典": { sym: "kr", code: "SEK" }, "波兰": { sym: "zł", code: "PLN" },
  "土耳其": { sym: "₺", code: "TRY" },
  "日本": { sym: "¥", code: "JPY" }, "澳大利亚": { sym: "A$", code: "AUD" },
  "新加坡": { sym: "S$", code: "SGD" }, "印度": { sym: "₹", code: "INR" },
  "阿联酋": { sym: "AED ", code: "AED" }, "沙特阿拉伯": { sym: "SAR ", code: "SAR" },
  "埃及": { sym: "EGP ", code: "EGP" },
};

const BY_CODE: Record<string, Cur> = {
  US: BY_COUNTRY_CN["美国"], CA: BY_COUNTRY_CN["加拿大"], MX: BY_COUNTRY_CN["墨西哥"],
  BR: BY_COUNTRY_CN["巴西"], UK: BY_COUNTRY_CN["英国"], GB: BY_COUNTRY_CN["英国"],
  DE: BY_COUNTRY_CN["德国"], FR: BY_COUNTRY_CN["法国"], IT: BY_COUNTRY_CN["意大利"],
  ES: BY_COUNTRY_CN["西班牙"], NL: BY_COUNTRY_CN["荷兰"], BE: BY_COUNTRY_CN["比利时"],
  IE: BY_COUNTRY_CN["爱尔兰"], SE: BY_COUNTRY_CN["瑞典"], PL: BY_COUNTRY_CN["波兰"],
  TR: BY_COUNTRY_CN["土耳其"], JP: BY_COUNTRY_CN["日本"], AU: BY_COUNTRY_CN["澳大利亚"],
  SG: BY_COUNTRY_CN["新加坡"], IN: BY_COUNTRY_CN["印度"], AE: BY_COUNTRY_CN["阿联酋"],
  SA: BY_COUNTRY_CN["沙特阿拉伯"], EG: BY_COUNTRY_CN["埃及"],
};

function curOfSeller(s: any): Cur | undefined {
  if (!s) return undefined;
  if (s.country && BY_COUNTRY_CN[s.country]) return BY_COUNTRY_CN[s.country];
  // fallback: trailing code in name like "欧洲-UK" / "日本-JP", or region
  const tail = String(s.name || "").split(/[-－]/).pop()?.trim().toUpperCase();
  if (tail && BY_CODE[tail]) return BY_CODE[tail];
  const reg = String(s.region || "").toUpperCase();
  if (BY_CODE[reg]) return BY_CODE[reg];
  return undefined;
}

/** Build sid -> currency from a sellers list. */
export function sidCurrencyMap(sellers: any[]): Record<string, Cur> {
  const m: Record<string, Cur> = {};
  for (const s of sellers || []) {
    const c = curOfSeller(s);
    if (c && s.sid != null) m[String(s.sid)] = c;
  }
  return m;
}

/** Format a budget number with the store's currency symbol (no symbol if unknown).
 *  Thousands-separated to match the rest of the panel's number formatting. */
export function fmtBudget(v: any, cur?: Cur): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  const s = Number.isFinite(n) ? n.toLocaleString("en-US", { maximumFractionDigits: 2 }) : String(v);
  return cur ? `${cur.sym}${s}` : s;
}

/**
 * 币种代码 → 符号。
 *
 * 和上面那张 `BY_COUNTRY_CN` 的区别是**数据来源**：领星大盘只能从 seller 的
 * country/name 去**推**币种（行里没有币种字段），而 `/api/cockpit/ads` 的
 * by_store / by_campaign 里后端直接给了 `currency`（ISO 代码）。有确切值的时候
 * 就别再推 —— 推错一次，界面上就是一个标着 $ 的欧元数字。
 *
 * 认不出来的代码返回 undefined，`fmtMoney` 会退化成不带符号的纯数字：
 * **宁可没有符号，也不能有一个错的符号。**
 */
const SYMBOL_BY_CODE: Record<string, string> = {
  USD: "$", CAD: "C$", MXN: "MX$", BRL: "R$", GBP: "£", EUR: "€",
  SEK: "kr", PLN: "zł", TRY: "₺", JPY: "¥", AUD: "A$", SGD: "S$",
  INR: "₹", AED: "AED ", SAR: "SAR ", EGP: "EGP ",
};

export function symbolOf(code?: string | null): string | undefined {
  if (!code) return undefined;
  return SYMBOL_BY_CODE[String(code).toUpperCase()];
}

/** 按币种代码格式化金额；代码认不出来就只给数字，不给符号。 */
export function fmtMoney(v: any, code?: string | null, digits = 2): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  const s = n.toLocaleString("en-US", { maximumFractionDigits: digits });
  const sym = symbolOf(code);
  return sym ? `${sym}${s}` : s;
}
