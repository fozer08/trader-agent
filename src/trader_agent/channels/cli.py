from __future__ import annotations

import httpx
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from rich.console import Console
from rich.markdown import Markdown
from rich.status import Status

SERVER_URL = "http://127.0.0.1:8000"
SESSION_ID = "cli"

_HELP = """
[bold]Komutlar:[/bold]
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

    async with httpx.AsyncClient(base_url=SERVER_URL, timeout=180) as client:
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

            print()
            with Status("[dim]Düşünüyor…[/dim]", console=console, spinner="dots"):
                try:
                    r = await client.post(
                        "/v1/chat",
                        json={"session_id": SESSION_ID, "message": text, "output_format": "markdown"},
                    )
                    r.raise_for_status()
                    data = r.json()
                except httpx.ConnectError:
                    console.print("[red]Sunucuya bağlanılamadı. 'trader-agent serve' veya 'trader-agent telegram' çalışıyor mu?[/red]")
                    print()
                    continue
                except Exception as exc:
                    console.print(f"[red]Hata: {exc}[/red]")
                    print()
                    continue

            if data.get("tools_used"):
                for tool in data["tools_used"]:
                    console.print(f"[dim]⚙  {tool}[/dim]")

            console.print(Markdown(data["response"]))
            print()
