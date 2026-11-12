"""Listing 采集解析 Item Highlights（2026-07-27 新规字段，≤125 字符）。

fixture 取自真实 Amazon PDP（B09XS7JWHH 桌面页，2026-11 抓取）的规格表片段：
亮点作为 <th>Item Highlight</th><td>值</td> 一行出现在 productDetails 规格表里。
"""
import re

from app.routers.listing.scrape import _parse_amazon_html

# 真实页面结构（Sony WH-1000XM5 规格表节选，值 116 字符）：
REAL_SNIPPET = """
<table id="productDetails_techSpec_section_1" class="a-keyvalue prodDetTable" role="presentation">
 <tr>
  <th class="a-color-secondary a-size-base prodDetSectionEntry">    Package Type   </th>
  <td class="a-size-base prodDetAttrValue">    FFP   </td> </tr>
 <tr>
  <th class="a-color-secondary a-size-base prodDetSectionEntry">    Item Highlight   </th>
  <td class="a-size-base prodDetAttrValue">    Auto NC Optimizer, Up to 30 Hrs of Battery,
   Carrying Case, Crystal Clear Calls, Touch Control, Multipoint Connection   </td> </tr>
 <tr>
  <th class="a-color-secondary a-size-base prodDetSectionEntry">    Manufacturer   </th>
  <td class="a-size-base prodDetAttrValue">    Sony   </td> </tr>
</table>
"""

EXPECTED = ("Auto NC Optimizer, Up to 30 Hrs of Battery, Carrying Case, "
            "Crystal Clear Calls, Touch Control, Multipoint Connection")


def test_parse_extracts_item_highlight_from_prod_det_table():
    parsed = _parse_amazon_html(REAL_SNIPPET)
    assert parsed["highlights"] == EXPECTED


def test_parse_item_highlight_html_entities_and_whitespace():
    snippet = REAL_SNIPPET.replace(
        "Auto NC Optimizer,",
        "Heat&nbsp;Resistant&nbsp;220&deg;C,")
    parsed = _parse_amazon_html(snippet)
    assert parsed["highlights"].startswith("Heat Resistant 220")
    assert "&nbsp;" not in parsed["highlights"]


def test_parse_item_highlight_truncated_to_125_contract():
    long_value = "A, " * 80  # 240 字符 —— 超过文案端 125 契约
    snippet = re.sub(
        r'(<th[^>]*>\s*Item Highlight\s*</th>\s*<td[^>]*>).*?(</td>)',
        rf'\1 {long_value} \2', REAL_SNIPPET, flags=re.S)
    parsed = _parse_amazon_html(snippet)
    assert parsed["highlights"] == long_value.strip()[:125]


def test_parse_item_highlight_detail_bullets_fallback():
    """detailBullets 无序列表形态："Item Highlight" 后跟 ‑ 值。"""
    snippet = """
    <div id="detailBullets_feature_div">
      <ul>
        <li><span class="a-text-bold"> Item Highlight ‑ </span>
            <span>Non-stick, Food Grade, Heat Resistant 220°C, 100 PCS</span></li>
        <li><span class="a-text-bold"> ASIN ‑ </span><span>B0TEST</span></li>
      </ul>
    </div>"""
    parsed = _parse_amazon_html(snippet)
    assert parsed["highlights"] == "Non-stick, Food Grade, Heat Resistant 220°C, 100 PCS"


def test_parse_without_highlights_returns_empty_string():
    parsed = _parse_amazon_html('<div id="productTitle">x</div>')
    assert parsed["highlights"] == ""
    # 旧字段不回归：没有亮点时 title / bullets / imageUrls 键仍然在
    assert parsed["title"] == "x"
    assert parsed["bullets"] == []
    assert parsed["imageUrls"] == []
