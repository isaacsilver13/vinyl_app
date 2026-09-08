# Vinyl App Copilot Instructions

## Project boundary

This repository is the canonical Streamlit application for the Vinyl project. The independent FastAPI service lives in the sibling `repos/vinyl_api` repository. The legacy workspace copy under `vinyl/` is not the source of truth for new changes.

## Runtime and deployment

- The Streamlit entrypoint is `app.py`; the production Fly app is `vinyl-catalog` and listens on port 8501.
- The app depends on `https://vinyl-api.fly.dev` in production. API deployment and health belong to `repos/vinyl_api`.
- Fly mounts the `vinyl_data` volume at `/data`; do not assume the production SQLite/database state is disposable.
- GitHub Actions deploys the app from `main`. Local deployment is an explicit diagnostic/release operation, not a replacement for the normal push-to-main workflow.
- Use `scripts/daily_refresh_and_alerts.py` or `scripts/run_daily_refresh.ps1` for the existing refresh workflow. Preserve listing and enrichment-cache reuse.

## Secrets and data

- Keep `.env`, Discogs tokens, API keys, OAuth credentials, SMTP credentials, and Fly tokens out of source files, command output, and chat.
- Do not commit SQLite databases, local refresh artifacts, or generated logs.
- Confirm whether a refresh is app-only, API-backed, or both before changing service code.

## Working method

Inspect the changed files, current branch, workflow configuration, and deployment target before editing. Prefer the smallest service-local change. Run focused tests or status/health checks first, then broader checks. Do not claim a Fly deployment, refresh, OAuth flow, or alert delivery succeeded without current evidence.
