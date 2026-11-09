"""Tracks every listing the agent has seen, its match status, and outreach history.
Persists to SQLite so state survives across sessions."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from agent.scrapers.base import Listing


class MatchStatus(Enum):
    PENDING = "pending"
    MATCH = "match"
    NO_MATCH = "no_match"
    PARTIAL = "partial"


class OutreachStatus(Enum):
    NONE = "none"
    SENT = "sent"
    REPLIED = "replied"
    FORM_SENT = "form_sent"


@dataclass
class TrackedListing:
    listing: Listing
    first_seen: datetime = field(default_factory=datetime.now)
    match_status: MatchStatus = MatchStatus.PENDING
    match_score: float = 0.0
    match_reason: str = ""
    outreach_status: OutreachStatus = OutreachStatus.NONE
    outreach_message: str = ""
    outreach_to: str = ""
    outreach_time: datetime | None = None
    reply_snippet: str = ""


class ListingTracker:
    def __init__(self, db_conn=None):
        self.tracked: dict[str, TrackedListing] = {}
        self._db = db_conn
        if self._db:
            self._load_from_db()

    def _load_from_db(self):
        from agent.db import load_all_tracked
        self.tracked = load_all_tracked(self._db)

    def _persist(self, entry: TrackedListing):
        if self._db:
            from agent.db import save_tracked_listing
            save_tracked_listing(self._db, entry)

    def add(self, listing: Listing) -> TrackedListing:
        if listing.url in self.tracked:
            self.tracked[listing.url].listing = listing
            self._persist(self.tracked[listing.url])
            return self.tracked[listing.url]
        entry = TrackedListing(listing=listing)
        self.tracked[listing.url] = entry
        self._persist(entry)
        return entry

    def get(self, url: str) -> TrackedListing | None:
        return self.tracked.get(url)

    def mark_match(self, url: str, score: float, reason: str):
        entry = self.tracked.get(url)
        if entry:
            entry.match_score = score
            entry.match_reason = reason
            if score >= 0.7:
                entry.match_status = MatchStatus.MATCH
            elif score >= 0.4:
                entry.match_status = MatchStatus.PARTIAL
            else:
                entry.match_status = MatchStatus.NO_MATCH
            self._persist(entry)

    def mark_outreach(self, url: str, message: str, to: str, method: str = "email"):
        entry = self.tracked.get(url)
        if entry:
            entry.outreach_status = OutreachStatus.FORM_SENT if method == "form" else OutreachStatus.SENT
            entry.outreach_message = message
            entry.outreach_to = to
            entry.outreach_time = datetime.now()
            self._persist(entry)

    def mark_reply(self, url: str, snippet: str):
        entry = self.tracked.get(url)
        if entry:
            entry.outreach_status = OutreachStatus.REPLIED
            entry.reply_snippet = snippet
            self._persist(entry)

    def get_matches(self) -> list[TrackedListing]:
        return [e for e in self.tracked.values() if e.match_status == MatchStatus.MATCH]

    def get_pending(self) -> list[TrackedListing]:
        return [e for e in self.tracked.values() if e.match_status == MatchStatus.PENDING]

    def get_contacted(self) -> list[TrackedListing]:
        return [e for e in self.tracked.values() if e.outreach_status != OutreachStatus.NONE]

    def summary_table(self) -> str:
        if not self.tracked:
            return "No listings tracked yet."

        rows = []
        rows.append("| # | Title | Price | Rooms | Size | Match | Score | Contacted | Link |")
        rows.append("|---|-------|-------|-------|------|-------|-------|-----------|------|")

        for i, (url, entry) in enumerate(self.tracked.items(), 1):
            l = entry.listing
            title = (l.title[:30] + "...") if len(l.title) > 30 else l.title
            price = f"{l.price_warm} EUR" if l.price_warm else (f"{l.price_cold} EUR" if l.price_cold else "—")
            rooms = str(l.rooms) if l.rooms else "—"
            size = f"{l.size_sqm} m²" if l.size_sqm else "—"

            match_icon = {
                MatchStatus.MATCH: "YES",
                MatchStatus.PARTIAL: "PARTIAL",
                MatchStatus.NO_MATCH: "NO",
                MatchStatus.PENDING: "...",
            }[entry.match_status]

            score = f"{entry.match_score:.0%}" if entry.match_status != MatchStatus.PENDING else "—"

            contacted = {
                OutreachStatus.NONE: "No",
                OutreachStatus.SENT: "Email sent",
                OutreachStatus.FORM_SENT: "Form sent",
                OutreachStatus.REPLIED: "REPLIED!",
            }[entry.outreach_status]

            rows.append(
                f"| {i} | {title} | {price} | {rooms} | {size} | {match_icon} | {score} | {contacted} | [Link]({url}) |"
            )

        return "\n".join(rows)

    def stats(self) -> dict:
        total = len(self.tracked)
        matches = sum(1 for e in self.tracked.values() if e.match_status == MatchStatus.MATCH)
        partial = sum(1 for e in self.tracked.values() if e.match_status == MatchStatus.PARTIAL)
        no_match = sum(1 for e in self.tracked.values() if e.match_status == MatchStatus.NO_MATCH)
        pending = sum(1 for e in self.tracked.values() if e.match_status == MatchStatus.PENDING)
        contacted = sum(1 for e in self.tracked.values() if e.outreach_status != OutreachStatus.NONE)
        replied = sum(1 for e in self.tracked.values() if e.outreach_status == OutreachStatus.REPLIED)
        return {
            "total": total,
            "matches": matches,
            "partial": partial,
            "no_match": no_match,
            "pending": pending,
            "contacted": contacted,
            "replied": replied,
        }
