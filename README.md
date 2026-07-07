# 🎡 Saturday Datenight

A tiny self-hosted web app for couples who can never decide on date night. One of you (the planner) collects date ideas with all the practical details. When the next date is coming up, you spin a wheel together. The wheel picks an idea, but keeps it secret: the other person only sees what they need to get ready.

> "Wear something fancy, it takes about 3 hours, dinner is included, 45 minutes of travel." That's all you get. The rest is a surprise. 💘

## Why

Every couples app out there is a cloud subscription, and none of them keep the pick a secret. This one is a single container on your own network, has no accounts, and treats the surprise as the core feature.

## Features

- **Date ideas with real metadata**: link to the website, location, price, travel time, expected duration, dress code (casual / smart casual / fancy / sporty / swimwear / weatherproof), whether dinner is covered, availability notes, and a "limited time only" expiry date.
- **The wheel**: spins over all eligible ideas. Every slice is a mystery. Limited-time ideas that are about to expire get better odds; recently picked ideas get worse odds.
- **Redacted result**: after the spin, the public page shows only dress code, duration, travel time, dinner info and category. The title, location and link stay hidden.
- **Pin and book**: the planner pins the pick, checks availability, makes the reservation and sets the booked date.
- **Timed reveal**: the full details reveal automatically shortly before the booked date (configurable, default 24h), or manually whenever the planner decides.
- **Rate and repeat**: after the date, mark it done with a 1-5 rating. Ban ideas from the wheel without deleting them.
- **Import/export**: back up your ideas as JSON. A starter pack of ~24 Netherlands date ideas ships in `seeds/nl-date-ideas.json`.
- **No accounts**: the public view is for both of you; planner mode is behind a single passcode. Built for a trusted home network.

## Quick start

```bash
cp .env.example .env   # edit passcode + secret
docker compose up -d
# open http://localhost:5000
```

Or without Docker:

```bash
pip install -r requirements.txt
PLANNER_PASSCODE=yourcode SECRET_KEY=$(openssl rand -hex 32) python app.py
```

Then log into planner mode and import `seeds/nl-date-ideas.json` to get started.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `PLANNER_PASSCODE` | `changeme` | Passcode for planner mode. Change it. |
| `SECRET_KEY` | dev value | Flask session secret. Set to a random string. |
| `AUTO_REVEAL_HOURS` | `24` | Hours before the booked date at which the pick auto-reveals. `0` reveals on the day itself. |
| `DATENIGHT_DB` | `./data/datenight.db` | SQLite database path. |

## Security note

This app is intentionally low-security: one passcode, no rate limiting, no HTTPS. Run it on a trusted LAN or behind a reverse proxy with its own auth. The threat model is "my partner might peek", not the internet.

## License

MIT
