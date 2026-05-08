from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .types import Bar, TimeFrame, TradingSession


class MarketIntradayCache:
    """Tek seanslık gün içi barlar için bellek içi cache.

    Cache anahtarı sembol, timeframe ve seans tarihinden oluşur. Yalnızca aktif
    seansı tutar; böylece tekrarlanan provider çağrıları sadece eksik kuyruğu
    çekerken hâlâ açık olan son barın yenilenmesine izin verir.
    """

    def __init__(
        self,
        session_start: time,
    ) -> None:
        """Eksik aralık sınırlarında ``session_start`` kullanan cache oluşturur."""
        self.session_start = session_start
        self._bars: dict[tuple[str, TimeFrame, date], list[Bar]] = {}

    def get(
        self,
        symbol: str,
        timeframe: TimeFrame,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Bar]:
        """Sembol/timeframe için cache'lenmiş barları, gerekirse zamana göre kırparak döndürür."""
        session_date = self._session_date(start, end)
        bars = self._bars.get((symbol, timeframe, session_date), [])

        if start is None and end is None:
            # Shallow copy is enough because Bar is frozen; callers cannot mutate items.
            return bars.copy()

        result: list[Bar] = []
        for bar in bars:
            if start is not None and bar.datetime < start:
                continue
            if end is not None and bar.datetime > end:
                break
            result.append(bar)

        return result

    def missing_range(
        self,
        symbol: str,
        timeframe: TimeFrame,
        *,
        end: datetime,
    ) -> tuple[datetime, datetime] | None:
        """``end`` zamanına kadar hâlâ çekilmesi gereken aralığı döndürür.

        Son cache barı kapalıysa çekim ondan sonraki bardan başlar. Son bar
        hâlâ açıksa çekim o barın zamanından başlar; böylece yeni veri merge
        sırasında açık barın yerine geçebilir.
        """
        session_date = end.date()
        self._prune_except(session_date)
        session_start = self._session_start_datetime(session_date, end)
        bars = self._bars.get((symbol, timeframe, session_date), [])
        last_bar = None
        for bar in reversed(bars):
            if bar.datetime <= end:
                last_bar = bar
                break

        if last_bar is None:
            return session_start, end

        next_start = last_bar.datetime
        if last_bar.is_closed:
            next_start += timedelta(minutes=timeframe.minutes)
        next_start = max(next_start, session_start)

        if next_start > end:
            return None

        return next_start, end

    def merge(
        self,
        symbol: str,
        timeframe: TimeFrame,
        bars: list[Bar],
    ) -> None:
        """Sıralı veya sırasız barları cache'e ekler, aynı zamanlı barları yenisiyle değiştirir."""
        if not bars:
            return

        session_date = bars[0].datetime.date()
        self._prune_except(session_date)
        self._validate_bars(symbol, timeframe, session_date, bars)

        key = (symbol, timeframe, session_date)
        existing = self._bars.get(key, [])
        incoming = self._dedupe_sorted(bars)

        if not existing:
            self._bars[key] = incoming
            return

        merged: list[Bar] = []
        existing_index = 0
        incoming_index = 0

        while existing_index < len(existing) and incoming_index < len(incoming):
            current = existing[existing_index]
            new = incoming[incoming_index]

            if current.datetime < new.datetime:
                merged.append(current)
                existing_index += 1
            elif current.datetime > new.datetime:
                merged.append(new)
                incoming_index += 1
            else:
                merged.append(new)
                existing_index += 1
                incoming_index += 1

        merged.extend(existing[existing_index:])
        merged.extend(incoming[incoming_index:])
        self._bars[key] = merged

    @staticmethod
    def _session_date(
        start: datetime | None,
        end: datetime | None,
    ) -> date:
        """Sorgu başlangıç veya bitiş zamanından seans tarihini çıkarır."""
        if start is not None:
            return start.date()
        if end is not None:
            return end.date()
        raise ValueError("start or end is required.")

    @staticmethod
    def _dedupe_sorted(
        bars: list[Bar],
    ) -> list[Bar]:
        """Barları zamana göre sıralar ve aynı zamandaki kayıtlarda son barı tutar."""
        deduped: dict[datetime, Bar] = {}
        for bar in bars:
            deduped[bar.datetime] = bar
        return sorted(deduped.values(), key=lambda bar: bar.datetime)

    def _session_start_datetime(
        self,
        session_date: date,
        end: datetime,
    ) -> datetime:
        """Sorgu timezone'u ile uyumlu, timezone-aware seans başlangıcı oluşturur."""
        return datetime.combine(
            session_date,
            self.session_start,
            tzinfo=end.tzinfo,
        )

    def _prune_except(
        self,
        session_date: date,
    ) -> None:
        """``session_date`` dışındaki cache'lenmiş seansları siler."""
        stale_keys = [
            key
            for key in self._bars
            if key[2] != session_date
        ]
        for key in stale_keys:
            del self._bars[key]

    @staticmethod
    def _validate_bars(
        symbol: str,
        timeframe: TimeFrame,
        session_date: date,
        bars: list[Bar],
    ) -> None:
        """Gelen barların saklanacakları cache anahtarıyla uyumlu olduğunu doğrular."""
        for bar in bars:
            if bar.symbol != symbol:
                raise ValueError(f"Unexpected cache symbol: {bar.symbol!r}")
            if bar.timeframe is not timeframe:
                raise ValueError(f"Unexpected cache timeframe: {bar.timeframe!r}")
            if bar.datetime.date() != session_date:
                raise ValueError(f"Unexpected cache session date: {bar.datetime.date()!r}")


class MarketDailyCache:
    """Günlük barlar için bellek içi cache.

    Staleness kararı son barın ``is_closed`` durumuna ve tarihine göre verilir;
    provider'ın ``is_closed`` semantiği değişse bile doğru çalışır.
    """

    def __init__(self, session: TradingSession, delay_minutes: int) -> None:
        self._timezone = session.timezone
        self._session_end = session.end
        self._delay_minutes = delay_minutes
        self._bars: dict[str, list[Bar]] = {}

    def get(self, symbol: str, period: int) -> list[Bar] | None:
        """Cache'li barları döndürür; stale veya yetersizse None."""
        bars = self._bars.get(symbol)
        if bars is None or self._is_stale(bars):
            return None
        if len(bars) < period:
            return None
        return bars[-period:]

    def set(self, symbol: str, bars: list[Bar]) -> None:
        """Sembol için bar listesini cache'e yazar."""
        if bars:
            self._bars[symbol] = bars

    # ---- private -------------------------------------------------------------

    def _is_stale(self, bars: list[Bar]) -> bool:
        last = bars[-1]
        if not last.is_closed:
            return True
        today = datetime.now(tz=self._timezone).date()
        if today > last.datetime.date():
            return self._is_bar_closed(today)
        return False

    def _is_bar_closed(self, bar_date: date) -> bool:
        delayed_now = datetime.now(tz=self._timezone) - timedelta(minutes=self._delay_minutes)
        if bar_date < delayed_now.date():
            return True
        if bar_date > delayed_now.date():
            return False
        return delayed_now.time() >= self._session_end
