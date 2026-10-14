from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from types import MappingProxyType
from typing import Literal, Mapping
from zoneinfo import ZoneInfo


class CalendarCoverageError(ValueError):
    """Seansın belirlenemediğini bildirir; bilinmeyen tarihi kapalı günden ayırır.

    Kapsam dışına çıkan veya desteklenen tarih sınırına ulaşan seans sorguları
    bu hatayı üretir; çağıran bileşen veri eksikliğini açıkça ele alabilir.
    """


@dataclass(frozen=True)
class TradingSession:
    """Bir güne ait kesin seans sınırlarını bileşenler arasında taşır.

    start/end sürekli işlem aralığıdır; daily_end kapanış işlemlerini de içerir.
    Bu ayrım gün içi ve günlük verinin tamamlanmasını ayrı hesaplamak içindir.
    Tüm zamanlar borsanın timezone'unu içerir.
    """

    start: datetime
    end: datetime
    daily_end: datetime

    @property
    def date(self) -> date:
        return self.start.date()


@dataclass(frozen=True)
class ExchangeSchedule:
    """Normal haftalık düzenden tarihli seans üretmek için ortak kuralları tutar.

    Config modelinden bağımsızdır; çalışma günleri ve yerel seans saatleri
    takvime bu değer nesnesiyle aktarılır. Seans aynı gün içinde biter;
    daily_end belirtilmezse end kullanılır. Tatilleri TradingCalendar uygular.
    """

    start: time
    end: time
    timezone: ZoneInfo
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)
    daily_end: time | None = None

    def session(self, on: date) -> TradingSession:
        return TradingSession(
            datetime.combine(on, self.start, self.timezone),
            datetime.combine(on, self.end, self.timezone),
            datetime.combine(on, self.daily_end or self.end, self.timezone),
        )


@dataclass(frozen=True)
class TradingDay:
    """Bir tarih sorgusunun durumunu, seansını ve gerekçesini birlikte taşır.

    Kapalı gün ile takvim kapsamı dışındaki günü ayırarak tüketicilerin eksik
    bilgiyi kapalı piyasa olarak yorumlamasını önler. normal/half_day seans
    içerir; closed/unknown içermez.
    """

    date: date
    kind: Literal["normal", "half_day", "closed", "unknown"]
    session: TradingSession | None
    reason: str | None = None

    def is_open(self, at: datetime) -> bool | None:
        """Timezone içeren anda [start, end) durumunu verir; unknown için None."""
        if self.kind == "unknown":
            return None
        return self.session is not None and self.session.start <= at < self.session.end


@dataclass(frozen=True)
class TradingCalendar:
    """Haftalık düzeni ve tatilleri birleştirerek seans kararlarını tek yerde tutar.

    Provider, servis ve arayüzler aynı kuralları bu değişmez nesneden sorgular;
    dosya, config ve sistem saatine bağımlı değildir. exceptions, yüklemede
    tarihli seanslara dönüştürülmüş tatillerdir; yarım gün kuralları burada
    denetlenir. allow_uncovered günlük geçmiş sorguları için kapsam dışında
    haftalık düzeni kullanır; bilinen tatilleri geçersiz kılmaz.
    """

    schedule: ExchangeSchedule
    exceptions: Mapping[date, TradingDay]
    covered_years: frozenset[int]

    def __post_init__(self) -> None:
        exceptions = dict(self.exceptions)
        for on, day in exceptions.items():
            self._validate_exception(on, day)
        object.__setattr__(self, "exceptions", MappingProxyType(exceptions))

    def _validate_exception(self, on: date, day: TradingDay) -> None:
        if day.date != on or on.year not in self.covered_years:
            raise ValueError(f"Tatil tarihi takvimle uyuşmuyor: {on}")
        if day.kind == "closed" and day.session is None:
            return
        session = day.session
        if day.kind != "half_day" or session is None:
            raise ValueError(f"Geçersiz tatil seansı: {on}")
        normal = self.schedule.session(on)
        if (
            on.weekday() not in self.schedule.weekdays
            or not normal.start <= session.start < session.end <= normal.end
            or not session.end <= session.daily_end <= normal.daily_end
            or (session.start == normal.start and session.end == normal.end)
        ):
            raise ValueError(f"Geçersiz yarım gün seansı: {on}")

    @property
    def timezone(self) -> ZoneInfo:
        return self.schedule.timezone

    def local_time(self, at: datetime) -> datetime:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("Takvim sorgusu timezone içeren datetime gerektirir.")
        return at.astimezone(self.timezone)

    def day(self, on: date, *, allow_uncovered: bool = False) -> TradingDay:
        """Tatil, çalışma günü ve kapsamı çözer; kapsam dışı tarih unknown olur."""
        if on.year not in self.covered_years and not allow_uncovered:
            return TradingDay(on, "unknown", None, "Takvim bu yılı kapsamıyor.")
        if on in self.exceptions:
            return self.exceptions[on]
        if on.weekday() not in self.schedule.weekdays:
            return TradingDay(on, "closed", None, "Çalışma günü değil.")
        return TradingDay(on, "normal", self.schedule.session(on))

    def _require_day(self, on: date, *, allow_uncovered: bool = False) -> TradingDay:
        """Kesin seans bilgisi isteyen sorguların kapsam kuralını uygular."""
        day = self.day(on, allow_uncovered=allow_uncovered)
        if day.kind == "unknown":
            raise CalendarCoverageError(f"Takvim bu tarihi kapsamıyor: {day.date}")
        return day

    def is_open(self, at: datetime) -> bool | None:
        at = self.local_time(at)
        return self.day(at.date()).is_open(at)

    def previous_session(
        self, on: date, *, inclusive: bool = False, allow_uncovered: bool = False,
    ) -> TradingSession:
        """Önceki seansı bulur; inclusive=True ise verilen gün de adaydır."""
        return self._find_session(on, -1, inclusive, allow_uncovered)

    def next_session(
        self, on: date, *, inclusive: bool = False, allow_uncovered: bool = False,
    ) -> TradingSession:
        """Sonraki seansı bulur; inclusive=True ise verilen gün de adaydır."""
        return self._find_session(on, 1, inclusive, allow_uncovered)

    def _find_session(
        self, on: date, direction: Literal[-1, 1], inclusive: bool, allow_uncovered: bool,
    ) -> TradingSession:
        ordinal = on.toordinal() if inclusive else on.toordinal() + direction
        while date.min.toordinal() <= ordinal <= date.max.toordinal():
            day = self._require_day(date.fromordinal(ordinal), allow_uncovered=allow_uncovered)
            if day.session is not None:
                return day.session
            ordinal += direction
        raise CalendarCoverageError(f"Desteklenen tarih aralığında seans bulunamadı: {on}")

    def last_completed_session(
        self, at: datetime, *, allow_uncovered: bool = False,
    ) -> TradingSession:
        """daily_end <= at olan son seansı bulur; veri gecikmesini çağıran düşer."""
        at = self.local_time(at)
        day = self._require_day(at.date(), allow_uncovered=allow_uncovered)
        if day.session is not None and day.session.daily_end <= at:
            return day.session
        return self.previous_session(at.date(), allow_uncovered=allow_uncovered)

    def next_open(self, at: datetime) -> datetime | None:
        """at sonrasındaki ilk açılışı verir; kapsam yetmiyorsa None döndürür.

        Seans açılmışsa sonraki seans aranır; mevcut seansın açılışı dönmez.
        """
        at = self.local_time(at)
        day = self.day(at.date())
        if day.kind == "unknown":
            return None
        if day.session is not None and at < day.session.start:
            return day.session.start
        try:
            return self.next_session(at.date()).start
        except CalendarCoverageError:
            return None

    def reliable_bounds(
        self, data_until: datetime, *, on: date | None = None,
    ) -> tuple[datetime, datetime] | None:
        """Gecikmesi düşülmüş data_until zamanını seans sınırlarıyla kesiştirir.

        on verilirse o gün, yoksa data_until'ın borsadaki yerel günü kullanılır.
        Kapalı günde veya açılıştan önce None döner; tam açılışta aralık boştur.
        Bar periyoduna hizalamak ve eksik son barı elemek çağıranın görevidir.
        """
        data_until = self.local_time(data_until)
        day = self._require_day(on or data_until.date())
        if day.session is None or data_until < day.session.start:
            return None
        return day.session.start, min(data_until, day.session.end)
