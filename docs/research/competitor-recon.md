# Competitor recon (2026-07-07)

A survey of the date-night picker category, done at project kickoff.

## Is there a gap?

No app found, open source or commercial, combines self-hosting with a pick that stays a surprise. The closest open-source project is [couplecards](https://github.com/qiaeru/couplecards) (Fastify and SQLite, card draw), but it reveals the card in full and has no booking metadata. The redacted-reveal concept otherwise exists only as a human concierge service. `limited_until` and availability metadata also appear to be new: commercial apps treat ideas as evergreen text.

## Ideas adopted in v1

- Ban list and recency damping on the wheel (couplecards)
- Weighted odds for limited-time ideas that expire soon (recency bias as in aws-ops-wheel)
- Quick-add by URL, enrich later (wishlist pattern from Lovewick)
- Post-date rating and a times-done counter (Notion date board templates)
- Booked-date state after pinning (Cobble)
- Timed auto-reveal before the booked date (the 24 to 48 hour pattern used by mystery-date services)
- JSON import and export, plus a seed pack (couplecards)

## Ideas deferred to v2

See [v2 direction](../decisions/v2-direction.md).

- Expiry ping via ntfy (reminder pattern from Lovewick)
- Rigged spin, planner-only (aws-ops-wheel)
- Swipe-to-match mutual veto pool (Cobble): parked, harder
- Milestone countdown (Lovewick): rejected, low value here

## What to avoid

Patterns that user reviews of commercial couples apps complain about most:

- Accounts, subscriptions and hard-to-cancel renewals.
- Paywalling content the users created themselves, or auto-saving content they cannot delete.
- Daily-engagement and streak mechanics, a common reason people quit.
- Depending on curated content that someone has to keep producing.
- Depending on a cloud service: several apps in this category shut down and took their users' data with them.
