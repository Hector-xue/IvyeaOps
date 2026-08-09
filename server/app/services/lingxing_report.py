"""领星 操作前可视化报告 — a self-contained HTML report for an operation ticket.

Assembles: the change (before→after), the target campaign's recent performance
(KPIs + an inline SVG trend, pulled via the cache-friendly read layer), the
points worth optimising (rule-derived + the proposal's rationale), the reasoning,
the three independent reviews, and the deterministic guardrail report. No
external deps — opens in any browser and prints to PDF.
"""
from __future__ import annotations

import logging

import html as _html
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.services import lingxing_data as _data
from app.services import lingxing_operate as _op
from app.services import lingxing_service as _gw

logger = logging.getLogger("ivyea.services.lingxing_report")

# minimal store-country → currency code (for labelling amounts in the report)
_CCY = {
    "美国": "USD", "加拿大": "CAD", "墨西哥": "MXN", "巴西": "BRL", "英国": "GBP",
    "德国": "EUR", "法国": "EUR", "意大利": "EUR", "西班牙": "EUR", "荷兰": "EUR",
    "比利时": "EUR", "爱尔兰": "EUR", "瑞典": "SEK", "波兰": "PLN", "土耳其": "TRY",
    "日本": "JPY", "澳大利亚": "AUD", "新加坡": "SGD", "印度": "INR",
    "阿联酋": "AED", "沙特阿拉伯": "SAR", "埃及": "EGP",
}


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _e(v: Any) -> str:
    return _html.escape("" if v is None else str(v))


async def _store_ccy(sid: int) -> str:
    try:
        s = await _data.fetch_dataset("sellers")
        for r in (s.get("rows") or []):
            if str(r.get("sid")) == str(sid):
                return _CCY.get(r.get("country") or "", "")
    except Exception:
        logger.debug("s = await _data.fetch_dataset 失败（旁路，已忽略）", exc_info=True)
    return ""


async def _resolve_campaign_id(intent: Dict[str, Any]) -> Optional[str]:
    ot = intent.get("op_type")
    op = _op.OP_TYPES.get(ot or "")
    if not op:
        return None
    if ot == "campaign_budget":
        return str(intent.get("target_id"))
    if op["category"] == "add":
        return str(intent.get("campaign_id")) if intent.get("campaign_id") else None
    # bid ops: look up the entity to find its campaign_id
    ds = op.get("snapshot_dataset")
    if not ds:
        return None
    try:
        for offset in range(0, 2000, 300):
            res = await _data.fetch_dataset(ds, {"sid": int(intent["sid"]), "length": 300, "offset": offset}, force=True)
            rows = res.get("rows") or []
            for c in rows:
                if str(c.get(op["snapshot_id"])) == str(intent["target_id"]):
                    return str(c.get("campaign_id"))
            if len(rows) < 300:
                break
    except Exception:
        logger.debug("res = await _data.fetch_dataset 失败（旁路，已忽略）", exc_info=True)
    return None


async def _campaign_metrics(sid: int, campaign_id: str, days: int = 14) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    trend: List[Dict[str, Any]] = []
    tot = {"spend": 0.0, "sales": 0.0, "orders": 0.0, "clicks": 0.0, "impressions": 0.0}
    for d in range(days, 0, -1):
        day = (datetime.now(timezone.utc) - timedelta(days=d)).strftime("%Y-%m-%d")
        try:
            rep = await _data.fetch_dataset(
                "sp_campaign_report", {"sid": sid, "report_date": day, "length": 300}, ttl=7 * 86400)
        except Exception:
            continue
        sp = sa = od = ck = im = 0.0
        for r in (rep.get("rows") or []):
            if str(r.get("campaign_id")) != str(campaign_id):
                continue
            sp += _f(r.get("cost")); sa += _f(r.get("sales")); od += _f(r.get("orders"))
            ck += _f(r.get("clicks")); im += _f(r.get("impressions"))
        trend.append({"date": day, "spend": sp, "sales": sa, "orders": od, "clicks": ck, "impressions": im,
                      "acos": (sp / sa) if sa else None})
        tot["spend"] += sp; tot["sales"] += sa; tot["orders"] += od; tot["clicks"] += ck; tot["impressions"] += im
    s, sa, ck, im, od = tot["spend"], tot["sales"], tot["clicks"], tot["impressions"], tot["orders"]
    tot.update({"acos": (s / sa) if sa else None, "roas": (sa / s) if s else None,
                "ctr": (ck / im) if im else None, "cvr": (od / ck) if ck else None})
    return trend, tot


def _svg_trend(trend: List[Dict[str, Any]]) -> str:
    pts = [t for t in trend if t["spend"] or t["sales"]]
    if len(pts) < 2:
        return '<div class="muted">窗口内无足够数据绘制趋势</div>'
    W, H, P = 720, 200, 28
    mx = max(max(t["spend"] for t in pts), max(t["sales"] for t in pts)) or 1
    n = len(pts)
    def x(i): return P + i * (W - 2 * P) / (n - 1)
    def y(v): return H - P - (v / mx) * (H - 2 * P)
    def line(key, color):
        d = " ".join(f"{'M' if i == 0 else 'L'}{x(i):.1f},{y(t[key]):.1f}" for i, t in enumerate(pts))
        return f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2"/>'
    grid = "".join(f'<line x1="{P}" x2="{W-P}" y1="{P+k*(H-2*P)/3:.0f}" y2="{P+k*(H-2*P)/3:.0f}" stroke="#eee"/>' for k in range(4))
    xlab = "".join(f'<text x="{x(i):.0f}" y="{H-8}" font-size="9" fill="#999" text-anchor="middle">{t["date"][5:]}</text>'
                   for i, t in enumerate(pts) if i % max(1, n // 7) == 0)
    return (f'<svg viewBox="0 0 {W} {H}" width="100%">{grid}{line("spend","#e2574c")}{line("sales","#3aa757")}{xlab}'
            f'<text x="{P}" y="16" font-size="10" fill="#e2574c">花费</text>'
            f'<text x="{P+44}" y="16" font-size="10" fill="#3aa757">销售额</text></svg>')


def _pct(v): return "—" if v is None else f"{v*100:.1f}%"
def _n(v): return "—" if v is None else f"{float(v):,.0f}"
def _m(v, ccy): return "—" if v is None else f"{float(v):,.2f} {ccy}".strip()
def _roas(v): return "—" if v is None else f"{float(v):.2f}"


def _optimization_points(tot: Dict[str, Any], intent: Dict[str, Any]) -> List[str]:
    pts: List[str] = []
    acos = tot.get("acos")
    if acos is not None and acos > 0.35:
        pts.append(f"ACOS 偏高（{_pct(acos)}），存在较多无效广告花费，需控本或优化转化。")
    elif acos is not None and acos < 0.15 and tot.get("spend"):
        pts.append(f"ACOS 较低（{_pct(acos)}），表现健康，存在放量空间。")
    if tot.get("ctr") is not None and tot["ctr"] < 0.003 and tot.get("impressions"):
        pts.append(f"点击率偏低（{_pct(tot['ctr'])}），素材/相关性或出价位置可能有问题。")
    if tot.get("cvr") is not None and tot["cvr"] < 0.05 and tot.get("clicks"):
        pts.append(f"转化率偏低（{_pct(tot['cvr'])}），落地页/词相关性或受众需优化。")
    sp = (intent.get("source_proposal") or {})
    if sp.get("rationale"):
        pts.append(sp["rationale"])
    if not pts:
        pts.append("窗口内数据有限，详见下方依据与复核结论。")
    return pts


def _reviews_html(reviews: Dict[str, Any]) -> str:
    pmap = {"deepseek": "DeepSeek", "apimart": "Claude", "fallback": "兜底", "none": "不可用"}
    rows = ""
    for r in (reviews or {}).get("reviews", []):
        ok = "通过" if r.get("approve") else "否决"
        cls = "ok" if r.get("approve") else "bad"
        rows += (f'<tr><td>{_e(r.get("reviewer"))}</td><td>{_e(pmap.get(r.get("provider"), r.get("provider")))}</td>'
                 f'<td class="{cls}">{ok}</td><td>{int((r.get("risk_score") or 0)*100)}%</td>'
                 f'<td>{_e(r.get("reasons"))}</td></tr>')
    verdict = "全部通过 ✓" if (reviews or {}).get("approved") else "未全部通过 ✗"
    return (f'<p><b>结论：</b><span class="{"ok" if (reviews or {}).get("approved") else "bad"}">{verdict}</span> '
            f'（最高风险 {int((reviews or {}).get("max_risk", 0)*100)}%）</p>'
            f'<table><tr><th>复核员</th><th>模型</th><th>结论</th><th>风险</th><th>理由</th></tr>{rows}</table>')


def _guardrail_html(guard: Dict[str, Any]) -> str:
    rows = "".join(f'<tr><td>{_e(c.get("name"))}</td><td class="{"ok" if c.get("ok") else "bad"}">'
                   f'{"✓" if c.get("ok") else "✗"}</td><td>{_e(c.get("detail"))}</td></tr>'
                   for c in (guard or {}).get("checks", []))
    return f'<table><tr><th>护栏项</th><th></th><th>说明</th></tr>{rows}</table>'


def _change_desc(intent: Dict[str, Any], ccy: str) -> str:
    op = _op.OP_TYPES.get(intent.get("op_type") or "", {})
    if op.get("category") == "add":
        return (f'新增「{_e(intent.get("keyword_text"))}」（{_e(intent.get("match_type"))}），'
                f'活动 {_e(intent.get("campaign_id"))}'
                + (f'，竞价 {_m(intent.get("bid"), ccy)}' if intent.get("bid") is not None else ''))
    ch = intent.get("change") or {}
    bf = intent.get("before") or {}
    parts = []
    for k, lbl in (("daily_budget", "日预算"), ("bid", "竞价"), ("defaultBid", "默认竞价")):
        if ch.get(k) is not None:
            parts.append(f'{lbl} {_m(bf.get(k), ccy)} → <b>{_m(ch.get(k), ccy)}</b>'
                         + (f'（{intent.get("change_pct")}%）' if intent.get("change_pct") is not None else ''))
    if ch.get("state"):
        parts.append(f'状态 {_e(bf.get("state"))} → <b>{_e(ch.get("state"))}</b>')
    return "；".join(parts) or "—"


async def build_report_html(tid: str) -> str:
    t = _op.get_ticket(tid)
    if not t:
        raise _gw.LingXingError("未找到工单")
    intent = t.get("intent") or {}
    sid = intent.get("sid")
    ccy = await _store_ccy(int(sid)) if sid else ""
    cid = await _resolve_campaign_id(intent)
    trend, tot = ([], {})
    if cid and sid:
        try:
            trend, tot = await _campaign_metrics(int(sid), cid, 14)
        except Exception:
            trend, tot = [], {}

    opt = intent.get("opt") or {}
    opt_html = ""
    if opt:
        opt_html = (f'<div class="box" style="margin-top:8px">'
                    f'<b>命中规则：</b>{_e(opt.get("rule"))}<br>'
                    f'<b>显著性：</b>{_e(opt.get("significance"))}<br>'
                    f'<b>目标 ACOS：</b>{_pct(opt.get("target_acos"))} · '
                    f'<b>盈亏平衡 ACOS：</b>{_pct(opt.get("breakeven_acos"))} · '
                    f'<b>杠杆：</b>{_e(opt.get("lever"))}</div>')

    sp = intent.get("source_proposal") or {}
    why_bits = []
    if intent.get("rationale"):
        why_bits.append(f'<p>{_e(intent["rationale"])}</p>')
    if sp.get("expected_impact"):
        why_bits.append(f'<p><b>预期影响：</b>{_e(sp["expected_impact"])}</p>')
    if sp.get("confidence") is not None:
        why_bits.append(f'<p><b>提案置信度：</b>{int(_f(sp["confidence"])*100)}%</p>')
    opt = "".join(f"<li>{_e(p)}</li>" for p in _optimization_points(tot, intent))

    kpi = ""
    if tot:
        kpi = (f'<div class="kpis">'
               f'<div class="k"><span>花费</span><b>{_m(tot.get("spend"), ccy)}</b></div>'
               f'<div class="k"><span>销售额</span><b>{_m(tot.get("sales"), ccy)}</b></div>'
               f'<div class="k"><span>ACOS</span><b>{_pct(tot.get("acos"))}</b></div>'
               f'<div class="k"><span>ROAS</span><b>{_roas(tot.get("roas"))}</b></div>'
               f'<div class="k"><span>订单</span><b>{_n(tot.get("orders"))}</b></div>'
               f'<div class="k"><span>CTR/CVR</span><b>{_pct(tot.get("ctr"))} / {_pct(tot.get("cvr"))}</b></div>'
               f'</div>')

    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>领星操作报告 {_e(tid)}</title><style>
*{{box-sizing:border-box}} body{{font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;color:#222;max-width:860px;margin:24px auto;padding:0 20px;line-height:1.6}}
h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:14px;margin:22px 0 8px;border-left:3px solid #16a34a;padding-left:8px}}
.muted{{color:#888;font-size:12px}} .meta{{color:#666;font-size:12px;margin-bottom:8px}}
table{{width:100%;border-collapse:collapse;font-size:12px;margin-top:6px}} th,td{{border:1px solid #e5e5e5;padding:6px 8px;text-align:left;vertical-align:top}} th{{background:#fafafa}}
.ok{{color:#16a34a}} .bad{{color:#dc2626}}
.kpis{{display:flex;flex-wrap:wrap;gap:10px;margin:8px 0}} .k{{border:1px solid #eee;border-radius:6px;padding:8px 12px;min-width:110px}} .k span{{display:block;color:#888;font-size:11px}} .k b{{font-size:16px}}
.box{{background:#f6fbf7;border:1px solid #d6ecdb;border-radius:6px;padding:10px 14px}}
ul{{margin:6px 0;padding-left:20px}} li{{margin:3px 0}}
@media print{{body{{margin:0}}}}
</style></head><body>
<h1>领星广告操作报告</h1>
<div class="meta">工单 {_e(tid)} · 生成 {datetime.now().strftime('%Y-%m-%d %H:%M')} · 状态 <b>{_e(t.get('status'))}</b></div>
<div class="box"><b>{_e(intent.get('op_label') or intent.get('op_type'))}</b> · 店铺 {_e(sid)} · 目标 {_e(intent.get('target_name') or intent.get('target_id') or intent.get('keyword_text'))}
<br>操作内容：{_change_desc(intent, ccy)}</div>

<h2>① 操作前数据情况{(' · 活动 ' + _e(cid)) if cid else ''}（近14天）</h2>
{kpi or '<div class="muted">未能定位到该目标的活动层报表数据（可能为新词/无投放/测试账号无数据）。</div>'}
{_svg_trend(trend) if trend else ''}

<h2>② 需要优化的点</h2>
<ul>{opt}</ul>

<h2>③ 为什么这么操作</h2>
{opt_html}
{''.join(why_bits) or '<p class="muted">无额外说明。</p>'}

<h2>④ 三重独立复核</h2>
{_reviews_html(t.get('reviews') or {})}

<h2>⑤ 确定性护栏</h2>
{_guardrail_html(t.get('guardrail') or {})}

<div class="muted" style="margin-top:24px">本报告由 IvyeaOps 领星模块自动生成；操作经 三重复核 + 护栏 + 人工确认后执行，执行前抓取回滚快照。</div>
</body></html>"""
