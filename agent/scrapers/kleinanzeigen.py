from __future__ import annotations
import re
from urllib.parse import urlencode, quote

from playwright.async_api import async_playwright

from agent.criteria import RentalCriteria
from .base import BaseScraper, Listing

# Kleinanzeigen location codes — must be appended to category code (e.g. c203l3331)
BERLIN_LOCATION_CODE = "l3331"
# District-level codes (TODO: verify these if district filtering needed)
# For now we use city-level l3331 which covers all of Berlin


class KleinanzeigenScraper(BaseScraper):
    name = "Kleinanzeigen"

    def _build_search_url(self, criteria: RentalCriteria) -> str:
        # Kleinanzeigen requires location code appended to category: c203l3331
        base = f"https://www.kleinanzeigen.de/s-wohnung-mieten/berlin/c203{BERLIN_LOCATION_CODE}"

        params = {}
        if criteria.max_rent:
            params["maxPrice"] = str(int(criteria.max_rent))
        if criteria.min_rent:
            params["minPrice"] = str(int(criteria.min_rent))
        if criteria.min_rooms:
            params["minRooms"] = str(int(criteria.min_rooms))
        if criteria.max_rooms:
            params["maxRooms"] = str(int(criteria.max_rooms))
        if criteria.min_size_sqm:
            params["minSize"] = str(int(criteria.min_size_sqm))
        if criteria.max_size_sqm:
            params["maxSize"] = str(int(criteria.max_size_sqm))

        if params:
            return base + "?" + urlencode(params)
        return base

    async def search(self, criteria: RentalCriteria) -> list[Listing]:
        url = self._build_search_url(criteria)
        listings = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale="de-DE",
            )
            page = await context.new_page()

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)

                # Handle consent
                try:
                    consent_btn = page.locator("#gdpr-banner-accept, button[data-testid='gdpr-banner-accept']")
                    await consent_btn.click(timeout=5000)
                except Exception:
                    pass

                await page.wait_for_timeout(2000)

                # Parse listing cards — new Kleinanzeigen layout uses
                # <li data-clickable="card"> with <article data-adid="..." data-href="...">
                cards = await page.query_selector_all('article[data-adid]')
                if not cards:
                    # Fallback to older selectors
                    cards = await page.query_selector_all("article.aditem, li.ad-listitem article")

                for card in cards[:30]:  # parse extra to compensate for filtering
                    try:
                        listing = await self._parse_card(card)
                        if listing:
                            listings.append(listing)
                    except Exception:
                        continue

            except Exception as e:
                print(f"Kleinanzeigen scraping error: {e}")
            finally:
                await browser.close()

        # Post-filter: only keep Berlin listings
        # Kleinanzeigen often leaks nationwide results even with Berlin URL
        # Berlin postal codes: 10xxx, 12xxx, 13xxx, 14xxx
        berlin_plz_prefixes = ("10", "12", "13", "14")
        filtered = []
        for listing in listings:
            addr = listing.address or ""
            # Check postal code in address (e.g. "10997 Berlin - Kreuzberg")
            plz_match = re.match(r"(\d{5})", addr)
            if plz_match:
                if plz_match.group(1)[:2] in berlin_plz_prefixes:
                    filtered.append(listing)
                    continue
            # Fallback: check for "berlin" in address or title
            if "berlin" in addr.lower() or "berlin" in listing.title.lower():
                filtered.append(listing)

        return filtered

    async def _parse_card(self, card) -> Listing | None:
        # New layout: <article data-adid="..." data-href="/s-anzeige/...">
        href = await card.get_attribute("data-href")
        if not href:
            # Fallback: find a link
            link_el = await card.query_selector("a[href*='/s-anzeige/']")
            if not link_el:
                return None
            href = await link_el.get_attribute("href")
        if not href:
            return None
        if href.startswith("/"):
            href = "https://www.kleinanzeigen.de" + href

        # Title — find the first link with substantial text inside the card
        title = ""
        link_els = await card.query_selector_all("a[href*='/s-anzeige/']")
        for link_el in link_els:
            text = (await link_el.inner_text()).strip()
            if len(text) > 5:
                title = text
                break
        if not title:
            title = "Unknown"

        # Get all text from the card for parsing
        card_text = (await card.inner_text()).strip()

        # Price — look for EUR pattern
        price = None
        m = re.search(r"([\d.,]+)\s*€", card_text)
        if m:
            price = float(m.group(1).replace(".", "").replace(",", "."))

        # Rooms — "X Zi." format
        rooms = None
        m = re.search(r"([\d.,]+)\s*Zi", card_text)
        if m:
            rooms = float(m.group(1).replace(",", "."))

        # Size
        size = None
        m = re.search(r"([\d.,]+)\s*m²", card_text)
        if m:
            size = float(m.group(1).replace(",", "."))

        # Location — postal code + city at the top of the card text
        # Format: "12345 City Name" or "12345 Berlin - District"
        address = None
        m = re.search(r"(\d{5}\s+[^\n]+)", card_text)
        if m:
            address = m.group(1).strip()

        return Listing(
            title=title,
            url=href,
            source="Kleinanzeigen",
            price_warm=price,
            rooms=rooms,
            size_sqm=size,
            address=address,
        )

    async def get_listing_details(self, url: str) -> Listing | None:
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
                    consent_btn = page.locator("#gdpr-banner-accept")
                    await consent_btn.click(timeout=3000)
                except Exception:
                    pass

                await page.wait_for_timeout(2000)

                title_el = await page.query_selector("h1#viewad-title")
                title = (await title_el.inner_text()).strip() if title_el else "Unknown"

                price = None
                price_el = await page.query_selector("h2#viewad-price")
                if price_el:
                    m = re.search(r"([\d.,]+)", await price_el.inner_text())
                    if m:
                        price = float(m.group(1).replace(".", "").replace(",", "."))

                description = None
                desc_el = await page.query_selector("#viewad-description-text, p[itemprop='description']")
                if desc_el:
                    description = (await desc_el.inner_text()).strip()[:1500]

                address = None
                addr_el = await page.query_selector("#viewad-locality")
                if addr_el:
                    address = (await addr_el.inner_text()).strip()

                # Extract details (rooms, size) from attributes
                rooms = None
                size = None
                detail_els = await page.query_selector_all("li.addetailslist--detail")
                for del_el in detail_els:
                    text = (await del_el.inner_text()).strip()
                    if "Zimmer" in text:
                        m = re.search(r"([\d.,]+)", text)
                        if m:
                            rooms = float(m.group(1).replace(",", "."))
                    elif "m²" in text or "Wohnfläche" in text:
                        m = re.search(r"([\d.,]+)", text)
                        if m:
                            size = float(m.group(1).replace(",", "."))

                # Contact name
                contact_name = None
                contact_el = await page.query_selector(".userprofile-vip a, span.userprofile-vip")
                if contact_el:
                    contact_name = (await contact_el.inner_text()).strip()

                return Listing(
                    title=title,
                    url=url,
                    source="Kleinanzeigen",
                    price_warm=price,
                    rooms=rooms,
                    size_sqm=size,
                    address=address,
                    description=description,
                    contact_name=contact_name,
                )

            except Exception as e:
                print(f"Error getting Kleinanzeigen details: {e}")
                return None
            finally:
                await browser.close()
