from __future__ import annotations

import json
from datetime import datetime
from typing import AsyncGenerator

import anthropic

from ..config.llm import LLMConfig
from ..market.helpers import is_weekday
from ..market.types import TradingSession
from ..tools.base import Tool


class AgentRunner:
    """Claude API ile streaming konuşma döngüsünü yöneten agent çalıştırıcısı.

    Tool use bloklarını yakalar, ilgili handler'ı çalıştırır ve sonucu
    Claude'a geri göndererek döngüyü tamamlar. ``chat()`` bir async
    generator döndürür; text chunk'ları ve ``[tool_name]`` bildirimleri
    karışık olarak yield edilir.
    """

    def __init__(
        self,
        config: LLMConfig,
        watchlist: list[dict],
        session: TradingSession,
        tools: list[Tool],
        output_format: str = "plain",
    ) -> None:
        self._config = config
        self._watchlist = watchlist
        self._session = session
        self._output_format = output_format
        self._tools = {t.name: t for t in tools}
        self._tool_schemas = [t.to_api_dict() for t in tools]
        self._history: list[dict] = []
        self._client = anthropic.AsyncAnthropic(api_key=config.api_key)

    def reset(self) -> None:
        """Konuşma geçmişini temizler."""
        self._history.clear()

    async def chat(self, user_input: str) -> AsyncGenerator[str, None]:
        """Kullanıcı mesajına karşılık text ve tool bildirimi chunk'ları üretir.

        Yields:
            str: Claude'dan gelen text delta'ları veya ``\\x00TOOL:name\\x00``
                 formatında tool çağrı bildirimleri.
        """
        self._history.append({"role": "user", "content": user_input})
        self._trim_history()

        tool_calls_made = False
        while True:
            model = self._config.heavy_model if tool_calls_made else self._config.light_model
            async with self._client.messages.stream(
                model=model,
                system=self._build_system_prompt(),
                messages=self._history,
                tools=self._tool_schemas,
                max_tokens=self._config.max_tokens,
            ) as stream:
                async for event in stream:
                    if (
                        event.type == "content_block_delta"
                        and hasattr(event.delta, "text")
                    ):
                        yield event.delta.text

                final = await stream.get_final_message()

            assistant_content = []
            tool_use_blocks = []
            for block in final.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    yield f"\x00TOOL:{block.name}\x00"
                    assistant_content.append(
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    )
                    tool_use_blocks.append(block)

            self._history.append({"role": "assistant", "content": assistant_content})

            if final.stop_reason != "tool_use":
                break

            tool_calls_made = True
            tool_results = []
            for block in tool_use_blocks:
                tool = self._tools.get(block.name)
                if tool is None:
                    result = {"error": f"Unknown tool: {block.name}"}
                else:
                    try:
                        result = await tool.handler(**block.input)
                    except Exception as exc:
                        result = {"error": str(exc)}
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

            self._history.append({"role": "user", "content": tool_results})

    # ---- private -------------------------------------------------------------

    def _build_system_prompt(self) -> str:
        now = datetime.now(self._session.timezone)
        session_open = (
            is_weekday(now)
            and now.time() >= self._session.start
            and now.time() < self._session.end
        )
        session_status = (
            f"AÇIK ({self._session.start.strftime('%H:%M')}–{self._session.end.strftime('%H:%M')})"
            if session_open
            else f"KAPALI (seans saatleri: {self._session.start.strftime('%H:%M')}–{self._session.end.strftime('%H:%M')})"
        )

        day_names = {0: "Pazartesi", 1: "Salı", 2: "Çarşamba", 3: "Perşembe", 4: "Cuma", 5: "Cumartesi", 6: "Pazar"}
        day_name = day_names[now.weekday()]

        watchlist_lines = "\n".join(
            f"  {e['symbol']}: {e.get('name', e['symbol'])}"
            for e in self._watchlist
        )

        return f"""\
Sen BIST'te günlük trading kararları için teknik analiz desteği sağlayan bir ajansın.

## Güncel Bağlam
Tarih: {day_name}, {now.strftime('%d.%m.%Y')}
Saat: {now.strftime('%H:%M')} (İstanbul)
BIST Seans: {session_status}

## Takip Listesi ({len(self._watchlist)} hisse)
{watchlist_lines}

## Araç Kullanım Kılavuzu
- `scan` → Hızlı piyasa taraması; symbols belirtilmezse tüm takip listesi. Seans açıksa canlı fiyat ve % değişim içerir. Genel görünüm veya hisse önerisi için.
- `get_technicals` → Belirli hisseler için kapsamlı analiz (D1 + seans açıksa M15/M5). Liste alır. Stop/hedef veya giriş zamanlaması için.
- `get_levels` → Tek hisse için pivot, PDH/PDL/PDC, haftalık aralık, mum. Stop ve hedef seviyesi planlamak için.

## Davranış Kuralları
- Yanıtları kısa, net ve aksiyona yönelik tut
- Her işlem önerisinde stop-loss seviyesini mutlaka belirt
- ATR değerini stop mesafesi için referans olarak kullan
- Seans kapalıysa intraday araçların anlamsız olduğunu hatırlat
- Kesin kazanç garantisi verme; teknik analizin olasılıksal doğasını vurgula

{self._format_instructions()}"""

    def _format_instructions(self) -> str:
        if self._output_format == "telegram_html":
            return """\
## Çıktı Formatı
Telegram HTML kullan. Emoji kullanma.
- Kalın: <b>metin</b>
- İtalik: <i>metin</i>
- Satır içi değer: <code>değer</code>
- Tablo: <pre> içinde boşlukla hizalanmış sütunlar
- Liste maddeleri: - ile başla
- Başlık yerine <b>kalın</b> kullan
- Yalnızca şu tag'ları kullan: <b>, <i>, <code>, <pre>"""
        if self._output_format == "markdown":
            return """\
## Çıktı Formatı
Standart Markdown kullan. Emoji kullanma.
- Kalın: **metin**
- Tablo: Markdown tablo formatı"""
        return "## Çıktı Formatı\nDüz metin kullan, formatting sembolü kullanma. Emoji kullanma."

    def _trim_history(self) -> None:
        max_n = self._config.max_conversation_history
        if len(self._history) > max_n:
            self._history = self._history[-max_n:]
        # Tool result mesajları da role="user" taşır ama content bir liste olur.
        # API konuşmanın gerçek bir kullanıcı metin mesajıyla başlamasını gerektirir.
        while self._history:
            first = self._history[0]
            if first["role"] == "user" and isinstance(first["content"], str):
                break
            self._history.pop(0)
