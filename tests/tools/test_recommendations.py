from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from trader_agent.repository.base import Base
from trader_agent.repository.recommendations import RecommendationRepository
from trader_agent.tools.recommendations import RecommendationTools


@pytest.fixture
def tools() -> RecommendationTools:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return RecommendationTools(repository=RecommendationRepository(session_factory))


# ---- record_recommendation ---------------------------------------------------

async def test_record_recommendation_success(tools):
    result = await tools.record_recommendation(
        "THYAO", "BUY", entry=100.0, stop=95.0, target=110.0, rationale="EMA hizalı"
    )
    rec = result["recommendation"]
    assert rec["symbol"] == "THYAO"
    assert rec["decision"] == "BUY"
    assert rec["entry"] == 100.0
    assert rec["stop"] == 95.0
    assert rec["target"] == 110.0
    assert rec["rationale"] == "EMA hizalı"
    assert "id" in rec
    assert "created_at" in rec


async def test_record_recommendation_optional_fields_omitted(tools):
    result = await tools.record_recommendation("THYAO", "SELL", entry=100.0, stop=105.0)
    rec = result["recommendation"]
    assert "target" not in rec
    assert "rationale" not in rec


async def test_record_recommendation_invalid_decision_returns_error(tools):
    result = await tools.record_recommendation("THYAO", "HOLD", entry=100.0, stop=95.0)
    assert "error" in result


async def test_record_recommendation_invalid_price_returns_error(tools):
    result = await tools.record_recommendation("THYAO", "BUY", entry=0.0, stop=95.0)
    assert "error" in result


# ---- list_recommendations ----------------------------------------------------

async def test_list_recommendations_empty(tools):
    result = await tools.list_recommendations()
    assert result == {"recommendations": []}


async def test_list_recommendations_returns_newest_first(tools):
    await tools.record_recommendation("THYAO", "BUY", entry=100.0, stop=95.0)
    await tools.record_recommendation("AKBNK", "SELL", entry=50.0, stop=52.0)
    result = await tools.list_recommendations()
    assert [r["symbol"] for r in result["recommendations"]] == ["AKBNK", "THYAO"]


async def test_list_recommendations_filters_by_symbol(tools):
    await tools.record_recommendation("THYAO", "BUY", entry=100.0, stop=95.0)
    await tools.record_recommendation("AKBNK", "SELL", entry=50.0, stop=52.0)
    result = await tools.list_recommendations(symbol="THYAO")
    assert [r["symbol"] for r in result["recommendations"]] == ["THYAO"]


# ---- as_tool_list ------------------------------------------------------------

def test_as_tool_list_returns_both_tools(tools):
    names = {t.name for t in tools.as_tool_list()}
    assert names == {"record_recommendation", "list_recommendations"}
