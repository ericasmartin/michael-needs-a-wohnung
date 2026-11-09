"""Notification system — emails the user and prints to CLI when matches are found."""
from __future__ import annotations

import os
from datetime import datetime

from agent.email_client import EmailClient
from agent.scrapers.base import Listing


class Notifier:
    def __init__(self, user_email: str | None = None):
        self.user_email = user_email
        self.email_client = None
        self.cli_callbacks: list = []  # functions to call for CLI notifications

    def _init_email(self) -> EmailClient | None:
        if self.email_client is None:
            try:
                self.email_client = EmailClient()
            except KeyError:
                return None
        return self.email_client

    def on_cli_message(self, callback):
        """Register a callback for CLI notifications: callback(title, body)"""
        self.cli_callbacks.append(callback)

    def notify(self, title: str, body: str, listings: list[Listing] | None = None):
        """Send notification via both email and CLI."""
        # CLI notification
        for cb in self.cli_callbacks:
            cb(title, body)

        # Email notification to user
        if self.user_email:
            self._send_email_notification(title, body, listings)

    def notify_new_matches(self, listings: list[Listing]):
        if not listings:
            return

        title = f"🏠 {len(listings)} new apartment(s) found!"
        body_parts = [f"Found {len(listings)} new listing(s) matching your criteria:\n"]
        for i, listing in enumerate(listings, 1):
            body_parts.append(f"--- Listing {i} ---")
            body_parts.append(listing.summary())
            body_parts.append("")

        body_parts.append("Open the Berlin Rental Agent to review and reach out!")
        body = "\n".join(body_parts)

        self.notify(title, body, listings)

    def notify_landlord_reply(self, sender: str, subject: str, snippet: str):
        title = "📬 Reply from landlord!"
        body = (
            f"You got a reply!\n\n"
            f"From: {sender}\n"
            f"Subject: {subject}\n\n"
            f"{snippet[:500]}"
        )
        self.notify(title, body)

    def _send_email_notification(self, title: str, body: str, listings: list[Listing] | None = None):
        client = self._init_email()
        if not client:
            return

        # Build HTML version for nicer email
        html_parts = [
            "<html><body>",
            f"<h2>{title}</h2>",
        ]

        if listings:
            for i, listing in enumerate(listings, 1):
                html_parts.append(f"<div style='margin-bottom:20px;padding:15px;border:1px solid #ddd;border-radius:8px;'>")
                html_parts.append(f"<h3>Listing {i}: {listing.title}</h3>")
                if listing.price_warm:
                    html_parts.append(f"<p><strong>Warm rent:</strong> {listing.price_warm} EUR</p>")
                if listing.price_cold:
                    html_parts.append(f"<p><strong>Cold rent:</strong> {listing.price_cold} EUR</p>")
                if listing.rooms:
                    html_parts.append(f"<p><strong>Rooms:</strong> {listing.rooms}</p>")
                if listing.size_sqm:
                    html_parts.append(f"<p><strong>Size:</strong> {listing.size_sqm} m²</p>")
                if listing.address:
                    html_parts.append(f"<p><strong>Address:</strong> {listing.address}</p>")
                html_parts.append(f"<p><a href='{listing.url}'>View listing</a></p>")
                html_parts.append("</div>")
        else:
            html_parts.append(f"<pre>{body}</pre>")

        html_parts.append(
            "<p style='color:#888;font-size:12px;margin-top:20px;'>"
            "Sent by Berlin Rental Agent</p>"
        )
        html_parts.append("</body></html>")

        client.send_email(
            to=self.user_email,
            subject=f"Berlin Rental Agent: {title}",
            body=body,
            html_body="".join(html_parts),
        )
