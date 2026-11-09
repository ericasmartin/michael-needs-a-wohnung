from __future__ import annotations
import asyncio
import json
import os
from datetime import datetime

from anthropic import Anthropic

from agent.criteria import RentalCriteria
from agent.db import get_connection, save_user_config, load_user_config
from agent.email_client import EmailClient
from agent.notifier import Notifier
from agent.tracker import ListingTracker, MatchStatus, OutreachStatus
from agent.scrapers import ALL_SCRAPERS
from agent.scrapers.base import Listing

SYSTEM_PROMPT = """You are a helpful apartment-hunting assistant specializing in the Berlin/Germany rental market.
You help users find apartments by gathering their criteria, searching listings, and reaching out to landlords.

You speak both English and German fluently. When composing messages to landlords, write in German
(as this is expected in the Berlin rental market) unless the user asks otherwise.

Your workflow:
1. GATHER CRITERIA: Ask the user about their rental needs (budget, rooms, size, districts, move-in date, etc.)
2. SEARCH: When you have enough criteria, call the search_listings tool to find apartments. Every listing found is automatically analyzed against the user's criteria and scored.
3. PRESENT: Show the user ONLY the listings that matched their criteria (score >= 70%). Mention how many were checked total vs how many matched. ALWAYS include the listing URL/link for every listing you present — the user needs to be able to click through.
4. PERSONALIZE: Ask the user for a brief personal message/introduction about themselves (job, hobbies, why Berlin, etc.) to make outreach messages feel genuine and personal.
5. REACH OUT: For matched listings, compose a personalized inquiry email in German (Bewerbung/Anfrage style) and send it. The message should:
   - Sound like it's from the user (use their name, personal details)
   - Be polite and professional (German rental market expects this)
   - Express genuine interest in the specific apartment (reference details from the listing)
   - Include the user's personal intro naturally
   - Ask for a viewing appointment (Besichtigungstermin)
   - ONLY send messages for listings that matched criteria. Never contact listings that didn't match.
6. NOTIFY: When you find good matches, use the notify_user tool to alert the user via both email and CLI.
7. BACKGROUND MODE: After gathering criteria, offer to start a background search using start_background_search. This will periodically check for new listings, analyze them against criteria, and notify + auto-contact matched ones.
8. MONITOR: Check for replies using read_emails and inform the user.
9. STATUS: When the user asks what you've been doing, what you searched, or wants a status update, call get_search_status to show them a table of every listing you've looked at, whether it matched, and whether you contacted them.

IMPORTANT RULES:
- ONLY send outreach for listings with match_status "match" (score >= 70%).
- When recording outreach, always call record_outreach so the tracker stays accurate.
- Always confirm with the user before sending emails (unless in auto mode).
- If the user is in BROWSE-ONLY MODE (no email configured), do NOT offer to send emails, contact landlords, or start background searches. Focus entirely on searching, analyzing, and presenting listings. You can still score/match listings and show the status table. If the user asks about contacting a listing, let them know they can restart with email configured to enable outreach.

Important Berlin rental market context:
- Kaltmiete = cold rent (base rent), Warmmiete = warm rent (includes Nebenkosten/utilities)
- Kaution = deposit, usually 2-3 months cold rent
- Popular districts: Mitte, Kreuzberg, Friedrichshain, Neukölln, Prenzlauer Berg, Charlottenburg, Schöneberg
- Schufa = credit report, often required
- Many landlords prefer German-language inquiries
- Competition is fierce — fast, personalized responses are key"""

TOOLS = [
    {
        "name": "search_listings",
        "description": "Search for rental apartments on ImmobilienScout24, Kleinanzeigen, Immowelt, WG-Gesucht, and InBerlinWohnen. Each listing is automatically analyzed against the user's criteria and scored. Returns all listings with their match status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "default": "Berlin"},
                "districts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Berlin districts to search in, e.g. ['Kreuzberg', 'Neukölln']",
                },
                "min_rooms": {"type": "number", "description": "Minimum number of rooms"},
                "max_rooms": {"type": "number", "description": "Maximum number of rooms"},
                "min_size_sqm": {"type": "number", "description": "Minimum size in square meters"},
                "max_size_sqm": {"type": "number", "description": "Maximum size in square meters"},
                "max_rent": {"type": "number", "description": "Maximum warm rent (Warmmiete) in EUR"},
                "max_cold_rent": {"type": "number", "description": "Maximum cold rent (Kaltmiete) in EUR"},
                "furnished": {"type": "boolean", "description": "Whether the apartment should be furnished"},
                "balcony": {"type": "boolean"},
                "pets_allowed": {"type": "boolean"},
            },
            "required": [],
        },
    },
    {
        "name": "get_listing_details",
        "description": "Get detailed information about a specific listing by URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The listing URL to get details for"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "send_email",
        "description": "Send an email to a landlord. Only use for listings that matched criteria.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject line"},
                "body": {"type": "string", "description": "Plain text email body"},
                "listing_url": {"type": "string", "description": "URL of the listing this email is about (for tracking)"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "read_emails",
        "description": "Read recent emails from the inbox to check for replies from landlords.",
        "input_schema": {
            "type": "object",
            "properties": {
                "search_subject": {"type": "string", "description": "Optional: filter by subject keyword"},
                "limit": {"type": "integer", "description": "Number of emails to fetch (default 10)", "default": 10},
            },
            "required": [],
        },
    },
    {
        "name": "send_contact_form",
        "description": "Fill out a contact form on a listing page. Only use for listings that matched criteria.",
        "input_schema": {
            "type": "object",
            "properties": {
                "listing_url": {"type": "string", "description": "URL of the listing"},
                "name": {"type": "string", "description": "User's full name"},
                "email": {"type": "string", "description": "User's email address"},
                "phone": {"type": "string", "description": "User's phone number (optional)"},
                "message": {"type": "string", "description": "The inquiry message to send"},
            },
            "required": ["listing_url", "name", "email", "message"],
        },
    },
    {
        "name": "record_outreach",
        "description": "Record that a message was sent for a listing. Call this after successfully sending an email or contact form for a listing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "listing_url": {"type": "string", "description": "URL of the listing"},
                "message": {"type": "string", "description": "The message that was sent"},
                "sent_to": {"type": "string", "description": "Email address or 'contact form'"},
                "method": {"type": "string", "enum": ["email", "form"], "description": "How the message was sent"},
            },
            "required": ["listing_url", "message", "sent_to", "method"],
        },
    },
    {
        "name": "notify_user",
        "description": "Send a notification to the user via email and CLI.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short notification title"},
                "body": {"type": "string", "description": "Notification body with details"},
                "listing_urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: URLs of listings to include",
                },
            },
            "required": ["title", "body"],
        },
    },
    {
        "name": "get_search_status",
        "description": "Get a status table of all listings the agent has checked. Shows every listing, whether it matched criteria, match score, whether a message was sent, and links. Use this when the user asks what you've been doing, what you searched, or wants a status update.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "start_background_search",
        "description": "Start background monitoring that periodically checks for new listings, analyzes them against criteria, notifies user of matches, and optionally auto-contacts them.",
        "input_schema": {
            "type": "object",
            "properties": {
                "interval_minutes": {"type": "integer", "description": "Check interval (default: 30)", "default": 30},
                "auto_contact": {"type": "boolean", "description": "Automatically send inquiry messages for matched listings (default: false)", "default": False},
                "user_name": {"type": "string", "description": "User's name for auto-contact messages"},
                "user_intro": {"type": "string", "description": "User's personal intro for auto-contact messages"},
                "city": {"type": "string", "default": "Berlin"},
                "districts": {"type": "array", "items": {"type": "string"}},
                "min_rooms": {"type": "number"},
                "max_rooms": {"type": "number"},
                "min_size_sqm": {"type": "number"},
                "max_size_sqm": {"type": "number"},
                "max_rent": {"type": "number"},
                "max_cold_rent": {"type": "number"},
                "furnished": {"type": "boolean"},
                "balcony": {"type": "boolean"},
                "pets_allowed": {"type": "boolean"},
            },
            "required": [],
        },
    },
]


class RentalAgent:
    def __init__(self, user_email: str | None = None, browse_only: bool = False):
        self.client = Anthropic()
        self.email_client = None
        self.browse_only = browse_only
        self.db = get_connection()
        self.notifier = Notifier(user_email=user_email)
        self.tracker = ListingTracker(db_conn=self.db)
        self.conversation: list[dict] = []
        self.scrapers = [cls() for cls in ALL_SCRAPERS]
        self._background_task: asyncio.Task | None = None

        if user_email:
            save_user_config(self.db, "user_email", user_email)

    def get_session_summary(self) -> str:
        """Summary of previous session data for returning users."""
        stats = self.tracker.stats()
        if stats["total"] == 0:
            return ""
        return (
            f"Resuming from previous session: {stats['total']} listings tracked, "
            f"{stats['matches']} matches, {stats['contacted']} contacted, "
            f"{stats['replied']} replies."
        )

    def _init_email(self):
        if self.email_client is None:
            try:
                self.email_client = EmailClient()
            except KeyError as e:
                return f"Email not configured. Please set {e} in your .env file."
        return None

    def _analyze_match(self, listing: Listing, criteria: RentalCriteria) -> tuple[float, str]:
        """Use Claude to analyze how well a listing matches criteria. Returns (score, reason)."""
        prompt = (
            f"Analyze this apartment listing against the user's criteria. "
            f"Return a JSON object with 'score' (0.0-1.0) and 'reason' (brief explanation).\n\n"
            f"CRITERIA:\n{criteria.summary()}\n\n"
            f"LISTING:\n"
            f"Title: {listing.title}\n"
            f"Price (warm): {listing.price_warm or 'unknown'} EUR\n"
            f"Price (cold): {listing.price_cold or 'unknown'} EUR\n"
            f"Rooms: {listing.rooms or 'unknown'}\n"
            f"Size: {listing.size_sqm or 'unknown'} m²\n"
            f"Address: {listing.address or 'unknown'}\n"
            f"District: {listing.district or 'unknown'}\n"
            f"Description: {(listing.description or '')[:500]}\n"
            f"Features: {', '.join(listing.features) if listing.features else 'none listed'}\n\n"
            f"Score guide:\n"
            f"- 1.0 = perfect match on all criteria\n"
            f"- 0.7+ = good match, meets key criteria (budget, size, location)\n"
            f"- 0.4-0.69 = partial match, some criteria met\n"
            f"- <0.4 = poor match, key criteria not met (over budget, wrong area, too small)\n\n"
            f"If price or size data is missing, be generous but note it. "
            f"Budget is the most important criterion — anything clearly over budget gets <0.4.\n\n"
            f"Respond with ONLY a JSON object: {{\"score\": 0.X, \"reason\": \"...\"}}"
        )

        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            # Extract JSON from response
            if "{" in text:
                text = text[text.index("{"):text.rindex("}") + 1]
            data = json.loads(text)
            return float(data["score"]), data["reason"]
        except Exception as e:
            # Fallback: basic rule-based matching
            return self._basic_match(listing, criteria)

    def _basic_match(self, listing: Listing, criteria: RentalCriteria) -> tuple[float, str]:
        """Fallback rule-based matching if AI analysis fails."""
        score = 0.5
        reasons = []

        if criteria.max_rent and listing.price_warm:
            if listing.price_warm <= criteria.max_rent:
                score += 0.2
                reasons.append("within budget")
            else:
                score -= 0.3
                reasons.append(f"over budget ({listing.price_warm} > {criteria.max_rent})")

        if criteria.min_rooms and listing.rooms:
            if listing.rooms >= criteria.min_rooms:
                score += 0.1
                reasons.append(f"{listing.rooms} rooms OK")
            else:
                score -= 0.2
                reasons.append(f"too few rooms ({listing.rooms} < {criteria.min_rooms})")

        if criteria.min_size_sqm and listing.size_sqm:
            if listing.size_sqm >= criteria.min_size_sqm:
                score += 0.1
                reasons.append(f"{listing.size_sqm} m² OK")
            else:
                score -= 0.15
                reasons.append(f"too small ({listing.size_sqm} < {criteria.min_size_sqm})")

        score = max(0.0, min(1.0, score))
        reason = "; ".join(reasons) if reasons else "limited data to evaluate"
        return score, reason

    async def _handle_tool_call(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "search_listings":
            return await self._tool_search(tool_input)
        elif tool_name == "get_listing_details":
            return await self._tool_details(tool_input)
        elif tool_name == "send_email":
            return self._tool_send_email(tool_input)
        elif tool_name == "read_emails":
            return self._tool_read_emails(tool_input)
        elif tool_name == "send_contact_form":
            return await self._tool_contact_form(tool_input)
        elif tool_name == "record_outreach":
            return self._tool_record_outreach(tool_input)
        elif tool_name == "notify_user":
            return self._tool_notify_user(tool_input)
        elif tool_name == "get_search_status":
            return self._tool_get_search_status(tool_input)
        elif tool_name == "start_background_search":
            return await self._tool_start_background_search(tool_input)
        return f"Unknown tool: {tool_name}"

    async def _tool_search(self, params: dict) -> str:
        criteria = RentalCriteria(
            city=params.get("city", "Berlin"),
            districts=params.get("districts", []),
            min_rooms=params.get("min_rooms"),
            max_rooms=params.get("max_rooms"),
            min_size_sqm=params.get("min_size_sqm"),
            max_size_sqm=params.get("max_size_sqm"),
            max_rent=params.get("max_rent"),
            max_cold_rent=params.get("max_cold_rent"),
            furnished=params.get("furnished"),
            balcony=params.get("balcony"),
            pets_allowed=params.get("pets_allowed"),
        )

        all_listings = []
        for scraper in self.scrapers:
            try:
                results = await scraper.search(criteria)
                all_listings.extend(results)
            except Exception as e:
                print(f"Error with {scraper.name}: {e}")

        if not all_listings:
            return "No listings found matching your criteria. Try broadening your search."

        # Track and analyze each listing
        matches = []
        non_matches = []
        for listing in all_listings:
            entry = self.tracker.add(listing)
            if entry.match_status == MatchStatus.PENDING:
                score, reason = self._analyze_match(listing, criteria)
                self.tracker.mark_match(listing.url, score, reason)
                entry = self.tracker.get(listing.url)

            if entry.match_status == MatchStatus.MATCH:
                matches.append(entry)
            else:
                non_matches.append(entry)

        # Build response
        # Sort matches by score descending
        matches.sort(key=lambda e: e.match_score, reverse=True)

        parts = [f"Searched {len(all_listings)} listings. **{len(matches)} matched** your criteria, {len(non_matches)} did not.\n"]

        if matches:
            # Top picks — highest scoring listings
            top = matches[:5]
            parts.append("## Top Picks\n")
            for i, entry in enumerate(top, 1):
                l = entry.listing
                price = l.price_warm or l.price_cold or "?"
                rooms = l.rooms or "?"
                size = f"{l.size_sqm} m²" if l.size_sqm else "?"
                addr = l.address or l.district or ""
                parts.append(
                    f"{i}. **{l.title}** — {rooms} rooms, {size}, {price}€\n"
                    f"   {addr}\n"
                    f"   Score: {entry.match_score:.0%} — {entry.match_reason}\n"
                    f"   {l.url}\n"
                )

            # Remaining matches
            rest = matches[5:]
            if rest:
                parts.append(f"\n## Other Matches ({len(rest)} more)\n")
                for i, entry in enumerate(rest, 6):
                    l = entry.listing
                    price = l.price_warm or l.price_cold or "?"
                    rooms = l.rooms or "?"
                    size = f"{l.size_sqm} m²" if l.size_sqm else "?"
                    parts.append(
                        f"{i}. {l.title} — {rooms}r, {size}, {price}€ | "
                        f"Score: {entry.match_score:.0%} | {l.url}\n"
                    )

        if non_matches:
            parts.append(f"\n*{len(non_matches)} listings didn't match (use get_search_status to see all)*")

        return "\n".join(parts)

    async def _tool_details(self, params: dict) -> str:
        url = params["url"]
        listing = None

        for scraper in self.scrapers:
            if scraper.name == "ImmobilienScout24" and "immobilienscout24" in url:
                listing = await scraper.get_listing_details(url)
                break
            elif scraper.name == "Kleinanzeigen" and "kleinanzeigen" in url:
                listing = await scraper.get_listing_details(url)
                break

        if not listing:
            return "Could not fetch details for this listing."

        self.tracker.add(listing)
        details = listing.summary()
        if listing.description:
            details += f"\n\nDescription:\n{listing.description}"
        if listing.contact_name:
            details += f"\n\nContact: {listing.contact_name}"
        if listing.contact_email:
            details += f"\nEmail: {listing.contact_email}"

        return details

    def _tool_send_email(self, params: dict) -> str:
        # Check if listing is tracked and matched
        listing_url = params.get("listing_url")
        if listing_url:
            entry = self.tracker.get(listing_url)
            if entry and entry.match_status == MatchStatus.NO_MATCH:
                return f"BLOCKED: This listing (score {entry.match_score:.0%}) did not match criteria. Reason: {entry.match_reason}. Do not contact non-matching listings."

        err = self._init_email()
        if err:
            return err
        success = self.email_client.send_email(
            to=params["to"],
            subject=params["subject"],
            body=params["body"],
        )
        if success:
            # Auto-record outreach if listing_url provided
            if listing_url:
                self.tracker.mark_outreach(listing_url, params["body"], params["to"], "email")
            return f"Email sent successfully to {params['to']}"
        return "Failed to send email. Check your email configuration."

    def _tool_read_emails(self, params: dict) -> str:
        err = self._init_email()
        if err:
            return err
        messages = self.email_client.read_inbox(
            limit=params.get("limit", 10),
            search_subject=params.get("search_subject"),
        )
        if not messages:
            return "No emails found."

        result = []
        for msg in messages:
            result.append(
                f"From: {msg['from']}\n"
                f"Subject: {msg['subject']}\n"
                f"Date: {msg['date']}\n"
                f"Body: {msg['body'][:500]}\n---"
            )
        return "\n".join(result)

    async def _tool_contact_form(self, params: dict) -> str:
        url = params["listing_url"]

        # Check match status
        entry = self.tracker.get(url)
        if entry and entry.match_status == MatchStatus.NO_MATCH:
            return f"BLOCKED: This listing (score {entry.match_score:.0%}) did not match criteria. Do not contact non-matching listings."

        from playwright.async_api import async_playwright

        name = params["name"]
        user_email = params["email"]
        phone = params.get("phone", "")
        message = params["message"]

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale="de-DE",
            )
            page = await context.new_page()

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                try:
                    consent_btn = page.locator("#uc-btn-accept-banner, #gdpr-banner-accept")
                    await consent_btn.click(timeout=3000)
                except Exception:
                    pass

                await page.wait_for_timeout(2000)

                if "immobilienscout24" in url:
                    result = await self._fill_immoscout_form(page, name, user_email, phone, message)
                elif "kleinanzeigen" in url:
                    result = await self._fill_kleinanzeigen_form(page, name, user_email, phone, message)
                else:
                    result = "Unsupported site for contact form submission."

                # Track outreach
                if "successfully" in result:
                    self.tracker.mark_outreach(url, message, "contact form", "form")

                return result

            except Exception as e:
                return f"Error submitting contact form: {e}"
            finally:
                await browser.close()

    async def _fill_immoscout_form(self, page, name, email, phone, message) -> str:
        try:
            contact_btn = page.locator("a[data-qa='sendButton'], button.button-primary")
            await contact_btn.click(timeout=5000)
            await page.wait_for_timeout(1000)

            name_input = page.locator("input[name='contactName'], input#contactName")
            await name_input.fill(name, timeout=3000)

            email_input = page.locator("input[name='contactEmail'], input#contactEmail")
            await email_input.fill(email, timeout=3000)

            if phone:
                phone_input = page.locator("input[name='contactPhone'], input#contactPhone")
                try:
                    await phone_input.fill(phone, timeout=2000)
                except Exception:
                    pass

            msg_input = page.locator("textarea[name='message'], textarea#contactMessage")
            await msg_input.fill(message, timeout=3000)

            submit_btn = page.locator("button[type='submit'], button[data-qa='sendButton']")
            await submit_btn.click(timeout=3000)
            await page.wait_for_timeout(2000)

            return "Contact form submitted successfully on ImmobilienScout24."
        except Exception as e:
            return f"Could not fill ImmobilienScout24 contact form: {e}. The listing may require login or have a different form layout."

    async def _fill_kleinanzeigen_form(self, page, name, email, phone, message) -> str:
        try:
            reply_btn = page.locator("a#viewad-contact, button.contactButton")
            await reply_btn.click(timeout=5000)
            await page.wait_for_timeout(1000)

            name_input = page.locator("input#contactName, input[name='contactName']")
            await name_input.fill(name, timeout=3000)

            email_input = page.locator("input#contactEmail, input[name='contactEmail']")
            await email_input.fill(email, timeout=3000)

            msg_input = page.locator("textarea#contactMessage, textarea[name='message']")
            await msg_input.fill(message, timeout=3000)

            submit_btn = page.locator("button[type='submit']")
            await submit_btn.click(timeout=3000)
            await page.wait_for_timeout(2000)

            return "Contact form submitted successfully on Kleinanzeigen."
        except Exception as e:
            return f"Could not fill Kleinanzeigen contact form: {e}. The listing may require login."

    def _tool_record_outreach(self, params: dict) -> str:
        url = params["listing_url"]
        entry = self.tracker.get(url)
        if not entry:
            return "Listing not found in tracker."
        self.tracker.mark_outreach(url, params["message"], params["sent_to"], params["method"])
        return f"Outreach recorded for {entry.listing.title}."

    def _tool_notify_user(self, params: dict) -> str:
        title = params["title"]
        body = params["body"]
        listing_urls = params.get("listing_urls", [])

        listings = []
        for url in listing_urls:
            entry = self.tracker.get(url)
            if entry:
                listings.append(entry.listing)

        self.notifier.notify(title, body, listings if listings else None)

        notified_via = ["CLI"]
        if self.notifier.user_email:
            notified_via.append(f"email ({self.notifier.user_email})")
        return f"User notified via {' and '.join(notified_via)}."

    def _tool_get_search_status(self, _params: dict) -> str:
        stats = self.tracker.stats()
        header = (
            f"## Search Status\n"
            f"Total listings checked: {stats['total']}\n"
            f"Matches: {stats['matches']} | Partial: {stats['partial']} | "
            f"No match: {stats['no_match']} | Pending: {stats['pending']}\n"
            f"Contacted: {stats['contacted']} | Replies: {stats['replied']}\n\n"
        )
        table = self.tracker.summary_table()
        return header + table

    async def _tool_start_background_search(self, params: dict) -> str:
        interval = params.get("interval_minutes", 30)
        auto_contact = params.get("auto_contact", False)
        user_name = params.get("user_name", "")
        user_intro = params.get("user_intro", "")

        criteria = RentalCriteria(
            city=params.get("city", "Berlin"),
            districts=params.get("districts", []),
            min_rooms=params.get("min_rooms"),
            max_rooms=params.get("max_rooms"),
            min_size_sqm=params.get("min_size_sqm"),
            max_size_sqm=params.get("max_size_sqm"),
            max_rent=params.get("max_rent"),
            max_cold_rent=params.get("max_cold_rent"),
            furnished=params.get("furnished"),
            balcony=params.get("balcony"),
            pets_allowed=params.get("pets_allowed"),
        )

        if self._background_task and not self._background_task.done():
            self._background_task.cancel()

        self._background_task = asyncio.create_task(
            self._background_monitor(criteria, interval, auto_contact, user_name, user_intro)
        )
        mode = "auto-contact enabled" if auto_contact else "notify only (no auto-contact)"
        return (
            f"Background search started! Checking every {interval} minutes. Mode: {mode}.\n"
            f"Criteria: {criteria.summary()}\n"
            f"Notifications via CLI"
            + (f" and email ({self.notifier.user_email})" if self.notifier.user_email else "")
            + "."
        )

    async def _background_monitor(self, criteria: RentalCriteria, interval_minutes: int,
                                    auto_contact: bool, user_name: str, user_intro: str):
        """Periodically scrapes, analyzes matches, notifies, and optionally contacts."""
        while True:
            await asyncio.sleep(interval_minutes * 60)

            try:
                new_listings = []
                for scraper in self.scrapers:
                    try:
                        results = await scraper.search(criteria)
                        for listing in results:
                            if listing.url not in self.tracker.tracked:
                                new_listings.append(listing)
                    except Exception:
                        continue

                # Analyze each new listing
                new_matches = []
                for listing in new_listings:
                    self.tracker.add(listing)
                    score, reason = self._analyze_match(listing, criteria)
                    self.tracker.mark_match(listing.url, score, reason)
                    entry = self.tracker.get(listing.url)
                    if entry.match_status == MatchStatus.MATCH:
                        new_matches.append(entry)

                # Notify about matches
                if new_matches:
                    match_listings = [e.listing for e in new_matches]
                    self.notifier.notify_new_matches(match_listings)

                    # Auto-contact if enabled
                    if auto_contact and user_name and self.notifier.user_email:
                        for entry in new_matches:
                            msg = self._generate_inquiry_message(entry.listing, user_name, user_intro)
                            if entry.listing.contact_email:
                                err = self._init_email()
                                if not err:
                                    success = self.email_client.send_email(
                                        to=entry.listing.contact_email,
                                        subject=f"Wohnungsanfrage: {entry.listing.title}",
                                        body=msg,
                                    )
                                    if success:
                                        self.tracker.mark_outreach(
                                            entry.listing.url, msg,
                                            entry.listing.contact_email, "email"
                                        )

                # Notify about non-matches found (just a count for awareness)
                non_match_count = len(new_listings) - len(new_matches)
                if new_listings and not new_matches:
                    self.notifier.notify(
                        "Background scan complete",
                        f"Checked {len(new_listings)} new listings — none matched your criteria.",
                    )

                # Check for landlord replies
                err = self._init_email()
                if not err:
                    messages = self.email_client.read_inbox(limit=5)
                    for msg in messages:
                        subj = msg.get("subject", "").lower()
                        sender = msg.get("from", "").lower()
                        if any(kw in subj or kw in sender for kw in [
                            "besichtigung", "wohnung", "anfrage", "immobilienscout",
                            "kleinanzeigen", "termin", "viewing", "apartment",
                        ]):
                            self.notifier.notify_landlord_reply(
                                sender=msg["from"],
                                subject=msg["subject"],
                                snippet=msg["body"],
                            )

            except asyncio.CancelledError:
                break
            except Exception:
                continue

    def _generate_inquiry_message(self, listing: Listing, user_name: str, user_intro: str) -> str:
        """Use Claude to generate a personalized inquiry message in German."""
        prompt = (
            f"Write a short, polite apartment inquiry email in German for this listing. "
            f"It should sound personal and genuine, like it's from {user_name}.\n\n"
            f"User info: {user_intro}\n\n"
            f"Listing:\n"
            f"Title: {listing.title}\n"
            f"Address: {listing.address or 'not specified'}\n"
            f"Price: {listing.price_warm or listing.price_cold or 'not specified'} EUR\n"
            f"Rooms: {listing.rooms or 'not specified'}\n"
            f"Size: {listing.size_sqm or 'not specified'} m²\n\n"
            f"The email should:\n"
            f"- Open with a greeting\n"
            f"- Express interest in the specific apartment (reference details)\n"
            f"- Briefly introduce the user naturally\n"
            f"- Ask for a Besichtigungstermin\n"
            f"- Be 6-10 sentences max\n"
            f"- Sound human, not like a template\n\n"
            f"Return ONLY the email body text, no subject line or headers."
        )

        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text.strip()
        except Exception:
            # Fallback template
            return (
                f"Sehr geehrte Damen und Herren,\n\n"
                f"mit großem Interesse habe ich Ihre Anzeige \"{listing.title}\" gesehen. "
                f"Ich suche derzeit eine Wohnung in Berlin und Ihre Wohnung spricht mich sehr an.\n\n"
                f"{user_intro}\n\n"
                f"Ich würde mich sehr freuen, die Wohnung bei einem Besichtigungstermin kennenzulernen.\n\n"
                f"Mit freundlichen Grüßen,\n{user_name}"
            )

    # Tools that require email — excluded in browse-only mode
    EMAIL_TOOLS = {"send_email", "read_emails", "send_contact_form", "record_outreach", "notify_user", "start_background_search"}

    def _get_tools(self) -> list[dict]:
        if self.browse_only:
            return [t for t in TOOLS if t["name"] not in self.EMAIL_TOOLS]
        return TOOLS

    def _get_system_prompt(self) -> str:
        prompt = SYSTEM_PROMPT
        if self.browse_only:
            prompt += (
                "\n\nIMPORTANT: The user is in BROWSE-ONLY mode (no email configured). "
                "You can search, analyze, score, and present listings. You can show the status table. "
                "But do NOT offer to send emails, contact landlords, or start background monitoring. "
                "If the user likes some listings and wants to reach out, tell them to type 'setup email' "
                "to configure their email and switch to full mode. Focus on helping them explore what's available."
            )
        return prompt

    async def chat(self, user_message: str) -> str:
        self.conversation.append({"role": "user", "content": user_message})

        tools = self._get_tools()
        system = self._get_system_prompt()

        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=system,
            tools=tools,
            messages=self.conversation,
        )

        while response.stop_reason == "tool_use":
            assistant_content = response.content
            self.conversation.append({"role": "assistant", "content": assistant_content})

            tool_results = []
            for block in assistant_content:
                if block.type == "tool_use":
                    print(f"  [Calling {block.name}...]")
                    result = await self._handle_tool_call(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            self.conversation.append({"role": "user", "content": tool_results})

            response = self.client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=system,
                tools=tools,
                messages=self.conversation,
            )

        final_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                final_text += block.text

        self.conversation.append({"role": "assistant", "content": response.content})
        return final_text
