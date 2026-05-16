from __future__ import annotations

import json

import httpx
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from rich.console import Console

SERVER_URL = "http://127.0.0.1:8000"
SESSION_ID = "cli"

_HELP = """
[bold]Komutlar:[/bold]
  [cyan]/state[/cyan]   Agent hafızasını (state + summary) göster
  [cyan]/reset[/cyan]   Konuşma geçmişini temizle
  [cyan]/quit[/cyan]    Çıkış
"""

_QUIT_CMDS = {"/quit", "/exit", "quit", "exit", "çıkış"}


async def run() -> None:
    console = Console(highlight=False)
    session: PromptSession = PromptSession()

    console.print(
        "\n[bold cyan]Trader Agent[/bold cyan] hazır. "
        "Yardım için [bold]/help[/bold] yazın.\n"
    )

    async with httpx.AsyncClient(base_url=SERVER_URL, timeout=600) as client:
        while True:
            try:
                raw = await session.prompt_async(HTML("<ansigreen>▶</ansigreen> "))
            except (KeyboardInterrupt, EOFError):
                break

            text = raw.strip()
            if not text:
                continue
            if text in _QUIT_CMDS:
                break
            if text == "/help":
                console.print(_HELP)
                continue
            if text == "/reset":
                await client.post("/v1/reset", json={"session_id": SESSION_ID})
                console.print("[dim]Konuşma geçmişi sıfırlandı.[/dim]\n")
                continue
            if text == "/state":
                try:
                    r = await client.get("/v1/debug/state", params={"session_id": SESSION_ID})
                    r.raise_for_status()
                    console.print_json(data=r.json())
                except httpx.HTTPStatusError as exc:
                    console.print(f"[red]State alınamadı: {exc.response.status_code} {exc.response.text[:200]}[/red]")
                except Exception as exc:
                    console.print(f"[red]State alınamadı ({type(exc).__name__}): {exc or 'detay yok'}[/red]")
                console.print()
                continue

            console.print()
            try:
                await _stream_response(client, console, text)
            except httpx.ConnectError:
                console.print("[red]Sunucuya bağlanılamadı. 'trader-agent serve' veya 'trader-agent telegram' çalışıyor mu?[/red]")
            except httpx.HTTPStatusError as exc:
                body = exc.response.text[:500] if exc.response is not None else ""
                console.print(f"[red]Sunucu hatası {exc.response.status_code}: {body}[/red]")
            except Exception as exc:
                console.print(f"[red]Hata ({type(exc).__name__}): {exc or 'detay yok'}[/red]")
            console.print("\n")


async def _stream_response(client: httpx.AsyncClient, console: Console, message: str) -> None:
    """ndjson stream'i tüketir; text'i inline yazar, tool bildirimini ayrı satıra basar."""
    async with client.stream(
        "POST",
        "/v1/chat",
        json={"session_id": SESSION_ID, "message": message, "output_format": "plain"},
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
                # Streaming text için raw print + flush — rich.Console buffer'ı
                # newline'a kadar takabiliyor; her chunk'ın anında görünmesi gerek.
                print(event.get("content", ""), end="", flush=True)
            elif kind == "tool":
                console.print(f"\n[dim]⚙  {event.get('name', '?')}[/dim]")
            elif kind == "error":
                console.print(f"\n[red]Hata: {event.get('message', '')}[/red]")
