from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from trader_agent.repository.base import Base
from trader_agent.repository.recommendations import (
    Recommendation,
    RecommendationORM,
    RecommendationRepository,
)


@pytest.fixture
def repo() -> RecommendationRepository:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return RecommendationRepository(session_factory)


def _session_factory_for(repo: RecommendationRepository):
    return repo._session_factory  # type: ignore[attr-defined]


# ---- record ------------------------------------------------------------------

def test_record_creates_recommendation(repo):
    rec = repo.record("THYAO", "BUY", entry=100.0, stop=95.0, target=110.0, rationale="EMA hizalı")
    assert isinstance(rec, Recommendation)
    assert rec.symbol == "THYAO"
    assert rec.decision == "BUY"
    assert rec.entry == 100.0
    assert rec.stop == 95.0
    assert rec.target == 110.0
    assert rec.rationale == "EMA hizalı"


def test_record_normalizes_symbol(repo):
    rec = repo.record(" thyao ", "BUY", entry=100.0, stop=95.0)
    assert rec.symbol == "THYAO"


def test_record_rejects_invalid_decision(repo):
    with pytest.raises(ValueError, match="decision"):
        repo.record("THYAO", "HOLD", entry=100.0, stop=95.0)  # type: ignore[arg-type]


def test_record_rejects_non_positive_prices(repo):
    with pytest.raises(ValueError, match="entry"):
        repo.record("THYAO", "BUY", entry=0.0, stop=95.0)
    with pytest.raises(ValueError, match="stop"):
        repo.record("THYAO", "BUY", entry=100.0, stop=-1.0)


def test_record_target_and_rationale_optional(repo):
    rec = repo.record("THYAO", "SELL", entry=100.0, stop=105.0)
    assert rec.target is None
    assert rec.rationale is None


# ---- list --------------------------------------------------------------------

def test_list_returns_newest_first(repo):
    repo.record("THYAO", "BUY", entry=100.0, stop=95.0)
    repo.record("AKBNK", "SELL", entry=50.0, stop=52.0)
    recs = repo.list()
    assert [r.symbol for r in recs] == ["AKBNK", "THYAO"]


def test_list_filters_by_symbol(repo):
    repo.record("THYAO", "BUY", entry=100.0, stop=95.0)
    repo.record("AKBNK", "SELL", entry=50.0, stop=52.0)
    recs = repo.list("THYAO")
    assert [r.symbol for r in recs] == ["THYAO"]


def test_list_empty_returns_empty(repo):
    assert repo.list() == []


# ---- 10-day retention --------------------------------------------------------

def test_record_purges_older_than_10_days(repo):
    factory = _session_factory_for(repo)
    old = datetime.now(timezone.utc) - timedelta(days=11)
    with factory() as session:
        session.add(RecommendationORM(
            symbol="OLD",
            decision="BUY",
            entry=10.0,
            stop=9.0,
            target=None,
            rationale=None,
            created_at=old,
        ))
        session.commit()

    repo.record("NEW", "BUY", entry=100.0, stop=95.0)
    symbols = [r.symbol for r in repo.list()]
    assert "OLD" not in symbols
    assert "NEW" in symbols


def test_list_does_not_return_records_older_than_10_days(repo):
    factory = _session_factory_for(repo)
    old = datetime.now(timezone.utc) - timedelta(days=11)
    recent = datetime.now(timezone.utc) - timedelta(days=5)
    with factory() as session:
        session.add(RecommendationORM(
            symbol="OLD", decision="BUY", entry=10.0, stop=9.0,
            target=None, rationale=None, created_at=old,
        ))
        session.add(RecommendationORM(
            symbol="RECENT", decision="BUY", entry=10.0, stop=9.0,
            target=None, rationale=None, created_at=recent,
        ))
        session.commit()

    symbols = [r.symbol for r in repo.list()]
    assert symbols == ["RECENT"]
