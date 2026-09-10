"""自动把一台 MCP 服务器配成可用的数据源。

用户该做的只有两件事：填端点、填密钥。工具叫什么、参数怎么传、返回里哪个字段是
价格 —— 这些都是机器能查出来的，不该让人对着一屏输入框填。

怎么查：
  1. ``tools/list`` 拿到工具清单和每个工具的入参 schema；
  2. 按工具名/描述给每项能力挑一个最像的工具；
  3. **拿样例真调一次**，把返回的 JSON 摊平成「路径 → 值」，再按别名表把对方的字段
     名对到工作台要的字段名上。

第 3 步是关键：光看 schema 猜不出返回字段（绝大多数 MCP 服务器不给 outputSchema），
猜错的代价是卡片照常渲染但数字是空的 —— 静默出错。真调一次，字段是从真实响应里
读出来的，对不上就明确报"这项没配成"，不假装成功。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from app.services import custom_source_mcp as _mcp
from app.services import custom_source_provider as _provider

logger = logging.getLogger("ivyea.services.custom_source_autoconfig")

_MAX_PIPELINE_STEPS = 5

# 摊平返回 JSON 的最大深度。再深的嵌套里几乎不会是卡片要的主字段，
# 继续钻只会让别名撞名的概率变高。
_MAX_DEPTH = 4


# ── 入参：参数名 → 占位符 ────────────────────────────────────────────────────
#
# 键是**规范化后**的参数名（小写、去掉下划线和短横线）。

_PARAM_ALIASES: List[Tuple[Tuple[str, ...], str]] = [
    (("asin", "asins", "itemasin", "productasin", "childasin", "parentasin"), "{asin}"),
    (("keyword", "keywords", "kw", "query", "q", "word", "words", "term", "searchterm",
      "searchword", "productname"), "{keyword}"),
    (("marketplace", "site", "amzsite", "country", "countrycode", "region", "market",
      "keywordsupportsite", "station", "marketplaceid"), "{marketplace}"),
    (("month", "datemonth", "yearmonth"), "{month}"),
    (("startdate", "begindate", "datefrom"), "{days_ago_30}"),
    (("enddate", "dateto", "date", "day", "today"), "{today}"),
    (("nodeid", "nodeidpath", "categoryid", "cid", "node", "category", "categorypath"), "{query}"),
    (("size", "pagesize", "limit", "topn", "top", "num", "count", "rows", "perpage"), "{top_n}"),
    (("page", "pageno", "pagenum", "pageindex", "currentpage"), 1),
]


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9一-鿿]", "", str(name or "").lower())


def _placeholder_for(param: str) -> Any:
    key = _norm(param)
    for names, value in _PARAM_ALIASES:
        if key in names:
            return value
    return None


# ── 返回：目标字段 → 对方可能的字段名 ───────────────────────────────────────
#
# 顺序即优先级。中文名列在这里是因为国内几家数据服务的 MCP 直接返回中文字段。

_FIELD_ALIASES: Dict[str, Tuple[str, ...]] = {
    # 商品
    "title": ("title", "productname", "itemname", "name", "商品标题", "标题"),
    "brand": ("brand", "brandname", "品牌"),
    "image": ("zoomimageurl", "imageurl", "mainimage", "image", "img", "picture", "主图", "图片"),
    "price": ("price", "currentprice", "sellprice", "amount", "avgprice", "价格", "售价"),
    "bsr": ("bsrrank", "bsr", "bigrank", "mainrank", "rank", "ranking", "大类排名", "排名"),
    "bsr_category": ("bsrlabel", "bsrcategory", "maincategory", "categoryname", "大类目", "类目"),
    "sub_rank": ("subrank", "smallrank", "subcategoryrank", "小类排名"),
    "sub_category": ("subcategory", "smallcategory", "sublabel", "小类目"),
    "est_sales": ("monthlysales", "totalunits", "parentunitsales", "childunitsales",
                  "estimatedsales", "unitsales", "sales", "月销量", "销量"),
    "rating": ("rating", "star", "stars", "avgrating", "score", "评分"),
    "review_count": ("reviewcount", "reviewnum", "reviews", "ratings", "评论数", "评价数"),
    "variations": ("variations", "variantcount", "variationcount", "变体数"),
    "coupon": ("coupon", "couponinfo", "优惠券"),
    "deal": ("deal", "dealinfo", "promotion", "促销"),
    "inventory": ("inventory", "stock", "quantity", "库存"),
    # 关键词
    "monthly_search_volume": ("searches", "searchvolume", "monthlysearches", "searchcount",
                              "月搜索量", "搜索量"),
    "recommended_cpc_bid": ("bid", "suggestedbid", "averagecpc", "cpc", "推荐cpc竞价", "竞价"),
    "purchase_rate": ("purchaserate", "buyrate", "conversionrate", "cvr", "购买率", "转化率"),
    "competition_index": ("competitionindex", "competition", "competitiveness", "竞争度"),
    "keyword": ("keywords", "keyword", "word", "term", "searchterm", "关键词"),
    "monthly_search": ("searches", "searchvolume", "monthlysearches", "月搜索量", "搜索量"),
    "cpc": ("bid", "cpc", "averagecpc", "suggestedbid", "竞价"),
    "seasonality": ("marketperiod", "season", "seasonality", "季节性"),
    "evidence_sales": ("purchases", "monthlypurchases", "orders", "月购买量", "购买量"),
    # 类目 / 大盘
    "category_name": ("nodelabelpath", "categoryname", "nodename", "nodelabel", "类目名称", "类目"),
    "node_id": ("nodeid", "node", "categoryid", "类目id"),
    "node_id_path": ("nodeidpath", "categorypath", "类目路径"),
    "avg_price": ("avgprice", "averageprice", "均价", "平均价格"),
    "total_sales": ("totalunits", "totalsales", "sales", "总销量", "销量"),
    "search_volume": ("searches", "searchvolume", "monthlysearches", "搜索量"),
    # 序列
    "day": ("date", "day", "time", "month", "dt", "datetime", "日期", "月份", "时间"),
    "value": ("searchvolume", "searches", "search", "parentunitsales", "childunitsales",
              "units", "sales", "value", "数值"),
    # 单值
    "purchase_evidence": ("purchases", "monthlypurchases", "orders", "月购买量"),
}

# 数值型目标字段：匹配到的样例值必须能转成数字，否则宁可不映射。
# 这一条挡住的是"名字像但类型不对"的假匹配（比如 rank 是 "第 3 名" 这种串）。
_NUMERIC_TARGETS = {
    "price", "bsr", "sub_rank", "est_sales", "rating", "review_count", "variations",
    "monthly_search_volume", "recommended_cpc_bid", "purchase_rate", "competition_index",
    "monthly_search", "cpc", "evidence_sales", "avg_price", "total_sales", "search_volume",
    "value", "purchase_evidence",
}


# ── 能力 → 工具的挑选规则 ───────────────────────────────────────────────────
#
# want / avoid 是**工具名和描述**里的关键词。needs 是这个能力必须能填上的输入。

_CAP_RULES: List[Dict[str, Any]] = [
    {
        "id": "home_asin_pulse", "needs": "asin",
        "want": ("detail", "info", "product", "asin", "详情", "商品"),
        "avoid": ("trend", "history", "review", "keyword", "variation", "趋势", "评论"),
        "kind": "record", "fields": ("title", "brand", "image", "price", "bsr", "bsr_category",
                                     "sub_rank", "sub_category", "est_sales", "rating",
                                     "review_count", "variations", "coupon", "deal", "inventory"),
        "must": ("title",),
    },
    {
        "id": "home_product_trend_series", "needs": "asin",
        "want": ("trend", "history", "sales", "趋势", "历史"),
        "avoid": ("keyword", "关键词"),
        "kind": "series", "row_fields": ("day", "value"), "must": ("day", "value"),
    },
    {
        "id": "home_keyword_pulse", "needs": "keyword",
        "want": ("detail", "research", "info", "keyword", "关键词", "详情"),
        "avoid": ("trend", "extend", "related", "expand", "趋势", "拓展"),
        "kind": "record",
        "fields": ("monthly_search_volume", "recommended_cpc_bid", "purchase_rate",
                   "competition_index"),
        "must": ("monthly_search_volume",),
    },
    {
        "id": "home_keyword_trend_series", "needs": "keyword",
        "want": ("trend", "history", "趋势", "历史"),
        "avoid": ("asin", "product", "商品"),
        "kind": "series", "row_fields": ("day", "value"), "must": ("day", "value"),
    },
    {
        "id": "home_keyword_extends", "needs": "keyword",
        "want": ("extend", "related", "expand", "suggest", "similar", "research",
                 "拓展", "相关", "长尾"),
        "avoid": ("trend", "趋势"),
        "kind": "rows",
        "row_fields": ("keyword", "monthly_search", "cpc", "seasonality", "evidence_sales"),
        "must": ("keyword",),
    },
    {
        "id": "home_keyword_purchase_evidence", "needs": "keyword",
        "want": ("research", "detail", "keyword", "关键词"),
        "avoid": ("trend", "趋势"),
        "kind": "record", "fields": ("purchase_evidence",), "must": ("purchase_evidence",),
        "rename": {"purchase_evidence": "value"},
    },
    {
        "id": "home_category", "needs": "keyword",
        "want": ("category", "node", "market", "top", "rank", "concentration", "report",
                 "类目", "大盘", "排行"),
        "avoid": ("trend", "history", "keyword detail", "趋势"),
        "kind": "rows",
        "row_fields": ("asin", "title", "brand", "image", "price", "bsr", "est_sales",
                       "rating", "review_count"),
        "summary_fields": ("category_name", "node_id", "avg_price", "total_sales"),
        "must": ("asin",),
    },
    {
        "id": "home_market_metrics", "needs": "keyword",
        "want": ("market", "category", "overview", "stat", "report", "大盘", "概览"),
        "avoid": ("trend", "趋势"),
        "kind": "record",
        "fields": ("search_volume", "total_sales", "avg_price", "node_id", "node_id_path",
                   "category_name"),
        "must": (),          # 三个指标一个都没有时由 _score_fields 判空
        "min_fields": 1,
    },
]

_CAP_LABELS = {
    "home_asin_pulse": "ASIN 监控卡片",
    "home_product_trend_series": "ASIN 销量趋势",
    "home_keyword_pulse": "关键词监控卡片",
    "home_keyword_trend_series": "关键词趋势",
    "home_keyword_extends": "拓展词",
    "home_keyword_purchase_evidence": "关键词购买佐证",
    "home_category": "类目大盘",
    "home_market_metrics": "大盘指标",
    "keyword_pipeline": "关键词采集（市场调研 / 打法推荐）",
    "asin_pipeline": "ASIN 采集（市场调研 / 打法推荐）",
}


# ── 工具入参 ────────────────────────────────────────────────────────────────

def _schema_props(schema: Any) -> Dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


def build_args(schema: Any) -> Tuple[Dict[str, Any], List[str]]:
    """按 inputSchema 生成入参模板，返回 (模板, 填不上的必填参数)。

    支持一层对象嵌套：卖家精灵那种 ``{"request": {...}}`` 的包法很常见，
    不认的话它家所有工具都会被判成"参数填不上"而整个跳过。
    """
    args: Dict[str, Any] = {}
    unresolved: List[str] = []
    required = set(schema.get("required") or []) if isinstance(schema, dict) else set()
    for name, spec in _schema_props(schema).items():
        if isinstance(spec, dict) and spec.get("type") == "object" and _schema_props(spec):
            inner, inner_missing = build_args(spec)
            if inner:
                args[name] = inner
            unresolved.extend(inner_missing)
            continue
        value = _placeholder_for(name)
        if value is not None:
            args[name] = value
        elif name in required:
            unresolved.append(name)
    return args, unresolved


def _inputs_of(args: Dict[str, Any]) -> set:
    """模板里用到了哪些占位符（含嵌套）。"""
    found = set()
    for value in args.values():
        if isinstance(value, dict):
            found |= _inputs_of(value)
        elif isinstance(value, str):
            found |= set(re.findall(r"\{(\w+)\}", value))
    return found


# ── 工具打分 ────────────────────────────────────────────────────────────────

def _score_tool(tool: Dict[str, Any], rule: Dict[str, Any]) -> float:
    text = f"{tool.get('name', '')} {tool.get('description', '')}".lower()
    name = str(tool.get("name", "")).lower()
    score = 0.0
    for word in rule["want"]:
        if word in name:
            score += 2.0
        elif word in text:
            score += 1.0
    for word in rule["avoid"]:
        if word in name:
            score -= 3.0
        elif word in text:
            score -= 0.5
    return score


# ── 摊平返回、匹配字段 ──────────────────────────────────────────────────────

def flatten(node: Any, prefix: str = "", depth: int = 0) -> Dict[str, Any]:
    """把嵌套结构摊成 ``{"a.b[0].c": 值}``。列表只取第 0 项当代表。"""
    out: Dict[str, Any] = {}
    if depth > _MAX_DEPTH:
        return out
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, (dict, list)):
                out.update(flatten(value, path, depth + 1))
            else:
                out[path] = value
    elif isinstance(node, list) and node:
        out.update(flatten(node[0], f"{prefix}[0]", depth + 1))
    return out


def _is_number(value: Any) -> bool:
    return _provider._num(value) is not None


def match_fields(sample: Any, targets: Tuple[str, ...]) -> Tuple[Dict[str, str], List[str]]:
    """按别名表把样例返回里的字段对到目标字段上，返回 (映射, 没对上的目标)。"""
    flat = flatten(sample)
    # 浅的优先：同名字段出现在多层时，外层几乎总是主字段。
    ordered = sorted(flat.items(), key=lambda kv: (kv[0].count("."), len(kv[0])))
    mapping: Dict[str, str] = {}
    missing: List[str] = []
    for target in targets:
        aliases = _FIELD_ALIASES.get(target, (target,))
        hit = None
        for alias in aliases:          # 别名顺序即优先级，先命中先用
            for path, value in ordered:
                leaf = _norm(path.split(".")[-1].split("[")[0])
                if leaf != alias:
                    continue
                if value is None or value == "":
                    continue
                if target in _NUMERIC_TARGETS and not _is_number(value):
                    continue
                hit = path
                break
            if hit:
                break
        if hit:
            mapping[target] = hit
        else:
            missing.append(target)
    return mapping, missing


# ── 主流程 ──────────────────────────────────────────────────────────────────

def _eligible_tools(rule: Dict[str, Any], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把工具按"为什么用不上"分好类。

    上一版这里只回一句"没有找到合适的工具"，把三种完全不同的情况糊在一起：
    真没有这类工具 / 有但必填参数认不出 / 有而且参数也没问题、只是名字和描述里
    没有我认得的词。第三种明明是"它有，我没认出来"，说成"没有"会让人以为
    这台服务器不支持，直接放弃。
    """
    fits: List[Dict[str, Any]] = []          # 吃对输入、参数也填得上的
    scored: List[tuple] = []                 # 其中名字/描述还能对上号的
    unfillable: List[str] = []               # 有必填参数认不出来，调不动
    for tool in tools:
        args, unresolved = build_args(tool.get("inputSchema"))
        if unresolved:
            unfillable.append(str(tool.get("name")))
            continue
        inputs = _inputs_of(args)
        if rule["needs"] == "asin" and "asin" not in inputs:
            continue
        if rule["needs"] == "keyword" and not ({"keyword", "query"} & inputs):
            continue
        fits.append({"tool": str(tool.get("name")), "args": args,
                     "description": str(tool.get("description") or "")[:120]})
        score = _score_tool(tool, rule)
        if score > 0:
            scored.append((score, tool, args))
    scored.sort(key=lambda c: -c[0])
    return {"fits": fits, "scored": scored, "unfillable": unfillable}


def _no_tool_reason(rule: Dict[str, Any], pool: Dict[str, Any]) -> Dict[str, Any]:
    """候选为空时，说清到底是哪一种没有。"""
    what = "ASIN" if rule["needs"] == "asin" else "关键词"
    if pool["fits"]:
        # 有工具、参数也填得上，纯粹是名字/描述里没有认得出的线索。
        # 这种情况必须把工具名端出来让用户自己挑 —— 它是"我没认出来"，不是"没有"。
        return {
            "reason": "not_recognized",
            "error": f"有 {len(pool['fits'])} 个吃「{what}」的工具，但从名字和描述认不出哪个是干这件事的",
            "candidates": pool["fits"][:8],
        }
    if pool["unfillable"]:
        return {
            "reason": "unfillable",
            "error": (f"没有吃「{what}」的工具；另有 "
                      f"{len(pool['unfillable'])} 个工具的必填参数认不出该填什么"
                      f"（{'、'.join(pool['unfillable'][:3])}…），跳过了"),
            "candidates": [],
        }
    return {"reason": "no_tool", "error": f"这台服务器没有吃「{what}」的工具", "candidates": []}


def _build_spec(rule: Dict[str, Any], payload: Any, tool_name: str,
                args: Dict[str, Any], envelope: str) -> Dict[str, Any]:
    """按真实返回生成映射，并判断够不够用。"""
    spec: Dict[str, Any] = {"tool": tool_name, "args": args}
    if rule["kind"] == "record":
        sample = _mcp.record(payload, envelope)
        mapping, missing = match_fields(sample, rule["fields"])
        for src, dst in (rule.get("rename") or {}).items():
            if src in mapping:
                mapping[dst] = mapping.pop(src)
        missing = [(rule.get("rename") or {}).get(m, m) for m in missing]
        spec["fields"] = mapping
        matched = mapping
    else:
        rows = _mcp.rows(payload, envelope)
        sample = rows[0] if rows else {}
        mapping, missing = match_fields(sample, rule["row_fields"])
        spec["row_fields"] = mapping
        matched = mapping
        if rule.get("summary_fields"):
            summary, _ = match_fields(_mcp.record(payload, envelope), rule["summary_fields"])
            if summary:
                spec["summary_fields"] = summary

    must = [m for m in rule.get("must", ()) if m not in matched]
    must = [(rule.get("rename") or {}).get(m, m) for m in must]
    enough = not must and len(matched) >= rule.get("min_fields", 1)
    return {"spec": spec, "matched": matched, "missing": missing, "must": must, "enough": enough}


async def _try_capability(
    state: Dict[str, Any], rule: Dict[str, Any], tools: List[Dict[str, Any]],
    variables: Dict[str, Any], envelope: str,
) -> Dict[str, Any]:
    """给一项能力挑工具、真调一次、按真实返回生成映射。"""
    cap_id = rule["id"]
    label = _CAP_LABELS[cap_id]
    pool = _eligible_tools(rule, tools)
    if not pool["scored"]:
        return {"id": cap_id, "label": label, "ok": False, **_no_tool_reason(rule, pool)}

    last_error = ""
    last_reason = "no_data"
    # 只试前三名：再往下分数已经很低，每试一个都是一次真实调用（要花钱/配额）。
    for _score, tool, args in pool["scored"][:3]:
        name = str(tool.get("name"))
        try:
            payload = await _mcp.call_tool(state, name, _provider.render_args(args, variables))
        except Exception as exc:              # noqa: BLE001 — 换下一个候选继续试
            last_error = f"调用 {name} 失败：{exc}"
            last_reason = "call_failed"
            continue

        built = _build_spec(rule, payload, name, args, envelope)
        if not built["enough"]:
            last_error = (f"{name} 调通了，但返回里认不出"
                          + "、".join(built["must"] or ["需要的字段"]))
            last_reason = "no_data"
            continue

        return {"id": cap_id, "label": label, "ok": True, "tool": name,
                "spec": built["spec"], "matched": len(built["matched"]),
                "missing": built["missing"]}

    # 试过但都不成：把还没试过的工具一并给出来，用户可以自己指一个。
    tried = {str(t.get("name")) for _s, t, _a in pool["scored"][:3]}
    rest = [f for f in pool["fits"] if f["tool"] not in tried]
    return {"id": cap_id, "label": label, "ok": False,
            "reason": last_reason,
            "error": last_error or "候选工具都没返回可用数据",
            "candidates": rest[:8]}


async def remap_capability(cfg: Dict[str, Any], cap_id: str, tool_name: str,
                           sample_keyword: str, sample_asin: str,
                           marketplace: str = "US") -> Dict[str, Any]:
    """用**指定的**工具重新推断某一项能力的映射。

    自动挑错了、或者压根没认出来时的补救口：用户从工具清单里指一个，字段映射
    仍然由系统按真实返回推断 —— 让人挑工具是合理的，让人逐个填字段路径不是。
    """
    rule = next((r for r in _CAP_RULES if r["id"] == cap_id), None)
    if not rule:
        raise _mcp.CustomSourceError(f"{cap_id} 不支持指定工具（采集类能力请在下面直接加步骤）")
    variables = _provider.template_vars(
        keyword=sample_keyword, query=sample_keyword, asin=sample_asin,
        marketplace=marketplace, top_n=30, size=30,
    )
    envelope = str(cfg.get("envelope") or "")
    async with _mcp.session(cfg) as state:
        tools = await _mcp.list_tools(state)
        tool = next((t for t in tools if str(t.get("name")) == tool_name), None)
        if not tool:
            raise _mcp.CustomSourceError(f"这台服务器上没有名为 {tool_name} 的工具")
        args, unresolved = build_args(tool.get("inputSchema"))
        if unresolved:
            raise _mcp.CustomSourceError(
                f"{tool_name} 的必填参数 {'、'.join(unresolved)} 认不出该填什么，"
                "只能在下面手动写入参模板")
        payload = await _mcp.call_tool(
            state, tool_name, _provider.render_args(args, variables))

    built = _build_spec(rule, payload, tool_name, args, envelope)
    return {
        "ok": built["enough"],
        "spec": built["spec"],
        "matched": len(built["matched"]),
        "missing": built["missing"],
        "error": None if built["enough"]
        else f"{tool_name} 的返回里认不出" + "、".join(built["must"] or ["需要的字段"]),
    }


def _pipeline_steps(tools: List[Dict[str, Any]], want_input: str) -> List[Dict[str, Any]]:
    """采集管道：把所有"参数填得上、且吃这个输入"的工具排进去。

    这里不做字段映射 —— 采集结果是整包交给 AI 写报告的，AI 直接读原始 JSON。
    所以宁可多带几个工具，信息多一点报告就厚一点。
    """
    steps = []
    for tool in tools:
        args, unresolved = build_args(tool.get("inputSchema"))
        if unresolved:
            continue
        inputs = _inputs_of(args)
        if want_input == "asin" and "asin" not in inputs:
            continue
        if want_input == "keyword" and ("asin" in inputs or not ({"keyword", "query"} & inputs)):
            continue
        description = str(tool.get("description") or "").strip()
        label = re.split(r"[。\n.;；,，]", description)[0][:16] or str(tool.get("name"))
        steps.append({"label": label, "tool": str(tool.get("name")), "args": args})
        if len(steps) >= _MAX_PIPELINE_STEPS:
            break
    return steps


def _pick_probe_tool(tools: List[Dict[str, Any]]) -> Optional[Tuple[str, Dict[str, Any]]]:
    """挑一个参数填得上的工具，用来验鉴权。优先参数少的 —— 少一个参数就少一种
    "调不通其实是参数不对"的干扰。"""
    candidates = []
    for tool in tools:
        args, unresolved = build_args(tool.get("inputSchema"))
        if unresolved:
            continue
        candidates.append((len(args), str(tool.get("name")), args))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1], candidates[0][2]


async def autoconfigure(cfg: Dict[str, Any], sample_keyword: str,
                        sample_asin: str, marketplace: str = "US") -> Dict[str, Any]:
    """探测 + 试调 + 生成映射。返回补好 capabilities/surfaces 的配置和一份人话报告。"""
    envelope = str(cfg.get("envelope") or "")
    variables = _provider.template_vars(
        keyword=sample_keyword, query=sample_keyword, asin=sample_asin,
        marketplace=marketplace, top_n=30, size=30,
    )
    results: List[Dict[str, Any]] = []
    capabilities: Dict[str, Any] = {}
    auth_note = ""

    async with _mcp.session(cfg) as state:
        tools = await _mcp.list_tools(state)
        if not tools:
            raise _mcp.CustomSourceError("这台服务器没有返回任何工具")

    # 鉴权写法没定下来就先试出来。用户界面上不问"鉴权方式"和"参数名" ——
    # 那是机器试几次就能知道的事，不该占两个输入框还让人猜。
    if str((cfg.get("auth") or {}).get("mode") or "auto") == "auto":
        probe = _pick_probe_tool(tools)
        if not probe:
            raise _mcp.CustomSourceError("这台服务器的工具都有认不出的必填参数，没法自动配置")
        tool_name, probe_args = probe
        found = await _mcp.detect_auth(
            cfg, tool_name, _provider.render_args(probe_args, variables))
        if not found:
            raise _mcp.CustomSourceError(
                "密钥试了几种常见的传法都没通过。确认密钥没填错；"
                "如果这台服务器用的是别的传法，可以在「高级设置」里手动指定")
        cfg = {**cfg, "auth": {**found, "value": (cfg.get("auth") or {}).get("value", "")}}
        auth_note = _mcp.auth_label(found)

    async with _mcp.session(cfg) as state:

        for rule in _CAP_RULES:
            outcome = await _try_capability(state, rule, tools, variables, envelope)
            results.append(outcome)
            if outcome.get("ok"):
                capabilities[outcome["id"]] = outcome["spec"]

    for cap_id, want in (("keyword_pipeline", "keyword"), ("asin_pipeline", "asin")):
        steps = _pipeline_steps(tools, want)
        if steps:
            capabilities[cap_id] = {"steps": steps}
            results.append({"id": cap_id, "label": _CAP_LABELS[cap_id], "ok": True,
                            "tool": "、".join(s["tool"] for s in steps),
                            "matched": len(steps), "missing": []})
        else:
            results.append({"id": cap_id, "label": _CAP_LABELS[cap_id], "ok": False,
                            "error": "没有找到吃这类输入的工具"})

    from app.services import custom_source_registry as _registry
    surfaces = _registry.derive_surfaces(capabilities)

    return {
        "source": {**cfg, "capabilities": capabilities, "surfaces": surfaces},
        "report": {
            "tools": len(tools),
            "capabilities": results,
            "surfaces": surfaces,
            "auth": auth_note,
            "ok": bool(capabilities),
        },
    }
