"""SQLite persistence for listings, tracking state, and user config."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from agent.scrapers.base import Listing
from agent.tracker import TrackedListing, MatchStatus, OutreachStatus

DB_PATH = Path(__file__).parent.parent / "data" / "rental_agent.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_tables(conn)
    return conn


def _ensure_tables(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS listings (
            url TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            source TEXT NOT NULL,
            price_warm REAL,
            price_cold REAL,
            rooms REAL,
            size_sqm REAL,
            address TEXT,
            district TEXT,
            description TEXT,
            contact_email TEXT,
            contact_name TEXT,
            contact_form_url TEXT,
            image_urls TEXT,
            available_from TEXT,
            features TEXT,
            first_seen TEXT NOT NULL,
            match_status TEXT NOT NULL DEFAULT 'pending',
            match_score REAL NOT NULL DEFAULT 0.0,
            match_reason TEXT NOT NULL DEFAULT '',
            outreach_status TEXT NOT NULL DEFAULT 'none',
            outreach_message TEXT NOT NULL DEFAULT '',
            outreach_to TEXT NOT NULL DEFAULT '',
            outreach_time TEXT,
            reply_snippet TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS user_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    conn.commit()


def save_tracked_listing(conn: sqlite3.Connection, entry: TrackedListing):
    l = entry.listing
    conn.execute("""
        INSERT INTO listings (
            url, title, source, price_warm, price_cold, rooms, size_sqm,
            address, district, description, contact_email, contact_name,
            contact_form_url, image_urls, available_from, features,
            first_seen, match_status, match_score, match_reason,
            outreach_status, outreach_message, outreach_to, outreach_time,
            reply_snippet
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            title=excluded.title, price_warm=excluded.price_warm,
            price_cold=excluded.price_cold, rooms=excluded.rooms,
            size_sqm=excluded.size_sqm, address=excluded.address,
            district=excluded.district, description=excluded.description,
            contact_email=excluded.contact_email, contact_name=excluded.contact_name,
            contact_form_url=excluded.contact_form_url, image_urls=excluded.image_urls,
            available_from=excluded.available_from, features=excluded.features,
            match_status=excluded.match_status, match_score=excluded.match_score,
            match_reason=excluded.match_reason, outreach_status=excluded.outreach_status,
            outreach_message=excluded.outreach_message, outreach_to=excluded.outreach_to,
            outreach_time=excluded.outreach_time, reply_snippet=excluded.reply_snippet
    """, (
        l.url, l.title, l.source, l.price_warm, l.price_cold, l.rooms, l.size_sqm,
        l.address, l.district, l.description, l.contact_email, l.contact_name,
        l.contact_form_url, json.dumps(l.image_urls), l.available_from,
        json.dumps(l.features), entry.first_seen.isoformat(),
        entry.match_status.value, entry.match_score, entry.match_reason,
        entry.outreach_status.value, entry.outreach_message, entry.outreach_to,
        entry.outreach_time.isoformat() if entry.outreach_time else None,
        entry.reply_snippet,
    ))
    conn.commit()


def load_all_tracked(conn: sqlite3.Connection) -> dict[str, TrackedListing]:
    rows = conn.execute("SELECT * FROM listings").fetchall()
    result = {}
    for row in rows:
        listing = Listing(
            title=row["title"],
            url=row["url"],
            source=row["source"],
            price_warm=row["price_warm"],
            price_cold=row["price_cold"],
            rooms=row["rooms"],
            size_sqm=row["size_sqm"],
            address=row["address"],
            district=row["district"],
            description=row["description"],
            contact_email=row["contact_email"],
            contact_name=row["contact_name"],
            contact_form_url=row["contact_form_url"],
            image_urls=json.loads(row["image_urls"]) if row["image_urls"] else [],
            available_from=row["available_from"],
            features=json.loads(row["features"]) if row["features"] else [],
        )
        entry = TrackedListing(
            listing=listing,
            first_seen=datetime.fromisoformat(row["first_seen"]),
            match_status=MatchStatus(row["match_status"]),
            match_score=row["match_score"],
            match_reason=row["match_reason"],
            outreach_status=OutreachStatus(row["outreach_status"]),
            outreach_message=row["outreach_message"],
            outreach_to=row["outreach_to"],
            outreach_time=datetime.fromisoformat(row["outreach_time"]) if row["outreach_time"] else None,
            reply_snippet=row["reply_snippet"],
        )
        result[row["url"]] = entry
    return result


def save_user_config(conn: sqlite3.Connection, key: str, value: str):
    conn.execute(
        "INSERT INTO user_config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def load_user_config(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT key, value FROM user_config").fetchall()
    return {row["key"]: row["value"] for row in rows}
