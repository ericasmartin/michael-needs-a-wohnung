# michael-needs-a-wohnung

An AI-powered apartment hunting agent for the Berlin rental market. It scrapes ImmobilienScout24, Kleinanzeigen, Immowelt, WG-Gesucht, and InBerlinWohnen (Berlin public housing), analyzes listings against your criteria, and reaches out to landlords with personalized German-language inquiries on your behalf.

## What It Does

1. **Gathers your criteria** — budget, rooms, size, preferred districts, and preferences (Neubau, quiet street, etc.)
2. **Scrapes listings** from 5 sources using Playwright: ImmobilienScout24, Kleinanzeigen, Immowelt, WG-Gesucht, and InBerlinWohnen
3. **AI-analyzes each listing** against your criteria and scores them (0–100%)
4. **Presents top picks** with links, prices, and match reasons
5. **Contacts landlords** for matched listings (score ≥ 70%) with personalized German emails that sound like you
6. **Monitors for new listings** in the background and notifies you of matches
7. **Tracks everything** — match status, outreach status, replies — persisted in SQLite across sessions

## Quick Start

```bash
# Clone and set up
git clone https://github.com/ericasmartin/michael-needs-a-wohnung.git
cd michael-needs-a-wohnung
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# Configure
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY

# Run
python main.py
```

## Browse-Only Mode

Don't want to set up email yet? The agent starts in **browse-only mode** by default — it'll search, analyze, and present listings without sending anything. Type `setup email` when you're ready to enable outreach.

## How It Works

### Scraping

- **ImmobilienScout24** — uses a visible Chromium browser (IS24 blocks headless browsers). If a captcha appears, you solve it once in the browser window; cookies are saved for future runs.
- **Kleinanzeigen** — headless Chromium with Berlin postal code filtering (10xxx, 12xxx, 13xxx, 14xxx).
- **Immowelt** — scraper code is ready but currently disabled; its SPA blocks headless browsers. Can be enabled with a headed browser approach like IS24.
- **WG-Gesucht** — searches the full apartment section (not shared flats).
- **InBerlinWohnen** — aggregator for Berlin's public housing companies (degewo, Gewobag, WBM, Howoge, etc.). Often below-market rents.

### Matching

Each listing is scored against your criteria:
- Budget compliance (most important)
- Room count and size
- District/location preference
- Special preferences (Neubau, quiet street, etc.)

With an API key, Claude does the scoring and catches nuance. Without one, a rule-based fallback handles the basics.

### Outreach

For matched listings, Claude generates a personalized inquiry in German (Bewerbung-style) that:
- Sounds like it's from you (uses your name and personal intro)
- References specific details from the listing
- Asks about availability and a Besichtigungstermin (viewing appointment)
- Reads natural, not like a template

Messages are sent via email (SMTP) or by filling out the site's contact form via Playwright.

### Background Monitoring

Once criteria are set, the agent can run in the background — checking for new listings every 30 minutes, scoring them, notifying you of matches, and optionally auto-contacting landlords.

## Project Structure

```
michael-needs-a-wohnung/
├── main.py                  # CLI entry point
├── agent/
│   ├── core.py              # Main agent — Claude tool-use loop
│   ├── criteria.py          # RentalCriteria dataclass
│   ├── db.py                # SQLite persistence
│   ├── email_client.py      # IMAP/SMTP email
│   ├── notifier.py          # Email + CLI notifications
│   ├── tracker.py           # Listing tracking & status table
│   └── scrapers/
│       ├── base.py            # Listing dataclass & BaseScraper ABC
│       ├── immoscout.py       # ImmobilienScout24 scraper
│       ├── kleinanzeigen.py   # Kleinanzeigen scraper
│       ├── immowelt.py        # Immowelt scraper
│       ├── wggesucht.py       # WG-Gesucht scraper
│       └── inberlinwohnen.py  # InBerlinWohnen (public housing) scraper
├── requirements.txt
└── .env.example
```

## Configuration

All configuration is in `.env`:

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Required for AI features (scoring, outreach, conversation) |
| `EMAIL_ADDRESS` | Your email (for sending inquiries + receiving notifications) |
| `EMAIL_PASSWORD` | App password (not your regular password) |
| `IMAP_SERVER` | e.g. `imap.gmail.com` |
| `SMTP_SERVER` | e.g. `smtp.gmail.com` |
| `SMTP_PORT` | Usually `587` |

Email can also be configured interactively by typing `setup email` in the CLI.

## Requirements

- Python 3.9+
- Chromium (installed via `playwright install chromium`)
- Anthropic API key (optional for browse-only mode)

## License

MIT
