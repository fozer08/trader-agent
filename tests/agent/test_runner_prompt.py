from __future__ import annotations

from datetime import datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from trader_agent.agent.runner import AgentRunner, _tz_label
from trader_agent.market.base import TradingSession


def _runner(
    output_format: str = "plain",
    now: datetime | None = None,
    exchange_name: str = "Borsa İstanbul",
    tz: ZoneInfo = ZoneInfo("Europe/Istanbul"),
) -> AgentRunner:
    runner = AgentRunner.__new__(AgentRunner)
    runner._watchlist = [
        {"symbol": "THYAO", "name": "Türk Hava Yolları"},
        {"symbol": "AKBNK"},
    ]
    runner._session = TradingSession(
        start=time(10, 0),
        end=time(18, 0),
        timezone=tz,
    )
    runner._exchange_name = exchange_name
    runner._delay_minutes = None
    runner._output_format = output_format
    runner._config = SimpleNamespace(max_output_tokens=4096, use_extended_thinking=False)
    runner._recent_turns = []
    runner._tool_schemas = []
    if now is not None:
        runner._now = lambda: now
    return runner


# ---- Static (cached) system prompt -----------------------------------------

def test_static_prompt_renders_template_values():
    prompt = _runner()._build_static_prompt()
    assert "Sen borsada aktif trade yapan" in prompt
    assert "THYAO: Türk Hava Yolları" in prompt
    assert "AKBNK: AKBNK" in prompt
    assert "$watchlist_lines" not in prompt
    assert "$watchlist_count" not in prompt
    assert "$format_instructions" not in prompt


def test_static_prompt_is_exchange_agnostic():
    """Identity universal — BIST'e özel referanslar identity satırında kalmamalı.

    BIST kelimesi statik prompt'ta hiç geçmez; exchange bilgisi her turn dinamik
    CONTEXT_PROMPT'tan gelir.
    """
    prompt = _runner()._build_static_prompt()
    assert "BIST" not in prompt
    assert "USD/TL" not in prompt
    assert "BIST100" not in prompt


def test_static_prompt_excludes_dynamic_context():
    """Dinamik bağlam (saat/seans) statik prompt'a sızmamalı — 1h cache prefix'i bozulur."""
    prompt = _runner()._build_static_prompt()
    assert "Güncel Bağlam" not in prompt
    assert "Seans:" not in prompt
    assert "Seans Kapanışına Kalan" not in prompt
    assert "Borsa:" not in prompt


# ---- Dynamic context prompt ------------------------------------------------

def test_context_prompt_shows_exchange_name():
    prompt = _runner()._build_context_prompt()
    assert "Borsa: Borsa İstanbul" in prompt


def test_context_prompt_shows_tz_label_for_time():
    prompt = _runner()._build_context_prompt()
    assert "(Istanbul)" in prompt


def test_context_prompt_adapts_to_different_exchange():
    """Exchange parametrik — config değişince prompt akışı uyumlanır."""
    runner = _runner(
        exchange_name="NASDAQ",
        tz=ZoneInfo("America/New_York"),
        now=datetime(2026, 5, 12, 11, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    prompt = runner._build_context_prompt()
    assert "Borsa: NASDAQ" in prompt
    assert "(New York)" in prompt


def test_session_remaining_is_included_when_session_open():
    now = datetime(2026, 5, 12, 14, 30, tzinfo=ZoneInfo("Europe/Istanbul"))
    prompt = _runner(now=now)._build_context_prompt()
    assert "Seans: AÇIK" in prompt
    assert "Seans Kapanışına Kalan: 3 saat 30 dakika" in prompt


def test_session_remaining_is_omitted_when_session_closed():
    now = datetime(2026, 5, 12, 19, 0, tzinfo=ZoneInfo("Europe/Istanbul"))
    prompt = _runner(now=now)._build_context_prompt()
    assert "Seans: KAPALI" in prompt
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


# ---- Format instructions ---------------------------------------------------

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


# ---- System param composition ----------------------------------------------

def test_system_param_is_single_cached_static_block_when_memory_empty():
    runner = _runner()
    runner._state = SimpleNamespace(is_empty=lambda: True)
    runner._summary = ""
    system = runner._build_system_param()
    assert isinstance(system, list)
    assert len(system) == 1
    assert system[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "Felsefe" in system[0]["text"]
    assert "Güncel Bağlam" not in system[0]["text"]


# ---- tz_label helper -------------------------------------------------------

def test_tz_label_simple_city():
    assert _tz_label(ZoneInfo("Europe/Istanbul")) == "Istanbul"


def test_tz_label_multiword_city_underscore_replaced():
    assert _tz_label(ZoneInfo("America/New_York")) == "New York"


def test_tz_label_single_segment_kept_as_is():
    assert _tz_label(ZoneInfo("UTC")) == "UTC"
