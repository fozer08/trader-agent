from __future__ import annotations

import json
import os
import re

import httpx
import uvicorn
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.request import HTTPXRequest

from ..server.app import app as fastapi_app

SERVER_URL = "http://127.0.0.1:8000"
_PORT = 8000


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
    if not update.message or not update.message.text:
        return

    session_id = str(update.effective_chat.id)
    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    text_buffer: list[str] = []
    error_message: str | None = None
    try:
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
                    elif kind == "error":
                        error_message = event.get("message", "bilinmeyen hata")
    except Exception as exc:
        await update.message.reply_text(f"Hata oluştu: {exc}")
        return

    if error_message is not None:
        await update.message.reply_text(f"Hata: {error_message}")
        return

    response = "".join(text_buffer)
    if not response:
        await update.message.reply_text("Yanıt alınamadı.")
        return

    for chunk in _split_message(response):
        try:
            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)
        except BadRequest:
            await update.message.reply_text(_strip_html(chunk))


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def _split_message(text: str, limit: int = 4096) -> list[str]:
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


# ---- Entry point -------------------------------------------------------------

async def run() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN ortam değişkeni tanımlı değil.")

    tg_app = (
        Application.builder()
        .token(token)
        .request(HTTPXRequest(connection_pool_size=8, pool_timeout=30.0))
        .build()
    )
    tg_app.add_handler(CommandHandler("start", _cmd_start))
    tg_app.add_handler(CommandHandler("reset", _cmd_reset))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_message))

    server = uvicorn.Server(
        uvicorn.Config(fastapi_app, host="127.0.0.1", port=_PORT, log_level="warning")
    )

    async with tg_app:
        await tg_app.start()
        await tg_app.updater.start_polling(drop_pending_updates=True)
        print(f"Trader Agent çalışıyor (port {_PORT}). Durdurmak için Ctrl+C.")
        await server.serve()
        await tg_app.updater.stop()
        await tg_app.stop()
