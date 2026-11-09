from __future__ import annotations
import re
from urllib.parse import urlencode

from playwright.async_api import async_playwright

from agent.criteria import RentalCriteria
from .base import BaseScraper, Listing

# WG-Gesucht category codes
# 0 = WG-Zimmer, 1 = 1-Zimmer-Wohnung, 2 = Wohnung, 3 = Haus
# We want category 2 (full apartments)
CATEGORY_WOHNUNG = "2"

# Berlin city ID on WG-Gesucht
BERLIN_CITY_ID = "8"


class WGGesuchtScraper(BaseScraper):
    name = "WG-Gesucht"

    def _build_search_url(self, criteria: RentalCriteria) -> str:
        # WG-Gesucht URL: /wohnungen-in-Berlin.8.3.1.0.html (city.cityid.category.1.0)
        # Filter params appended as query string
        base = f"https://www.wg-gesucht.de/wohnungen-in-Berlin.{BERLIN_CITY_ID}.{CATEGORY_WOHNUNG}.1.0.html"

        params = {}
        if criteria.max_rent:
            params["rMax"] = str(int(criteria.max_rent))
        if criteria.min_rent:
            params["rMin"] = str(int(criteria.min_rent))
        if criteria.min_rooms:
            params["rmMin"] = str(int(criteria.min_rooms))
        if criteria.max_rooms:
            params["rmMax"] = str(int(criteria.max_rooms))
        if criteria.min_size_sqm:
            params["sMin"] = str(int(criteria.min_size_sqm))
        if criteria.max_size_sqm:
            params["sMax"] = str(int(criteria.max_size_sqm))

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
                viewport={"width": 1400, "height": 900},
            )
            page = await context.new_page()

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)

                # Handle cookie consent
                try:
                    consent_btn = page.locator("#cmpbntyestxt, button[data-testid='cmp-accept']")
                    await consent_btn.click(timeout=5000)
                    await page.wait_for_timeout(1000)
                except Exception:
                    pass

                await page.wait_for_timeout(2000)

                # WG-Gesucht uses .wgg_card or .offer_list_item for listing cards
                cards = await page.query_selector_all(".wgg_card.offer_list_item, .offer_list_item, [id^='liste-details-ad-']")
                if not cards:
                    # Fallback
                    cards = await page.query_selector_all("tr.offer_list_item, div.panel.panel-default")

                for card in cards[:25]:
                    try:
                        listing = await self._parse_card(card)
                        if listing:
                            listings.append(listing)
                    except Exception:
                        continue

                # Fallback: parse from HTML
                if not listings:
                    content = await page.content()
                    listings = self._parse_from_html(content)

            except Exception as e:
                print(f"WG-Gesucht scraping error: {e}")
            finally:
                await browser.close()

        return listings

    async def _parse_card(self, card) -> Listing | None:
        card_text = (await card.inner_text()).strip()
        if len(card_text) < 20:
            return None

        # The card element itself is an <a> tag with href and aria-label
        href = await card.get_attribute("href")
        if not href:
            # Fallback: look for child links
            link_els = await card.query_selector_all("a[href*='.html']")
            for link_el in link_els:
                h = await link_el.get_attribute("href")
                if h and re.search(r"\.\d{5,}\.html", h):
                    href = h
                    break

        if not href:
            return None
        if href.startswith("/"):
            href = "https://www.wg-gesucht.de" + href

        # Skip navigation/category links
        if re.match(r".*\.\d\.\d\.\d\.html", href):
            return None

        lines = [l.strip() for l in card_text.split("\n") if l.strip()]

        # Title — prefer aria-label, then first substantial text line
        title = await card.get_attribute("aria-label") or ""
        if not title or len(title) < 5:
            skip = {"chevron_right", "favorite", "Privat", "Gewerblich", "update"}
            for line in lines:
                if line in skip:
                    continue
                if "€" in line or "m²" in line:
                    continue
                if re.match(r"^\d+-Zimmer", line):
                    continue
                if re.match(r"^\d{2}\.\d{2}\.\d{4}", line):
                    continue
                if len(line) > 10 and not line.startswith("Online"):
                    title = line
                    break
        if not title:
            title = "WG-Gesucht Listing"

        # Price — "X € pro Monat"
        price = None
        m = re.search(r"([\d.,]+)\s*€", card_text)
        if m:
            price = float(m.group(1).replace(".", "").replace(",", "."))

        # Rooms — "3-Zimmer-Wohnung"
        rooms = None
        m = re.search(r"(\d+)-Zimmer", card_text)
        if m:
            rooms = float(m.group(1))

        # Size — "89 m²"
        size = None
        m = re.search(r"([\d.,]+)\s*m²", card_text)
        if m:
            size = float(m.group(1).replace(",", "."))

        # Address — "X-Zimmer-Wohnung · Berlin District · Street"
        address = None
        m = re.search(r"Berlin\s+(\S+)", card_text)
        if m:
            address = f"Berlin {m.group(1)}"
        # Also look for the full "· Berlin District · Street" pattern
        m = re.search(r"·\s*(Berlin[^·\n]+?)(?:\s*·\s*(.+?))?$", card_text, re.MULTILINE)
        if m:
            address = m.group(1).strip()
            street = m.group(2)
            if street:
                address = f"{street.strip()}, {address}"

        # Available from date
        available = None
        m = re.search(r"(\d{2}\.\d{2}\.\d{4})", card_text)
        if m:
            available = m.group(1)

        return Listing(
            title=title,
            url=href,
            source="WG-Gesucht",
            price_warm=price,
            rooms=rooms,
            size_sqm=size,
            address=address,
            available_from=available,
        )

    def _parse_from_html(self, html: str) -> list[Listing]:
        """Fallback: extract listings from raw HTML."""
        listings = []
        # WG-Gesucht listing URLs: /wohnungen-in-Berlin-District.ADID.html (ADID is 5+ digits)
        for m in re.finditer(r'href="(/wohnungen-in-[^"]+\.(\d{5,})\.html)"', html):
            listing_url = "https://www.wg-gesucht.de" + m.group(1)
            if any(l.url == listing_url for l in listings):
                continue

            start = max(0, m.start() - 500)
            end = min(len(html), m.end() + 500)
            context = html[start:end]

            title_m = re.search(r'title="([^"]{5,})"', context)
            title = title_m.group(1) if title_m else "WG-Gesucht Listing"

            listings.append(Listing(
                title=title,
                url=listing_url,
                source="WG-Gesucht",
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
                    consent_btn = page.locator("#cmpbntyestxt")
                    await consent_btn.click(timeout=3000)
                except Exception:
                    pass

                await page.wait_for_timeout(2000)

                title_el = await page.query_selector("h1, .headline")
                title = (await title_el.inner_text()).strip() if title_el else "Unknown"

                page_text = (await page.inner_text("body")).strip()

                price = None
                # WG-Gesucht shows "Gesamtmiete: X €" or "Miete: X €"
                m = re.search(r"(?:Gesamtmiete|Miete)[:\s]*([\d.,]+)\s*€", page_text)
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
                addr_el = await page.query_selector(".col-sm-4.mb10 a, .map_address")
                if addr_el:
                    address = (await addr_el.inner_text()).strip()

                description = None
                desc_el = await page.query_selector("#ad_description_text, .freitext")
                if desc_el:
                    description = (await desc_el.inner_text()).strip()[:1500]

                return Listing(
                    title=title, url=url, source="WG-Gesucht",
                    price_warm=price, rooms=rooms, size_sqm=size,
                    address=address, description=description,
                )

            except Exception as e:
                print(f"Error getting WG-Gesucht details: {e}")
                return None
            finally:
                await browser.close()
