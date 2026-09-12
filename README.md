# vinyl_app

Streamlit application for the Vinyl catalog. This repository is one half of the `Vinyl` GitHub Project; the independent FastAPI service lives in `vinyl_api`.

## Local development

Create a Python environment and install the dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
streamlit run app.py
```

The app stores its local data under `VINYL_DATA_DIR`, which defaults to the repository directory. Keep `.env` and local databases out of Git.

Run the API separately from the `vinyl_api` repository:

```powershell
$env:VINYL_API_URL = 'http://127.0.0.1:8003'
$env:VINYL_API_KEY = ''
python -m uvicorn vinyl_api.main:app --reload --port 8003
```

For the deployed app, set `VINYL_API_URL` to `https://vinyl-api.fly.dev` and set the same `VINYL_API_KEY` on both Fly applications.

## Refresh jobs

Run a complete refresh and alert cycle from any working directory:

```powershell
python scripts/daily_refresh_and_alerts.py
```

The Windows-only wrapper is available at `scripts/run_daily_refresh.ps1`. Set `PROJECT_ROOT` when invoking the scripts from a separate scheduler.

## Deployment

The Streamlit Fly.io application is `vinyl-catalog`. Its deployment configuration is in `fly.toml` and uses the `vinyl_data` persistent volume mounted at `/data`.

```powershell
fly deploy
```

Set Discogs, SMTP, registration, and API credentials through Fly secrets. Never commit `.env` files, database files, or tokens.
