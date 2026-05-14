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
    runner._config = SimpleNamespace(max_output_tokens=4096, use_extended_thinking=False)
    runner._history = []
    runner._tool_schemas = []
    if now is not None:
        runner._now = lambda: now
    return runner


def test_static_prompt_is_rendered_from_template():
    prompt = _runner()._build_static_prompt()
    assert "BIST'te günlük trade yapan bir trader" in prompt
    assert "THYAO: Türk Hava Yolları" in prompt
    assert "AKBNK: AKBNK" in prompt
    assert "$watchlist_lines" not in prompt
    assert "$format_instructions" not in prompt


def test_static_prompt_does_not_contain_dynamic_context():
    """Dinamik bağlam (saat/seans değerleri) statik prompt'a sızmamalı;
    1h cache prefix'i bozulur. Statik prompt sadece konseptlere atıfta
    bulunabilir, somut değer içermez."""
    prompt = _runner()._build_static_prompt()
    assert "Güncel Bağlam" not in prompt
    assert "BIST Seans:" not in prompt
    assert "Seans Kapanışına Kalan" not in prompt


def test_session_remaining_is_included_when_session_is_open():
    now = datetime(2026, 5, 12, 14, 30, tzinfo=ZoneInfo("Europe/Istanbul"))
    prompt = _runner(now=now)._build_context_prompt()
    assert "BIST Seans: AÇIK" in prompt
    assert "Seans Kapanışına Kalan: 3 saat 30 dakika" in prompt


def test_session_remaining_is_omitted_when_session_is_closed():
    now = datetime(2026, 5, 12, 19, 0, tzinfo=ZoneInfo("Europe/Istanbul"))
    prompt = _runner(now=now)._build_context_prompt()
    assert "BIST Seans: KAPALI" in prompt
    assert "Seans Kapanışına Kalan" not in prompt


def test_session_phase_pre_market():
    now = datetime(2026, 5, 12, 8, 0, tzinfo=ZoneInfo("Europe/Istanbul"))
    prompt = _runner(now=now)._build_context_prompt()
    assert "Seans Fazı: pre-market" in prompt


def test_session_phase_opening():
    now = datetime(2026, 5, 12, 10, 15, tzinfo=ZoneInfo("Europe/Istanbul"))
    prompt = _runner(now=now)._build_context_prompt()
    assert "Seans Fazı: açılış (ilk 30 dk)" in prompt


def test_session_phase_mid():
    now = datetime(2026, 5, 12, 14, 30, tzinfo=ZoneInfo("Europe/Istanbul"))
    prompt = _runner(now=now)._build_context_prompt()
    assert "Seans Fazı: orta seans" in prompt


def test_session_phase_closing():
    now = datetime(2026, 5, 12, 17, 30, tzinfo=ZoneInfo("Europe/Istanbul"))
    prompt = _runner(now=now)._build_context_prompt()
    assert "Seans Fazı: kapanışa yaklaşıyor (son 1 saat)" in prompt


def test_session_phase_post_market():
    now = datetime(2026, 5, 12, 19, 0, tzinfo=ZoneInfo("Europe/Istanbul"))
    prompt = _runner(now=now)._build_context_prompt()
    assert "Seans Fazı: post-market" in prompt


def test_session_phase_weekend():
    now = datetime(2026, 5, 10, 14, 30, tzinfo=ZoneInfo("Europe/Istanbul"))  # Sunday
    prompt = _runner(now=now)._build_context_prompt()
    assert "Seans Fazı: hafta sonu" in prompt


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


def test_system_param_is_single_cached_static_block():
    system = _runner()._build_system_param()
    assert isinstance(system, list)
    assert len(system) == 1
    assert system[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "Kimlik ve Felsefe" in system[0]["text"]
    assert "Güncel Bağlam" not in system[0]["text"]
