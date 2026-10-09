"""Saat, takvim ve takip listesinden prompt bağlamı üretir; I/O yapmaz."""

from collections.abc import Sequence, Mapping
from datetime import datetime, timedelta
from string import Template
from zoneinfo import ZoneInfo

from ..calendar import TradingCalendar, TradingSession
from .prompts import CONTEXT_PROMPT, FORMAT_PROMPTS, SYSTEM_PROMPT


def _render_prompt(template: str, **values: str) -> str:
    return Template(template.strip()).safe_substitute(values)


def format_instructions(output_format: str) -> str:
    return FORMAT_PROMPTS.get(output_format, FORMAT_PROMPTS["plain"]).strip()


def build_system(watchlist: Sequence[Mapping[str, str]], output_format: str) -> str:
    return _render_prompt(
        SYSTEM_PROMPT,
        watchlist_count=str(len(watchlist)),
        watchlist_lines="\n".join(
            f"  {entry['symbol']}: {entry.get('name', entry['symbol'])}" for entry in watchlist
        ),
        format_instructions=format_instructions(output_format),
    )


def build_context(
    calendar: TradingCalendar, exchange_name: str, delay_minutes: int | None, now: datetime,
) -> str:
    now = calendar.local_time(now)
    day = calendar.day(now.date())
    session_open = day.is_open(now)
    if day.session:
        hours = f"{day.session.start:%H:%M}–{day.session.end:%H:%M}"
        session_status = f"AÇIK ({hours})" if session_open else f"KAPALI (seans saatleri: {hours})"
        if day.kind == "half_day":
            session_status += f" — yarım gün: {day.reason}"
        session_phase = _session_phase(now, day.session, bool(session_open))
    else:
        session_status = ("BİLİNMİYOR" if day.kind == "unknown" else "KAPALI") + f" — {day.reason}"
        session_phase = "takvim kapsamı dışında" if day.kind == "unknown" else (
            "hafta sonu" if now.weekday() >= 5 else "tatil"
        )
    session_timing = ""
    if session_open and day.session is not None:
        session_timing = f"Seans Kapanışına Kalan: {_format_remaining(day.session.end - now)}"
    else:
        next_open = calendar.next_open(now)
        if next_open:
            session_timing = f"Sonraki Seans: {next_open:%d.%m.%Y %H:%M}"
    feed_delay = (
        f"Veri Gecikmesi: {delay_minutes} dakika"
        if delay_minutes and delay_minutes >= 5
        else ""
    )

    day_names = {0: "Pazartesi", 1: "Salı", 2: "Çarşamba", 3: "Perşembe", 4: "Cuma", 5: "Cumartesi", 6: "Pazar"}
    day_name = day_names[now.weekday()]

    return _render_prompt(
        CONTEXT_PROMPT,
        exchange_name=exchange_name,
        day_name=day_name,
        date=now.strftime("%d.%m.%Y"),
        time=now.strftime("%H:%M"),
        tz_label=_tz_label(calendar.timezone),
        session_status=session_status,
        session_phase=session_phase,
        session_timing=session_timing,
        feed_delay=feed_delay,
    )


def _tz_label(tz: ZoneInfo) -> str:
    """IANA timezone'dan okunabilir şehir etiketi türetir.

    Örn: ``Europe/Istanbul`` → ``Istanbul``, ``America/New_York`` → ``New York``.
    Tek segmentli (örn. ``UTC``) timezone'lar için olduğu gibi döner.
    """
    last = tz.key.split("/")[-1]
    return last.replace("_", " ")


def _session_phase(now: datetime, session: TradingSession, session_open: bool) -> str:
    """Şu anki anı seans fazına eşler.

    Faz etiketleri prompt'taki "Seans-Bilinçli Davranış" bölümüyle eşleşir.
    """
    if not session_open:
        return "pre-market" if now < session.start else "post-market"

    session_start = session.start
    session_end = session.end
    if now - session_start < timedelta(minutes=30):
        return "açılış (ilk 30 dk)"
    if session_end - now <= timedelta(hours=1):
        return "kapanışa yaklaşıyor (son 1 saat)"
    return "orta seans"


def _format_remaining(delta: timedelta) -> str:
    total_minutes = max(0, int(delta.total_seconds() // 60))
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours} saat {minutes} dakika"
    if hours:
        return f"{hours} saat"
    return f"{minutes} dakika"
