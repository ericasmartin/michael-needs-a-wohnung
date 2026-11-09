#!/usr/bin/env python3
"""Berlin Rental Agent — finds apartments and reaches out to landlords for you."""
from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt, Confirm

from agent.core import RentalAgent
from agent.db import get_connection, save_user_config, load_user_config


console = Console()

EMAIL_PROVIDERS = {
    "gmail": {
        "imap_server": "imap.gmail.com",
        "smtp_server": "smtp.gmail.com",
        "smtp_port": "587",
        "note": "Use an App Password (https://myaccount.google.com/apppasswords), not your regular password.",
    },
    "outlook": {
        "imap_server": "outlook.office365.com",
        "smtp_server": "smtp-mail.outlook.com",
        "smtp_port": "587",
        "note": "Use your regular Outlook/Hotmail password or an app password if 2FA is enabled.",
    },
    "yahoo": {
        "imap_server": "imap.mail.yahoo.com",
        "smtp_server": "smtp.mail.yahoo.com",
        "smtp_port": "587",
        "note": "Generate an app password at https://login.yahoo.com/account/security",
    },
    "gmx": {
        "imap_server": "imap.gmx.net",
        "smtp_server": "mail.gmx.net",
        "smtp_port": "587",
        "note": "Enable IMAP in GMX settings first.",
    },
    "custom": None,
}


def cli_notification(title: str, body: str):
    console.print()
    console.print(Panel(
        f"[bold]{title}[/bold]\n\n{body}",
        border_style="yellow",
        title="Notification",
    ))
    console.print()


def setup_email_interactive(db_conn) -> str | None:
    """Interactive email setup. Returns the user's email address or None."""
    config = load_user_config(db_conn)

    # Check if already configured
    if config.get("email_address") and config.get("email_configured") == "true":
        email = config["email_address"]
        if Confirm.ask(f"Email already configured as [bold]{email}[/bold]. Keep it?", default=True):
            os.environ["EMAIL_ADDRESS"] = config["email_address"]
            os.environ["EMAIL_PASSWORD"] = config["email_password"]
            os.environ["IMAP_SERVER"] = config["imap_server"]
            os.environ["SMTP_SERVER"] = config["smtp_server"]
            os.environ["SMTP_PORT"] = config["smtp_port"]
            return email

    want_email = Confirm.ask(
        "Would you like to set up email? (needed for notifications & contacting landlords)",
        default=True,
    )
    if not want_email:
        console.print("[yellow]Skipping email. You can still browse listings.[/yellow]")
        return None

    email = Prompt.ask("[bold]Your email address[/bold]")
    if not email or "@" not in email:
        console.print("[yellow]Invalid email. Skipping email setup.[/yellow]")
        return None

    # Detect or ask for provider
    provider = None
    domain = email.split("@")[1].lower()
    if "gmail" in domain:
        provider = "gmail"
        console.print("Detected [bold]Gmail[/bold].")
    elif "outlook" in domain or "hotmail" in domain or "live." in domain:
        provider = "outlook"
        console.print("Detected [bold]Outlook[/bold].")
    elif "yahoo" in domain:
        provider = "yahoo"
        console.print("Detected [bold]Yahoo[/bold].")
    elif "gmx" in domain:
        provider = "gmx"
        console.print("Detected [bold]GMX[/bold].")

    if not provider:
        provider = Prompt.ask(
            "Email provider",
            choices=list(EMAIL_PROVIDERS.keys()),
            default="custom",
        )

    if provider == "custom":
        imap_server = Prompt.ask("IMAP server")
        smtp_server = Prompt.ask("SMTP server")
        smtp_port = Prompt.ask("SMTP port", default="587")
    else:
        p = EMAIL_PROVIDERS[provider]
        imap_server = p["imap_server"]
        smtp_server = p["smtp_server"]
        smtp_port = p["smtp_port"]
        console.print(f"[dim]{p['note']}[/dim]")

    password = Prompt.ask("[bold]Email password / app password[/bold]", password=True)
    if not password:
        console.print("[yellow]No password provided. Skipping email setup.[/yellow]")
        return None

    os.environ["EMAIL_ADDRESS"] = email
    os.environ["EMAIL_PASSWORD"] = password
    os.environ["IMAP_SERVER"] = imap_server
    os.environ["SMTP_SERVER"] = smtp_server
    os.environ["SMTP_PORT"] = smtp_port

    save_user_config(db_conn, "email_address", email)
    save_user_config(db_conn, "email_password", password)
    save_user_config(db_conn, "imap_server", imap_server)
    save_user_config(db_conn, "smtp_server", smtp_server)
    save_user_config(db_conn, "smtp_port", smtp_port)
    save_user_config(db_conn, "email_configured", "true")

    console.print("[green]Email configured successfully.[/green]")
    return email


async def main():
    load_dotenv()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        console.print("[red]Error: ANTHROPIC_API_KEY not set. Copy .env.example to .env and fill it in.[/red]")
        sys.exit(1)

    console.print(Panel.fit(
        "[bold]Berlin Rental Agent[/bold]\n"
        "I'll help you find an apartment in Berlin.\n"
        "I search ImmobilienScout24, Kleinanzeigen, Immowelt, WG-Gesucht & InBerlinWohnen, then reach out to landlords for you.\n\n"
        "Type [bold]quit[/bold] to exit.",
        border_style="blue",
    ))
    console.print()

    db_conn = get_connection()

    # Start in browse mode — agent will gather criteria first.
    # Email setup happens later when the user is ready to reach out.
    agent = RentalAgent(browse_only=True)
    agent.notifier.on_cli_message(cli_notification)

    # Check for previous session
    session_summary = agent.get_session_summary()

    if session_summary:
        intro = (
            f"Hi! Session info: {session_summary} "
            "Welcome me back, give a quick status update, and ask if I want to continue or start fresh."
        )
    else:
        intro = (
            "Hi! I'm looking for an apartment in Berlin. "
            "Please ask me about my requirements — budget, rooms, size, preferred districts, and any other preferences. "
            "Don't search yet, just gather what I need first."
        )

    greeting = await agent.chat(intro)
    console.print(Markdown(greeting))
    console.print()

    while True:
        try:
            user_input = console.input("[bold green]You:[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            console.print("Goodbye! Viel Glück bei der Wohnungssuche!")
            break

        # Check if user wants to set up email (upgrade from browse to full mode)
        if user_input.lower() in ("setup email", "add email", "configure email", "email setup"):
            console.print()
            user_email = setup_email_interactive(db_conn)
            if user_email:
                agent.browse_only = False
                agent.notifier.user_email = user_email
                save_user_config(agent.db, "user_email", user_email)
                console.print("[green]Switched to full mode — I can now contact landlords and send you notifications.[/green]")
                # Tell the agent about the upgrade
                response = await agent.chat(
                    f"The user just configured their email ({user_email}). "
                    "We are now in full mode — you can send emails, contact landlords, and start background searches. "
                    "Let the user know and ask if they want you to reach out to any of the matched listings."
                )
                console.print(Markdown(response))
            else:
                console.print("[yellow]Staying in browse-only mode.[/yellow]")
            console.print()
            continue

        console.print()
        with console.status("[bold blue]Thinking..."):
            response = await agent.chat(user_input)

        console.print(Markdown(response))
        console.print()


if __name__ == "__main__":
    asyncio.run(main())
