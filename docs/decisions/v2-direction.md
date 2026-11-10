# v2 direction

Decided 2026-07-07, picking from the deferred list in the [competitor recon](../research/competitor-recon.md).

| Idea | Verdict | Status |
|---|---|---|
| Expiry ping | Yes | Shipped in PR #1 |
| Rigged spin | Yes | Shipped in PR #1 |
| Swipe-to-match | Parked | Revisit after v2 |
| Milestone countdown | Rejected | A booked date already implies it |

## Expiry ping

An ntfy notification when a limited-time idea approaches `limited_until`. It is checked on request instead of by a scheduler, and fires once per idea per expiry date. Configuration: `NTFY_URL`, `NTFY_TOPIC`, `EXPIRY_WARNING_DAYS`.

## Rigged spin

A hidden, planner-only "force the next result" control for special occasions. The requirement is that the animation is indistinguishable from a fair spin. The rig is one-shot: it is cleared after exactly one spin, and an ineligible rigged idea falls back to a fair spin.

A simpler sibling shipped first: "Pick a date (no wheel)", a planner-only dropdown (`POST /planner/pick`) that sets the pick directly and reuses the wheel's bookkeeping. It stays available for when no spin is wanted at all.

## Swipe-to-match (parked)

Both people anonymously approve or veto ideas, so the wheel pool holds only mutual yeses. Harder to build than the rest; revisit after v2.

## Standing constraints

- The repository stays free of personal information (open source, MIT).
- No accounts. Planner mode is passcode-only. The threat model is a trusted home network, see the security note in the README.
