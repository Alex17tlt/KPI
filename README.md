# Seven Group — KPI (MVP)

FastAPI + SQLite + Jinja2. KPI rules are versioned JSON (`app/rules/*.json`), interpreted by `app/engine`.

## Run locally
```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # set SECRET_KEY, ADMIN_EMAIL, ADMIN_PASSWORD
pytest -q                   # engine tests (port of the original 26 scenarios)
uvicorn app.main:app --reload
```
Open http://localhost:8000 and sign in with ADMIN_EMAIL / ADMIN_PASSWORD.
Without RESEND_API_KEY, emails (reset links) are printed to the server log.

## Start fresh
`rm -f data/kpi.db*` and restart: admin and the Finance department (notion5) are recreated.
Open the first month on the Finance page (/d/finance). After that, each new month opens automatically.

## Before every deploy
```bash
pytest -q && python -m scripts.check_rules
```
`check_rules` validates every department's live rules and recalculates every saved month with the current engine.

## Deploy (VPS)
- `uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --workers 1` under systemd.
- nginx with HTTPS in front; set `BASE_URL=https://…` and `COOKIE_SECURE=true`.
- Back up `data/kpi.db` (e.g. `sqlite3 data/kpi.db ".backup backup.db"` daily).
- Keep 1 worker while on SQLite + in-memory rate limiting.
