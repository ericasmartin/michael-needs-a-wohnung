from .immoscout import ImmoscoutScraper
from .kleinanzeigen import KleinanzeigenScraper
from .immowelt import ImmoweltScraper
from .wggesucht import WGGesuchtScraper
from .inberlinwohnen import InBerlinWohnenScraper

# Immowelt is excluded — its SPA blocks headless browsers (no content renders).
# The scraper code is ready; enable it if using a headed browser approach like IS24.
ALL_SCRAPERS = [ImmoscoutScraper, KleinanzeigenScraper, WGGesuchtScraper, InBerlinWohnenScraper]
