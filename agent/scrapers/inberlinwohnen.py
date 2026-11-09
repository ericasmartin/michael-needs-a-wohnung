from __future__ import annotations
import re

from playwright.async_api import async_playwright

from agent.criteria import RentalCriteria
from .base import BaseScraper, Listing


class InBerlinWohnenScraper(BaseScraper):
    name = "InBerlinWohnen"

    def _build_search_url(self, criteria: RentalCriteria) -> str:
        # inberlinwohnen.de aggregates listings from Berlin's public housing companies
        # (degewo, Gewobag, WBM, Howoge, Stadt und Land, GESOBAU)
        # The search page has filters applied via form params
        base = "https://inberlinwohnen.de/wohnungsfinder/"

        # The site uses a form-based filter, but the base URL shows all available listings
        # We'll filter in post-processing based on criteria
        return base

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
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)

                # Handle cookie consent
                try:
                    consent_btn = page.locator("button.accept-all, #cookie-accept, .cc-btn.cc-allow")
                    await consent_btn.click(timeout=5000)
                    await page.wait_for_timeout(1000)
                except Exception:
                    pass

                await page.wait_for_timeout(3000)

                # Try to apply filters if the site supports it
                # Set room filter
                if criteria.min_rooms:
                    try:
                        rooms_select = page.locator("select[name*='room'], select[name*='zimmer'], #zimmer_von")
                        await rooms_select.select_option(str(int(criteria.min_rooms)), timeout=3000)
                    except Exception:
                        pass

                # Set price filter
                if criteria.max_rent:
                    try:
                        price_input = page.locator("input[name*='miete_bis'], input[name*='price_max'], #miete_bis")
                        await price_input.fill(str(int(criteria.max_rent)), timeout=3000)
                    except Exception:
                        pass

                # Try clicking search/filter button
                try:
                    search_btn = page.locator("button[type='submit'], input[type='submit'], .btn-search, #submit-search")
                    await search_btn.click(timeout=3000)
                    await page.wait_for_timeout(3000)
                except Exception:
                    pass

                # Parse listing cards — the site uses various card layouts
                cards = await page.query_selector_all(".ang_wohnung_cardbox, .wohnung-card, .listing-item, article.property")
                if not cards:
                    # Try broader selectors
                    cards = await page.query_selector_all(".row.ang_wohnung, [class*='wohnung'], [class*='listing']")

                for card in cards[:30]:
                    try:
                        listing = await self._parse_card(card)
                        if listing:
                            # Post-filter by criteria
                            if self._matches_basic_criteria(listing, criteria):
                                listings.append(listing)
                    except Exception:
                        continue

                # Fallback: parse from HTML
                if not listings:
                    content = await page.content()
                    raw_listings = self._parse_from_html(content)
                    for listing in raw_listings:
                        if self._matches_basic_criteria(listing, criteria):
                            listings.append(listing)

            except Exception as e:
                print(f"InBerlinWohnen scraping error: {e}")
            finally:
                await browser.close()

        return listings

    def _matches_basic_criteria(self, listing: Listing, criteria: RentalCriteria) -> bool:
        """Basic filter since the site may not support all URL params."""
        price = listing.price_warm or listing.price_cold
        if criteria.max_rent and price and price > criteria.max_rent:
            return False
        if criteria.min_rooms and listing.rooms and listing.rooms < criteria.min_rooms:
            return False
        if criteria.max_rooms and listing.rooms and listing.rooms > criteria.max_rooms:
            return False
        return True

    async def _parse_card(self, card) -> Listing | None:
        # Find link
        link_el = await card.query_selector("a[href]")
        if not link_el:
            return None

        href = await link_el.get_attribute("href")
        if not href:
            return None
        if href.startswith("/"):
            href = "https://inberlinwohnen.de" + href

        card_text = (await card.inner_text()).strip()
        if len(card_text) < 10:
            return None

        # Title
        title = "InBerlinWohnen Listing"
        title_el = await card.query_selector("h2, h3, .title, .headline")
        if title_el:
            t = (await title_el.inner_text()).strip()
            if len(t) > 3:
                title = t

        # Price — look for "X,XX €" or "X €"
        price = None
        m = re.search(r"([\d.,]+)\s*€", card_text)
        if m:
            price = float(m.group(1).replace(".", "").replace(",", "."))

        # Rooms
        rooms = None
        m = re.search(r"([\d.,]+)\s*Zi", card_text)
        if not m:
            m = re.search(r"([\d.,]+)\s*Raum|Räume", card_text)
        if m:
            rooms = float(m.group(1).replace(",", "."))

        # Size
        size = None
        m = re.search(r"([\d.,]+)\s*m²", card_text)
        if m:
            size = float(m.group(1).replace(",", "."))

        # Address
        address = None
        addr_el = await card.query_selector(".address, .adresse, [class*='address'], [class*='strasse']")
        if addr_el:
            address = (await addr_el.inner_text()).strip()
        else:
            m = re.search(r"(\d{5}\s+Berlin[^\n]*|[A-ZÄÖÜ][\w.-]+(?:straße|str\.|weg|allee|platz|damm)\s*\d*[^\n]*)", card_text)
            if m:
                address = m.group(1).strip()

        # Housing company — often shown on the card
        company = None
        company_el = await card.query_selector(".company, .anbieter, [class*='company'], img[alt]")
        if company_el:
            tag = await company_el.evaluate("el => el.tagName")
            if tag == "IMG":
                company = await company_el.get_attribute("alt")
            else:
                company = (await company_el.inner_text()).strip()

        if company:
            title = f"{title} ({company})"

        return Listing(
            title=title,
            url=href,
            source="InBerlinWohnen",
            price_warm=price,
            rooms=rooms,
            size_sqm=size,
            address=address,
        )

    def _parse_from_html(self, html: str) -> list[Listing]:
        """Fallback: extract listings from raw HTML."""
        listings = []

        # Look for links to listing detail pages
        for m in re.finditer(r'href="(https?://[^"]*(?:wohnung|expose|angebot)[^"]*)"', html):
            listing_url = m.group(1)
            if any(l.url == listing_url for l in listings):
                continue

            start = max(0, m.start() - 500)
            end = min(len(html), m.end() + 500)
            context = html[start:end]

            title_m = re.search(r'title="([^"]{5,})"', context)
            title = title_m.group(1) if title_m else "InBerlinWohnen Listing"

            listings.append(Listing(
                title=title,
                url=listing_url,
                source="InBerlinWohnen",
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
                    consent_btn = page.locator("button.accept-all, #cookie-accept")
                    await consent_btn.click(timeout=3000)
                except Exception:
                    pass

                await page.wait_for_timeout(2000)

                title_el = await page.query_selector("h1, h2.title")
                title = (await title_el.inner_text()).strip() if title_el else "Unknown"

                page_text = (await page.inner_text("body")).strip()

                price = None
                m = re.search(r"(?:Gesamtmiete|Warmmiete|Miete)[:\s]*([\d.,]+)\s*€", page_text)
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
                addr_el = await page.query_selector(".address, .adresse, [class*='address']")
                if addr_el:
                    address = (await addr_el.inner_text()).strip()

                description = None
                desc_el = await page.query_selector(".description, .beschreibung, [class*='description']")
                if desc_el:
                    description = (await desc_el.inner_text()).strip()[:1500]

                return Listing(
                    title=title, url=url, source="InBerlinWohnen",
                    price_warm=price, rooms=rooms, size_sqm=size,
                    address=address, description=description,
                )

            except Exception as e:
                print(f"Error getting InBerlinWohnen details: {e}")
                return None
            finally:
                await browser.close()
