from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from trader_agent.engine.tools.news import NewsTools


@pytest.mark.parametrize("days", [0, -1, 15, True, "7", 1.5])
async def test_market_tool_rejects_invalid_window_before_read(days):
    service = AsyncMock()
    with pytest.raises(ValidationError):
        await NewsTools(service).get_market_news(days)
    service.get_market_news.assert_not_awaited()


@pytest.mark.parametrize("symbols", [[], [" "], ["THYAO; DROP TABLE"], [4], ["THYAO"] * 101])
async def test_symbol_tool_validates_model_input_before_read(symbols):
    service = AsyncMock()
    with pytest.raises(ValueError):
        await NewsTools(service).get_symbol_news(symbols)
    service.get_symbol_news.assert_not_awaited()


async def test_symbol_tool_normalizes_and_deduplicates():
    service = AsyncMock(get_symbol_news=AsyncMock(return_value=[]))
    result = await NewsTools(service).get_symbol_news(["thyao", " THYAO "])
    assert result == [{"symbol": "THYAO", "days": 7, "digests": []}]
    service.get_symbol_news.assert_awaited_once_with("THYAO", days=7)
