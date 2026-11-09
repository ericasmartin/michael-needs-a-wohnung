from __future__ import annotations
import re
from urllib.parse import urlencode

from playwright.async_api import async_playwright

from agent.criteria import RentalCriteria
from .base import BaseScraper, Listing


class ImmoweltScraper(BaseScraper):
    name = "Immowelt"

    def _build_search_url(self, criteria: RentalCriteria) -> str:
        # Immowelt redirects /liste/ to /suche/ — use /suche/ directly
        base = "https://www.immowelt.de/suche/berlin/wohnungen/mieten"

        params = {"sort": "relevanz"}
        if criteria.min_rooms:
            params["roomi"] = str(int(criteria.min_rooms))
        if criteria.max_rooms:
            params["rooma"] = str(int(criteria.max_rooms))
        if criteria.max_rent:
            params["prima"] = str(int(criteria.max_rent))
        if criteria.min_rent:
            params["prisi"] = str(int(criteria.min_rent))
        if criteria.min_size_sqm:
            params["wflmi"] = str(int(criteria.min_size_sqm))
        if criteria.max_size_sqm:
            params["wflma"] = str(int(criteria.max_size_sqm))

        return base + "?" + urlencode(params)

    async def search(self, criteria: RentalCriteria) -> list[Listing]:
        url = self._build_search_url(criteria)
        listings = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale="de-DE",
                viewport={"width": 1400, "height": 900},
            )
            page = await context.new_page()

            try:
                await page.goto(url, wait_until="networkidle", timeout=45000)

                # Handle cookie consent
                try:
                    consent_btn = page.locator("button[data-testid='uc-accept-all-button'], #uc-btn-accept-banner")
                    await consent_btn.click(timeout=5000)
                    await page.wait_for_timeout(1000)
                except Exception:
                    pass

                # Immowelt is an SPA — wait for listing cards to render
                try:
                    await page.wait_for_selector("a[href*='/expose/'], [class*='EstateItem'], [data-testid*='card'], [class*='css-']", timeout=20000)
                except Exception:
                    pass
                await page.wait_for_timeout(3000)

                # Immowelt listing cards — try multiple selectors
                cards = await page.query_selector_all("div[class*='EstateItem'], [data-testid*='classified-card'], [data-testid*='listitem']")
                if not cards:
                    # Fallback: find expose link containers
                    cards = await page.query_selector_all("a[href*='/expose/']")

                for card in cards[:25]:
                    try:
                        listing = await self._parse_card(card, page)
                        if listing:
                            listings.append(listing)
                    except Exception:
                        continue

                # Fallback: parse from HTML
                if not listings:
                    content = await page.content()
                    listings = self._parse_from_html(content)

            except Exception as e:
                print(f"Immowelt scraping error: {e}")
            finally:
                await browser.close()

        return listings

    async def _parse_card(self, card, page) -> Listing | None:
        # Try to get link
        href = await card.get_attribute("href")
        if not href:
            link_el = await card.query_selector("a[href*='/expose/']")
            if not link_el:
                return None
            href = await link_el.get_attribute("href")
        if not href:
            return None
        if href.startswith("/"):
            href = "https://www.immowelt.de" + href

        if "/expose/" not in href:
            return None

        card_text = (await card.inner_text()).strip()

        # Title — first substantial line of text
        title = "Immowelt Listing"
        title_el = await card.query_selector("h2, [data-testid*='title'], .FactsMain-title")
        if title_el:
            title = (await title_el.inner_text()).strip()
        elif card_text:
            first_line = card_text.split("\n")[0].strip()
            if len(first_line) > 5:
                title = first_line

        # Price
        price = None
        m = re.search(r"([\d.,]+)\s*€", card_text)
        if m:
            price = float(m.group(1).replace(".", "").replace(",", "."))

        # Rooms
        rooms = None
        m = re.search(r"([\d.,]+)\s*Zi", card_text)
        if m:
            rooms = float(m.group(1).replace(",", "."))

        # Size
        size = None
        m = re.search(r"([\d.,]+)\s*m²", card_text)
        if m:
            size = float(m.group(1).replace(",", "."))

        # Address
        address = None
        addr_el = await card.query_selector("[data-testid*='address'], .EstateAddress")
        if addr_el:
            address = (await addr_el.inner_text()).strip()
        else:
            # Look for Berlin postal code pattern in text
            m = re.search(r"(\d{5}\s+Berlin[^\n]*)", card_text)
            if m:
                address = m.group(1).strip()

        return Listing(
            title=title,
            url=href,
            source="Immowelt",
            price_warm=price,
            rooms=rooms,
            size_sqm=size,
            address=address,
        )

    def _parse_from_html(self, html: str) -> list[Listing]:
        """Fallback: extract listings from raw HTML."""
        listings = []
        for m in re.finditer(r'href="(/expose/[^"]+)"', html):
            expose_url = "https://www.immowelt.de" + m.group(1)
            if any(l.url == expose_url for l in listings):
                continue

            start = max(0, m.start() - 1000)
            end = min(len(html), m.end() + 1000)
            context = html[start:end]

            title_m = re.search(r'title="([^"]{10,})"', context)
            title = title_m.group(1) if title_m else "Immowelt Listing"

            listings.append(Listing(
                title=title,
                url=expose_url,
                source="Immowelt",
            ))

        return listings[:20]

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
                    consent_btn = page.locator("button[data-testid='uc-accept-all-button']")
                    await consent_btn.click(timeout=3000)
                except Exception:
                    pass

                await page.wait_for_timeout(2000)

                title_el = await page.query_selector("h1")
                title = (await title_el.inner_text()).strip() if title_el else "Unknown"

                page_text = (await page.inner_text("body")).strip()

                price = None
                m = re.search(r"([\d.,]+)\s*€\s*(?:Warmmiete|warm|Gesamtmiete)", page_text)
                if not m:
                    m = re.search(r"([\d.,]+)\s*€", page_text)
                if m:
                    price = float(m.group(1).replace(".", "").replace(",", "."))

                rooms = None
                m = re.search(r"([\d.,]+)\s*Zimmer", page_text)
                if m:
                    rooms = float(m.group(1).replace(",", "."))

                size = None
                m = re.search(r"([\d.,]+)\s*m²", page_text)
                if m:
                    size = float(m.group(1).replace(",", "."))

                address = None
                addr_el = await page.query_selector("[data-testid*='address'], .location")
                if addr_el:
                    address = (await addr_el.inner_text()).strip()

                description = None
                desc_el = await page.query_selector("[data-testid*='description'], .objektbeschreibung")
                if desc_el:
                    description = (await desc_el.inner_text()).strip()[:1500]

                return Listing(
                    title=title, url=url, source="Immowelt",
                    price_warm=price, rooms=rooms, size_sqm=size,
                    address=address, description=description,
                )

            except Exception as e:
                print(f"Error getting Immowelt details: {e}")
                return None
            finally:
                await browser.close()
