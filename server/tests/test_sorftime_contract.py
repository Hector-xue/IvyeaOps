"""Sorftime MCP call contract.

Sorftime's schema is snake_case throughout (``amz_site``,
``keyword_support_site``, ``product_name``, ``node_id``, ``start_date`` …) and a
camelCase key is *silently ignored*: the call comes back 200/no-isError with the
prose "Please specify the site to query…", which used to reach the AI as if it
were market data. These tests pin both halves of that fix, plus the response
shapes every panel parses.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from app.services import sorftime_service as S

_APP_DIR = Path(__file__).resolve().parents[1] / "app"

# Argument names Sorftime does not have. Any of these in a tool-call dict means
# the call silently loses that parameter.
_BANNED_ARG_KEYS = (
    "amzSite", "keywordSupportSite", "productName", "searchName",
    "nodeId", "startDate", "endDate",
)


def test_no_camelcase_sorftime_arguments_anywhere() -> None:
    """Guard: no call site may pass a camelCase Sorftime argument."""
    offenders: list[str] = []
    for path in _APP_DIR.rglob("*.py"):
        text = path.read_text("utf-8")
        for call in re.findall(r"_safe_call\((.{0,400}?)\)\s*,?\s*\d*\s*\)", text, re.S):
            for key in _BANNED_ARG_KEYS:
                if f'"{key}"' in call:
                    offenders.append(f"{path.name}: {key}")
    assert not offenders, f"camelCase Sorftime arguments: {offenders}"


def test_normalize_args_snake_cases_every_key() -> None:
    assert S.normalize_args({"amzSite": "US", "keyword": "x"}) == {"amz_site": "US", "keyword": "x"}
    assert S.normalize_args({"keywordSupportSite": "DE"}) == {"keyword_support_site": "DE"}
    assert S.normalize_args({"nodeId": "1", "startDate": "2026-07-01"}) == {
        "node_id": "1", "start_date": "2026-07-01"}
    assert S.normalize_args({"node_id": "1"}) == {"node_id": "1"}   # already correct
    assert S.normalize_args(None) == {}


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.text = "event: message\ndata: " + json.dumps(payload) + "\n\n"

    def raise_for_status(self) -> None:
        return None


class _FakeClient:
    """Captures the outgoing tool-call arguments and replays one answer."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.sent: dict = {}

    async def post(self, url, json=None, headers=None):   # noqa: A002
        self.sent = json
        return _FakeResponse(self.payload)


def _text_result(text: str) -> dict:
    return {"jsonrpc": "2.0", "id": 1,
            "result": {"content": [{"type": "text", "text": text}]}}


def _call(client, tool="keyword_detail", args=None):
    return asyncio.run(S._call_tool(client, tool, args if args is not None else {}, 1))


def test_call_tool_sends_snake_case_arguments(monkeypatch) -> None:
    monkeypatch.setattr(S, "_url", lambda: "https://mcp.sorftime.test?key=k")
    client = _FakeClient(_text_result(json.dumps({"doc": {}, "data": {"keyword": "x"}})))
    _call(client, args={"keyword": "x", "amzSite": "US"})
    assert client.sent["params"]["arguments"] == {"keyword": "x", "amz_site": "US"}


def test_missing_site_prose_is_an_error_not_data(monkeypatch) -> None:
    """The exact failure that made the market panel 'analyse' an error message."""
    monkeypatch.setattr(S, "_url", lambda: "https://mcp.sorftime.test?key=k")
    client = _FakeClient(_text_result(
        "Please specify the site to query. See the keyword_support_site "
        "parameter description in the method signature."))
    with pytest.raises(RuntimeError, match="Please specify the site"):
        _call(client)


def test_how_to_prose_from_product_report_is_an_error(monkeypatch) -> None:
    monkeypatch.setattr(S, "_url", lambda: "https://mcp.sorftime.test?key=k")
    client = _FakeClient(_text_result(
        "To analyze a single product, call the following tools to combine their data:\n1. …"))
    with pytest.raises(RuntimeError):
        _call(client, tool="product_report", args={"asin": "B0", "amz_site": "US"})


def test_plain_text_data_still_passes_through(monkeypatch) -> None:
    """product_trend legitimately answers with a text series — not an error."""
    monkeypatch.setattr(S, "_url", lambda: "https://mcp.sorftime.test?key=k")
    series = "2024年07月=1079,2024年08月=1616"
    client = _FakeClient(_text_result(series))
    assert _call(client, tool="product_trend", args={"asin": "B0", "amz_site": "US"}) == series


# ── Response-shape helpers (live envelope: {"doc": …, "data": …}) ─────────────

def test_unwrap_record_rows_on_live_envelopes() -> None:
    detail = {"doc": {"asin": "Product ASIN."}, "data": {"asin": "B07V4LR7HZ", "node_id": "17426738011"}}
    listing = {"doc": {"asin": "…"}, "data": [{"asin": "B1"}, {"asin": "B2"}]}
    report = {"doc": {}, "data": {"top100_products": [{"asin": "B3"}], "category_stats_report": {}}}

    assert S.record(detail)["node_id"] == "17426738011"
    assert [r["asin"] for r in S.rows(listing)] == ["B1", "B2"]
    assert [r["asin"] for r in S.rows(report)] == ["B3"]
    assert S.first_field(detail, "node_id") == "17426738011"
    assert S.first_field(listing, "asin") == "B1"
    assert S.first_field(detail, "missing") == ""
    # Unwrapped / unexpected payloads must not explode.
    assert S.rows("plain text") == []
    assert S.record(None) == {}
