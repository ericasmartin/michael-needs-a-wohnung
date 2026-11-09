from __future__ import annotations
import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlencode

from playwright.async_api import async_playwright

from agent.criteria import RentalCriteria
from .base import BaseScraper, Listing

# Persistent browser state directory — saves cookies/session across runs
BROWSER_STATE_DIR = Path(__file__).parent.parent.parent / "data" / "browser_state"

BERLIN_DISTRICT_CODES = {
    "charlottenburg": "charlottenburg-wilmersdorf",
    "charlottenburg-wilmersdorf": "charlottenburg-wilmersdorf",
    "friedrichshain": "friedrichshain-kreuzberg",
    "friedrichshain-kreuzberg": "friedrichshain-kreuzberg",
    "kreuzberg": "friedrichshain-kreuzberg",
    "lichtenberg": "lichtenberg",
    "marzahn": "marzahn-hellersdorf",
    "marzahn-hellersdorf": "marzahn-hellersdorf",
    "mitte": "mitte",
    "neukoelln": "neukoelln",
    "neukölln": "neukoelln",
    "pankow": "pankow",
    "prenzlauer berg": "pankow",
    "reinickendorf": "reinickendorf",
    "schoeneberg": "tempelhof-schoeneberg",
    "schöneberg": "tempelhof-schoeneberg",
    "tempelhof": "tempelhof-schoeneberg",
    "tempelhof-schöneberg": "tempelhof-schoeneberg",
    "spandau": "spandau",
    "steglitz": "steglitz-zehlendorf",
    "steglitz-zehlendorf": "steglitz-zehlendorf",
    "treptow": "treptow-koepenick",
    "treptow-köpenick": "treptow-koepenick",
    "wedding": "mitte",
    "wilmersdorf": "charlottenburg-wilmersdorf",
    "zehlendorf": "steglitz-zehlendorf",
}


class ImmoscoutScraper(BaseScraper):
    name = "ImmobilienScout24"

    def _build_search_url(self, criteria: RentalCriteria) -> str:
        base = "https://www.immobilienscout24.de/Suche/de/berlin/berlin"

        district_parts = []
        for d in criteria.districts:
            code = BERLIN_DISTRICT_CODES.get(d.lower())
            if code:
                district_parts.append(code)

        if district_parts:
            base += "/" + district_parts[0]

        base += "/wohnung-mieten"

        params = {}
        if criteria.min_rooms:
            params["numberofrooms"] = f"{criteria.min_rooms}-"
        if criteria.max_rooms:
            existing = params.get("numberofrooms", "")
            if existing:
                params["numberofrooms"] = f"{existing.rstrip('-')}-{criteria.max_rooms}"
            else:
                params["numberofrooms"] = f"-{criteria.max_rooms}"
        if criteria.max_rent:
            params["price"] = f"-{criteria.max_rent}"
        if criteria.min_size_sqm:
            params["livingspace"] = f"{criteria.min_size_sqm}-"

        if params:
            return base + "?" + urlencode(params)
        return base

    async def search(self, criteria: RentalCriteria) -> list[Listing]:
        """Search IS24 using a visible browser. User solves captcha once, cookies persist."""
        search_url = self._build_search_url(criteria)
        listings = []

        BROWSER_STATE_DIR.mkdir(parents=True, exist_ok=True)

        async with async_playwright() as p:
            # Launch visible browser — IS24 detects headless
            browser = await p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )

            # Use persistent context to save/reuse cookies
            context = await browser.new_context(
                storage_state=str(BROWSER_STATE_DIR / "is24_state.json") if (BROWSER_STATE_DIR / "is24_state.json").exists() else None,
                locale="de-DE",
                viewport={"width": 1400, "height": 900},
            )
            page = await context.new_page()

            try:
                print("Opening ImmobilienScout24...")
                resp = await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)

                # Handle cookie consent
                try:
                    await page.locator("#uc-btn-accept-banner").click(timeout=5000)
                    await page.wait_for_timeout(1000)
                except Exception:
                    pass

                # Check for captcha — if present, wait for user to solve it
                title = await page.title()
                if "Roboter" in title or (resp and resp.status == 401):
                    print()
                    print("╔══════════════════════════════════════════════╗")
                    print("║  CAPTCHA detected on ImmobilienScout24!     ║")
                    print("║  Please solve it in the browser window.     ║")
                    print("║  The agent will continue automatically.     ║")
                    print("╚══════════════════════════════════════════════╝")
                    print()

                    # Wait for the captcha to be solved (page will redirect)
                    try:
                        await page.wait_for_url("**/Suche/**", timeout=120000)  # 2 min to solve
                        await page.wait_for_timeout(2000)
                        print("Captcha solved! Continuing...")
                    except Exception:
                        print("Captcha timeout — could not proceed.")
                        await self._save_state(context)
                        await browser.close()
                        return []

                # Handle cookie consent again (may appear after captcha)
                try:
                    await page.locator("#uc-btn-accept-banner").click(timeout=3000)
                    await page.wait_for_timeout(1000)
                except Exception:
                    pass

                await page.wait_for_timeout(2000)

                # Parse listings — current IS24 layout uses div.listing-card with data-obid
                cards = await page.query_selector_all("div.listing-card[data-obid]")
                if not cards:
                    # Fallback to older selectors
                    cards = await page.query_selector_all("li.result-list__listing, article.result-list-entry, [data-testid='result-list-entry']")

                for card in cards[:25]:
                    try:
                        listing = await self._parse_card(card)
                        if listing:
                            listings.append(listing)
                    except Exception:
                        continue

                # If card parsing failed, try extracting from page source
                if not listings:
                    content = await page.content()
                    listings = self._parse_from_html(content)

                # Save browser state for next time (cookies, localStorage)
                await self._save_state(context)

            except Exception as e:
                print(f"ImmobilienScout24 error: {e}")
            finally:
                await browser.close()

        return listings

    async def _save_state(self, context):
        """Save browser cookies/state for reuse."""
        try:
            state = await context.storage_state()
            state_file = BROWSER_STATE_DIR / "is24_state.json"
            state_file.write_text(json.dumps(state))
        except Exception:
            pass

    async def _parse_card(self, card) -> Listing | None:
        """Parse a result card element (current IS24 layout: div.listing-card[data-obid])."""
        # Get the expose ID from data-obid or find the first expose link
        obid = await card.get_attribute("data-obid")
        if obid:
            href = f"https://www.immobilienscout24.de/expose/{obid}"
        else:
            link_el = await card.query_selector("a[href*='/expose/']")
            if not link_el:
                return None
            href = await link_el.get_attribute("href")
            if not href:
                return None
            if href.startswith("/"):
                href = "https://www.immobilienscout24.de" + href

        # Title — h2 element inside the card
        title_el = await card.query_selector("h2")
        title = (await title_el.inner_text()).strip() if title_el else "Unknown"

        # Extract price, rooms, size from <dd> elements
        dd_els = await card.query_selector_all("dd")
        price_cold = None
        rooms = None
        size = None
        for dd in dd_els:
            text = (await dd.inner_text()).strip()
            m = re.search(r"([\d.,]+)\s*€", text)
            if m:
                price_cold = float(m.group(1).replace(".", "").replace(",", "."))
                continue
            m = re.search(r"([\d.,]+)\s*Zi", text)
            if m:
                rooms = float(m.group(1).replace(",", "."))
                continue
            m = re.search(r"([\d.,]+)\s*m²", text)
            if m:
                size = float(m.group(1).replace(",", "."))

        # Address
        address_el = await card.query_selector("[data-testid='hybridViewAddress']")
        address = (await address_el.inner_text()).strip() if address_el else None

        return Listing(
            title=title,
            url=href,
            source="ImmobilienScout24",
            price_cold=price_cold,
            rooms=rooms,
            size_sqm=size,
            address=address,
        )

    def _parse_from_html(self, html: str) -> list[Listing]:
        """Fallback: extract listings from raw HTML source."""
        listings = []
        # Find expose links with surrounding context
        for m in re.finditer(r'href="(/expose/\d+)"', html):
            expose_url = "https://www.immobilienscout24.de" + m.group(1)
            # Avoid duplicates
            if any(l.url == expose_url for l in listings):
                continue

            # Try to get context around the link
            start = max(0, m.start() - 1000)
            end = min(len(html), m.end() + 1000)
            context = html[start:end]

            title_m = re.search(r'title="([^"]{10,})"', context)
            title = title_m.group(1) if title_m else "ImmobilienScout24 Listing"

            listings.append(Listing(
                title=title,
                url=expose_url,
                source="ImmobilienScout24",
            ))

        return listings[:20]

    async def get_listing_details(self, url: str) -> Listing | None:
        BROWSER_STATE_DIR.mkdir(parents=True, exist_ok=True)

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                storage_state=str(BROWSER_STATE_DIR / "is24_state.json") if (BROWSER_STATE_DIR / "is24_state.json").exists() else None,
                locale="de-DE",
            )
            page = await context.new_page()

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                try:
                    await page.locator("#uc-btn-accept-banner").click(timeout=3000)
                except Exception:
                    pass

                # Wait for captcha if needed
                title = await page.title()
                if "Roboter" in title:
                    print("Solve the captcha in the browser window...")
                    try:
                        await page.wait_for_url("**/expose/**", timeout=120000)
                    except Exception:
                        await browser.close()
                        return None

                await page.wait_for_timeout(2000)

                title_el = await page.query_selector("h1#expose-title")
                title = (await title_el.inner_text()).strip() if title_el else "Unknown"

                price_cold = None
                cold_el = await page.query_selector("dd.is24qa-kaltmiete")
                if cold_el:
                    m = re.search(r"([\d.,]+)", await cold_el.inner_text())
                    if m:
                        price_cold = float(m.group(1).replace(".", "").replace(",", "."))

                price_warm = None
                warm_el = await page.query_selector("dd.is24qa-gesamtmiete, dd.is24qa-warmmiete")
                if warm_el:
                    m = re.search(r"([\d.,]+)", await warm_el.inner_text())
                    if m:
                        price_warm = float(m.group(1).replace(".", "").replace(",", "."))

                rooms = None
                rooms_el = await page.query_selector("dd.is24qa-zimmer")
                if rooms_el:
                    m = re.search(r"([\d.,]+)", await rooms_el.inner_text())
                    if m:
                        rooms = float(m.group(1).replace(",", "."))

                size = None
                size_el = await page.query_selector("dd.is24qa-wohnflaeche-ca")
                if size_el:
                    m = re.search(r"([\d.,]+)", await size_el.inner_text())
                    if m:
                        size = float(m.group(1).replace(",", "."))

                address_el = await page.query_selector(".address-block")
                address = (await address_el.inner_text()).strip() if address_el else None

                desc_el = await page.query_selector(".is24qa-objektbeschreibung")
                description = (await desc_el.inner_text()).strip()[:1500] if desc_el else None

                contact_name = None
                contact_el = await page.query_selector(".contactDetails .title")
                if contact_el:
                    contact_name = (await contact_el.inner_text()).strip()

                await self._save_state(context)

                return Listing(
                    title=title, url=url, source="ImmobilienScout24",
                    price_warm=price_warm, price_cold=price_cold,
                    rooms=rooms, size_sqm=size, address=address,
                    description=description, contact_name=contact_name,
                )

            except Exception as e:
                print(f"Error getting ImmoscoutScraper details: {e}")
                return None
            finally:
                await browser.close()
