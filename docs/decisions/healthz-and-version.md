# Health probe and version reporting

Added 2026-07-15 as deploy plumbing.

- **`GET /healthz`** runs `SELECT 1`, so it fails with a 503 when the SQLite `/data` volume is unreachable, not only when the process is down. On success it returns `{"status": "ok", "version": APP_VERSION}`.
- **`APP_VERSION`** is a constant that can be overridden through the environment (default `1.0.0`). Set it at deploy time so `/healthz` reports exactly what is live.
- **Compose `healthcheck:`** calls `/healthz` with Python's `urllib`, because the `python:3.12-slim` base image has no curl. A `start_period` keeps gunicorn's boot time from counting as a failure.
- **`GET /version`** (added in PR #2) reports `version`, `commit_sha`, `environment` and `build_time` without touching the database.

## Rationale

A probe endpoint that also carries the version means the container health gate and "what is actually deployed" are the same check.

Related: [v2 direction](v2-direction.md)
