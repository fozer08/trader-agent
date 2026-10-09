from __future__ import annotations

from datetime import datetime

import httpx


class EngineClient:
    """Bakım komutlarının çalışan engine'e erişimini sağlayan asenkron HTTP istemcisi.

    CLI komutları bu sınıf üzerinden yerel veya uzak bir engine'e erişebilir.
    Sunucu adresi ve API erişim anahtarı çağıran tarafından verilir. Anahtar
    tanımlıysa isteklerde Authorization: Bearer başlığı kullanılır.

    İstemci async with bloğunda kullanılır; bloktan çıkarken HTTP bağlantıları
    kapatılır. İş metotları sunucunun yanıtını bekler ve sonuç JSON'unu döndürür.
    HTTP ve ağ hataları çağıran koda iletilir.
    """

    def __init__(
        self,
        url: str,
        *,
        api_key: str = "",
        timeout: float = 600,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Verilen engine adresi ve erişim anahtarıyla HTTP istemcisini oluşturur.

        Args:
            url: Engine'in erişilebilir HTTP veya HTTPS adresi.
            api_key: İsteklerde kullanılacak erişim anahtarı; boşsa başlık eklenmez.
            timeout: HTTP istekleri için saniye cinsinden zaman aşımı.
            transport: Testlerde veya özel bağlantılarda kullanılacak HTTP taşıyıcısı.
        """
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._http = httpx.AsyncClient(
            base_url=url, headers=headers, timeout=timeout, transport=transport
        )

    async def __aenter__(self) -> EngineClient:
        """HTTP istemcisini açar ve async with bloğuna bu istemciyi verir."""
        await self._http.__aenter__()
        return self

    async def __aexit__(self, *args) -> None:
        """Blok hata ile sonlansa da HTTP bağlantılarını kapatır."""
        await self._http.__aexit__(*args)

    async def prefetch_market(self) -> dict[str, bool]:
        """Takip listesinin günlük piyasa verisini engine'in önbelleğine yükletir.

        POST /v1/jobs/prefetch-market isteği gönderir ve işin yanıtını bekler.
        Hisse listesi ve ön yüklenecek bar sayısı engine tarafından belirlenir.

        Returns:
            Her sembolün yükleme sonucunu gösteren sözlük; örneğin
            {"THYAO": True, "AKBNK": False}. False, ilgili hissenin verisinin
            yüklenemediğini gösterir; diğer hisselerin sonuçları da döndürülür.

        Raises:
            httpx.HTTPStatusError: Engine başarısız bir HTTP durum kodu döndürürse.
            httpx.RequestError: Ağ hatası veya zaman aşımı oluşursa.
        """
        response = await self._http.post("/v1/jobs/prefetch-market")
        response.raise_for_status()
        return response.json()

    async def daily_digest(self, since: datetime | None = None) -> dict:
        """Engine'de piyasa ve takip edilen hisselerin haber özetlerini yeniletir.

        POST /v1/jobs/daily-digest isteği gönderir ve işin yanıtını bekler.
        Haberlerin alınması, özetlenmesi ve kaydedilmesi engine'de yürütülür.

        Args:
            since: Haberlerin başlangıç zamanı; ISO formatında gönderilir.
                None olduğunda engine'in varsayılan zaman aralığı kullanılır.
                Timezone bilgisi olmayan değerleri engine UTC olarak yorumlar.

        Returns:
            Job kimliği, updated/empty durumu, zaman aralığı ve haber/özet sayıları.
            Kaynak veya model hatalarında önceki özetler korunur ve HTTP hatası alınır.

        Raises:
            httpx.HTTPStatusError: Engine başarısız bir HTTP durum kodu döndürürse.
            httpx.RequestError: Ağ hatası veya zaman aşımı oluşursa.
        """
        response = await self._http.post(
            "/v1/jobs/daily-digest", json={"since": since.isoformat() if since else None}
        )
        response.raise_for_status()
        return response.json()
