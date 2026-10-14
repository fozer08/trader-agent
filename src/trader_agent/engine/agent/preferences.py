"""Bir sohbet mesajının kullanıcı tarafından seçilen değerlendirme bağlamı."""

from dataclasses import dataclass

from ..analysis import Horizon, RiskLevel


@dataclass(frozen=True)
class ConversationPreferences:
    """Vade/risk seçimini tur boyunca sabit taşır; geçmişin sonradan değişmesini önler."""

    horizon: Horizon | None = None
    risk_level: RiskLevel | None = None
