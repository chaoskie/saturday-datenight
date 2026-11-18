model: opus
summary: The genius of saturday-datenight
---
# The genius of saturday-datenight

## Domain
A small self-hosted Flask app for couples: the planner collects date ideas, a wheel picks one, and the public page shows only a redacted version until a timed or manual reveal. SQLite storage, no accounts, one planner passcode, single container. It is a finished v2 that gets small feature and fix changes.

## Repo
- Remote: https://github.com/chaoskie/saturday-datenight (public). Default branch: master. Merges: the genius opens the pull request and the maintainer merges.
- Docs: `README.md` first (features, configuration table), then `docs/decisions/` (`v2-direction.md`, `healthz-and-version.md`) and `docs/research/`.

## Tests
- Tests: `pytest` (the suite is `tests/test_app.py`; dependencies in `requirements-dev.txt`).
- Lint and types: none configured.
- CI: no workflow under `.github/`, so the tests are only run by hand.

## Lares
Only these serve here. One line each on when you use it in this project.
- `lar-developer`: changes in `app.py` (routes, wheel weighting, reveal logic), `templates/`, and the tests in `tests/test_app.py`.
- `lar-reviewer`: reads every diff, with extra attention to anything that could leak a hidden pick through `redacted()` or the public page.
- `lar-security`: the secrecy of the pick is the core feature, and the planner passcode, session handling, rigged spin and the outgoing ntfy request all sit on that edge.
- `lar-tester`: the wheel odds (`idea_weight`), auto-reveal timing and expiry ping have date and state logic that needs a coverage plan before it changes.
