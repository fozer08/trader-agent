from __future__ import annotations

from datetime import datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from trader_agent.agent.runner import AgentRunner
from trader_agent.market.types import TradingSession


def _runner(output_format: str = "plain", now: datetime | None = None) -> AgentRunner:
    runner = AgentRunner.__new__(AgentRunner)
    runner._watchlist = [
        {"symbol": "THYAO", "name": "Türk Hava Yolları"},
        {"symbol": "AKBNK"},
    ]
    runner._session = TradingSession(
        start=time(10, 0),
        end=time(18, 0),
        timezone=ZoneInfo("Europe/Istanbul"),
    )
    runner._output_format = output_format
    runner._config = SimpleNamespace(use_prompt_caching=True, max_tokens=4096)
    runner._history = []
    runner._tool_schemas = []
    if now is not None:
        runner._now = lambda: now
    return runner


def test_system_prompt_is_rendered_from_template():
    now = datetime(2026, 5, 12, 14, 30, tzinfo=ZoneInfo("Europe/Istanbul"))
    prompt = _runner(now=now)._build_system_prompt()
    assert "Sen BIST'te günlük trading kararları için destek sağlayan bir asistansın." in prompt
    assert "THYAO: Türk Hava Yolları" in prompt
    assert "AKBNK: AKBNK" in prompt
    assert "$watchlist_lines" not in prompt
    assert "$format_instructions" not in prompt
    assert "$session_timing" not in prompt


def test_session_remaining_is_included_when_session_is_open():
    now = datetime(2026, 5, 12, 14, 30, tzinfo=ZoneInfo("Europe/Istanbul"))
    prompt = _runner(now=now)._build_system_prompt()
    assert "BIST Seans: AÇIK" in prompt
    assert "Seans Kapanışına Kalan: 3 saat 30 dakika" in prompt


def test_session_remaining_is_omitted_when_session_is_closed():
    now = datetime(2026, 5, 12, 19, 0, tzinfo=ZoneInfo("Europe/Istanbul"))
    prompt = _runner(now=now)._build_system_prompt()
    assert "BIST Seans: KAPALI" in prompt
    assert "Seans Kapanışına Kalan" not in prompt


def test_markdown_format_prompt_is_loaded():
    prompt = _runner("markdown")._format_instructions()
    assert "Standart Markdown kullan" in prompt
    assert "**metin**" in prompt


def test_telegram_html_format_prompt_is_loaded():
    prompt = _runner("telegram_html")._format_instructions()
    assert "Telegram HTML kullan" in prompt
    assert "<b>metin</b>" in prompt


def test_unknown_format_falls_back_to_plain():
    prompt = _runner("unknown")._format_instructions()
    assert "Düz metin kullan" in prompt


def test_system_param_uses_cache_control_when_enabled():
    now = datetime(2026, 5, 12, 14, 30, tzinfo=ZoneInfo("Europe/Istanbul"))
    system = _runner(now=now)._build_system_param()
    assert isinstance(system, list)
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert "Araç Kullanım Kılavuzu" in system[0]["text"]
    assert "Güncel Bağlam" not in system[0]["text"]
    assert "Güncel Bağlam" in system[1]["text"]
    assert "Seans Kapanışına Kalan: 3 saat 30 dakika" in system[1]["text"]


def test_system_param_is_plain_string_when_prompt_caching_disabled():
    runner = _runner()
    runner._config.use_prompt_caching = False
    system = runner._build_system_param()
    assert isinstance(system, str)
    assert "cache_control" not in system
    assert "Araç Kullanım Kılavuzu" in system
    assert "Güncel Bağlam" in system
