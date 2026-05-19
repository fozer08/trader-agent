from __future__ import annotations

import asyncio
import json
import logging
import os
import re

import httpx
import uvicorn
from telegram import BotCommand, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.request import HTTPXRequest

from ..server.app import app as fastapi_app

# FastAPI sunucusu aynı process içinde 127.0.0.1:8000 portunda kalkar;
# bot ona httpx ile bağlanır (loopback, latency ihmal edilebilir).
SERVER_URL = "http://127.0.0.1:8000"
_PORT = 8000

# Telegram long-poll'un sunucudan beklediği süre. HTTPXRequest.read_timeout
# bu değerden büyük olmalı; aksi halde httpx, Telegram cevap göndermeden
# bağlantıyı düşürüp spurious ReadError fırlatır.
_POLL_TIMEOUT = 30

# Telegram'daki "typing…" göstergesi yaklaşık 5 saniyede bir sönüyor;
# uzun süren agent çağrılarında kullanıcıya "bot ölü mü?" hissi vermemek
# için periyodik refresh atıyoruz.
_TYPING_REFRESH_SECONDS = 4

logger = logging.getLogger(__name__)


# ---- Handlers ----------------------------------------------------------------

async def _cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Merhaba! BIST teknik analiz asistanınım.\n"
        "/reset — konuşmayı sıfırla\n"
        "Herhangi bir şey yazarak başlayabilirsin."
    )


async def _cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    session_id = str(update.effective_chat.id)
    async with httpx.AsyncClient(base_url=SERVER_URL) as client:
        await client.post("/v1/reset", json={"session_id": session_id})
    await update.message.reply_text("Konuşma geçmişi sıfırlandı.")


async def _on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Kullanıcı mesajını /v1/chat'e iletir, ndjson stream'i tüketir, yanıtı basar.

    Stream sırasında:
      - text event'leri text_buffer'da toplanır, sonra tek seferde gönderilir
        (Telegram editMessageText rate limit'i nedeniyle inline streaming yapmıyoruz).
      - tool event'leri canlı bir "⚙ ..." status mesajına yazılır.
      - error event'i yakalanıp kullanıcıya iletilir.
    """
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    session_id = str(chat_id)

    # Typing göstergesini ve tool-çağrı status mesajını ayrı yönetiyoruz.
    # typing_task ve status_msg her durumda (hata dahil) finally'de toparlanır.
    typing_task = asyncio.create_task(_keep_typing(ctx.bot, chat_id))
    status_msg = None
    tool_history: list[str] = []
    text_buffer: list[str] = []
    error_message: str | None = None

    try:
        # timeout=600: agent uzun analizlerde 1-2 dakikayı bulabiliyor;
        # bot çağrısının erken düşmemesi için cömert bir limit.
        async with httpx.AsyncClient(base_url=SERVER_URL, timeout=600) as client:
            async with client.stream(
                "POST",
                "/v1/chat",
                json={
                    "session_id": session_id,
                    "message": update.message.text,
                    "output_format": "telegram_html",
                },
            ) as r:
                r.raise_for_status()
                # ndjson: her satır bir JSON event. Boş satırları/parse hatalarını
                # sessizce atlıyoruz çünkü stream'in ortasında düşmemeli.
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    kind = event.get("type")
                    if kind == "text":
                        text_buffer.append(event.get("content", ""))
                    elif kind == "tool":
                        tool_history.append(event.get("name", "?"))
                        status_msg = await _update_status(
                            ctx.bot, chat_id, status_msg, tool_history
                        )
                    elif kind == "error":
                        # Stream içi error event'i — agent kendi içinde hata bildirdi.
                        # Kullanıcıya iletilecek, ama stream'in geri kalanını da okumaya devam edebiliriz.
                        error_message = event.get("message", "bilinmeyen hata")
    except Exception:
        # Network/parse/agent — hangi katmandan gelirse gelsin kullanıcıya generik mesaj,
        # detay log'a. Exception detayını kullanıcıya basarsak hem güvenlik hem UX kötüleşir.
        logger.exception("telegram message handler failed")
        await update.message.reply_text("İstek sırasında bir hata oluştu, tekrar deneyin.")
        return
    finally:
        # Yanıt akışı bitti (başarılı veya hata). Typing'i kapat, status mesajını sil.
        typing_task.cancel()
        if status_msg is not None:
            try:
                await status_msg.delete()
            except Exception:
                pass

    if error_message is not None:
        await update.message.reply_text(f"Hata: {error_message}")
        return

    response = "".join(text_buffer)
    if not response:
        await update.message.reply_text("Yanıt alınamadı.")
        return

    # Telegram tek mesaj başına 4096 char limitli. _split_message paragraf
    # bütünlüğüne saygı duyarak böler. HTML parse hatasında plain'e düşeriz.
    for chunk in _split_message(response):
        try:
            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)
        except BadRequest:
            await update.message.reply_text(_strip_html(chunk))


async def _keep_typing(bot, chat_id: int) -> None:
    """Yanıt akışı sürdükçe Telegram 'typing…' göstergesini canlı tutar.

    _on_message bittiğinde dışarıdan cancel edilir. send_chat_action hatasını
    yutuyoruz: ağ titremesi göstergeyi koparıp döngüyü öldürmemeli.
    """
    try:
        while True:
            try:
                await bot.send_chat_action(chat_id, ChatAction.TYPING)
            except Exception as exc:
                logger.debug("typing action failed: %s", exc)
            await asyncio.sleep(_TYPING_REFRESH_SECONDS)
    except asyncio.CancelledError:
        pass


async def _update_status(bot, chat_id: int, status_msg, tool_history: list[str]):
    """Çalışan tool'lar için canlı status mesajını yönetir.

    İlk tool çağrısında yeni mesaj atar; sonrakilerde aynı mesajı edit eder.
    Telegram chat başına ~1 edit/sn rate limit uyguluyor — hızlı arka arkaya
    tool çağrılarında edit reddi gelebilir, görmezden geliyoruz (sonraki
    çağrı zaten güncel listeyi yazacak).
    """
    text = "⚙ " + " · ".join(tool_history)
    if status_msg is None:
        try:
            return await bot.send_message(chat_id, text)
        except Exception as exc:
            logger.debug("status send failed: %s", exc)
            return None
    try:
        await status_msg.edit_text(text)
    except BadRequest as exc:
        logger.debug("status edit skipped: %s", exc)
    except Exception as exc:
        logger.debug("status edit failed: %s", exc)
    return status_msg


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def _split_message(text: str, limit: int = 4096) -> list[str]:
    """Telegram'ın 4096 char/mesaj limitine sığacak şekilde böler.

    Önce paragraf (boş satır) sınırından bölmeyi dener; tek paragraf yine
    limiti aşıyorsa satır satır parçalar. Tek satır 4096'yı aşarsa olduğu
    gibi yollanır — Telegram o durumda reddedebilir, çok nadir görülür.
    """
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current: list[str] = []
    current_len = 0
    for paragraph in text.split("\n\n"):
        block = paragraph + "\n\n"
        if current_len + len(block) > limit and current:
            parts.append("".join(current).rstrip())
            current = []
            current_len = 0
        if len(block) > limit:
            for line in paragraph.split("\n"):
                seg = line + "\n"
                if current_len + len(seg) > limit and current:
                    parts.append("".join(current).rstrip())
                    current = []
                    current_len = 0
                current.append(seg)
                current_len += len(seg)
        else:
            current.append(block)
            current_len += len(block)
    if current:
        parts.append("".join(current).rstrip())
    return parts


# ---- Error handler -----------------------------------------------------------

async def _on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler içinden bubble eden hataları yakalar.

    NetworkError/TimedOut: PTB zaten retry yapıyor, gürültü çıkarmaya gerek yok.
    Diğer her şey unexpected — full traceback'i ERROR seviyesinde basıyoruz.
    """
    err = ctx.error
    if isinstance(err, (NetworkError, TimedOut)):
        logger.debug("Recoverable telegram network error: %s", err)
        return
    logger.exception("Unhandled telegram error", exc_info=err)


class _RecoverableNetworkErrorFilter(logging.Filter):
    """Updater'ın long-poll sırasında bastığı NetworkError traceback'lerini susturur.

    add_error_handler bunu yakalamıyor çünkü hata, dispatcher'a değil Updater'ın
    kendi network_retry_loop'una düşüyor. Logger seviyesinde filtre tek temiz yol.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        if isinstance(exc, (NetworkError, TimedOut)):
            return False
        return True


def _install_log_filter() -> None:
    flt = _RecoverableNetworkErrorFilter()
    # Bilinen PTB logger isimlerine ekliyoruz; isim değişirse sessizce no-op olur.
    for name in ("telegram.ext.Updater", "telegram.ext.ExtBot", "telegram.Bot"):
        logging.getLogger(name).addFilter(flt)


# ---- Entry point -------------------------------------------------------------

async def run() -> None:
    """Tek process içinde hem FastAPI sunucusunu hem Telegram bot'u çalıştırır."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN ortam değişkeni tanımlı değil.")

    _install_log_filter()

    # HTTPXRequest timeout'ları long-poll süresinden büyük tutuluyor; aksi halde
    # httpx, Telegram cevap göndermeden bağlantıyı düşürüp NetworkError fırlatır.
    tg_app = (
        Application.builder()
        .token(token)
        .request(
            HTTPXRequest(
                connection_pool_size=8,
                connect_timeout=10.0,
                read_timeout=_POLL_TIMEOUT + 10,
                write_timeout=10.0,
                pool_timeout=5.0,
            )
        )
        .build()
    )
    tg_app.add_handler(CommandHandler("start", _cmd_start))
    tg_app.add_handler(CommandHandler("reset", _cmd_reset))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_message))
    tg_app.add_error_handler(_on_error)

    # FastAPI loopback'te ayağa kalkar; bot ona httpx ile çağrı atar.
    # log_level=warning: uvicorn'un access log gürültüsünü susturuyoruz.
    server = uvicorn.Server(
        uvicorn.Config(fastapi_app, host="127.0.0.1", port=_PORT, log_level="warning")
    )

    async with tg_app:
        await tg_app.start()
        # Telegram client UI'ında komut menüsünü doldurur. Ağ hatasında bot'u
        # öldürmek istemiyoruz — sadece log'la geçiyoruz.
        try:
            await tg_app.bot.set_my_commands(
                [
                    BotCommand("start", "Başlangıç mesajı"),
                    BotCommand("reset", "Konuşma geçmişini sıfırla"),
                ]
            )
        except Exception as exc:
            logger.warning("set_my_commands failed: %s", exc)
        # drop_pending_updates=True: bot down'ken birikmiş eski mesajları işlemiyoruz
        # (kullanıcı zaten beklemiyor, eski analizler yanıltıcı olur).
        await tg_app.updater.start_polling(
            timeout=_POLL_TIMEOUT,
            drop_pending_updates=True,
        )
        print(f"Trader Agent çalışıyor (port {_PORT}). Durdurmak için Ctrl+C.")
        # uvicorn.serve() bloklar; bot polling paralel koşar.
        # Ctrl+C ile serve dönünce graceful shutdown'a geçiyoruz.
        await server.serve()
        await tg_app.updater.stop()
        await tg_app.stop()
