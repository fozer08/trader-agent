from trader_agent.engine.repository import (
    PortfolioRepository, create_db_engine, create_session_factory, init_schema,
)
from trader_agent.engine.repository.preferences import TradingPreferencesRepository
from trader_agent.engine.repository.conversations import ConversationRepository
from trader_agent.engine.agent.preferences import ConversationPreferences
from trader_agent.engine.agent.memory import ConversationMemory


def test_portfolios_preferences_and_memory_survive_restart_without_crossing_users(tmp_path):
    path = tmp_path / "engine.db"
    database = create_db_engine(path)
    init_schema(database)
    factory = create_session_factory(database)
    alice = PortfolioRepository(factory, "alice")
    bob = PortfolioRepository(factory, "bob")
    a = alice.buy("THYAO", 10, 100, horizon="position")
    b = bob.buy("THYAO", 3, 200, horizon="intraday")
    alice.sell(a.id, 10, 120)
    assert len(alice.list_closed()) == 1
    assert bob.list_closed() == []
    assert bob.get(b.id).quantity == 3
    assert len(bob.transactions(b.id)) == 1
    alice.buy("THYAO", 1, 90, horizon="swing")
    alice.clear()
    assert bob.get(b.id).quantity == 3
    assert len(alice.transactions(a.id)) == 2
    preferences = ConversationPreferences("position", "low")
    TradingPreferencesRepository(factory, "alice").save(preferences)
    assert TradingPreferencesRepository(factory, "bob").get() == ConversationPreferences()
    memory = ConversationMemory()
    memory.remember([{"role": "user", "content": "Önceki sorum"}])
    ConversationRepository(factory).save("alice:chat", memory.dump())
    database.dispose()
    reopened = create_db_engine(path)
    try:
        factory = create_session_factory(reopened)
        assert TradingPreferencesRepository(factory, "alice").get() == preferences
        restored = ConversationMemory()
        restored.restore(ConversationRepository(factory).get("alice:chat"))
        assert restored.dump() == memory.dump()
        assert ConversationRepository(factory).get("bob:chat") is None
        assert PortfolioRepository(factory, "bob").get(b.id).horizon == "intraday"
    finally:
        reopened.dispose()
