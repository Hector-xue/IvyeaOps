"""采集到的 Item Highlights 要喂进 AI 上下文（分析 / 文案两条链路都吃到）。"""
from app.routers.listing.common import _build_product_context
from app.routers.listing.copywriting import _compose_project_notes

# 生产代码用 row['asin'] 下标（sqlite3.Row 语义），dict 同样满足
_ROW = {"asin": "B0TEST", "marketplace": "US"}


def test_build_product_context_includes_highlights():
    scrape_data = {"title": "Sony WH-1000XM5", "highlights": "Touch Control, 30 Hrs Battery"}
    ctx = _build_product_context(_ROW, scrape_data, {})
    assert "Current Item Highlights: Touch Control, 30 Hrs Battery" in ctx


def test_build_product_context_without_highlights_adds_no_line():
    ctx = _build_product_context(_ROW, {"title": "T"}, {})
    assert "Item Highlights" not in ctx


def test_compose_project_notes_includes_highlights():
    scrape_data = {"title": "T", "highlights": "Non-stick, 100 PCS", "bullets": ["b1"]}
    notes = _compose_project_notes(_ROW, scrape_data)
    assert "竞品 Item Highlights: Non-stick, 100 PCS" in notes


def test_compose_project_notes_without_highlights_adds_no_line():
    notes = _compose_project_notes(_ROW, {"title": "T", "bullets": ["b1"]})
    assert "Item Highlights" not in notes
