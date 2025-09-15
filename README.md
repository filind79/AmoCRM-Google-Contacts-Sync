# AmoCRM to Google Contacts Sync

This service syncs contacts from AmoCRM to Google People API.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Create `.env` from `.env.example` and fill credentials.

## Docker

```bash
docker-compose up --build
```

## OAuth

Visit `/auth/google/start` and `/auth/amocrm/start` to complete OAuth flows.

## Tests

```bash
pytest
```

## Debug endpoints

Set `DEBUG_SECRET` to enable read‑only diagnostic routes under `/debug`. The value can be provided via environment variable or `.env` file, e.g.:

```bash
export DEBUG_SECRET=my-secret
```

Every request to these routes must include header `X-Debug-Secret` with the same value. Missing or invalid secret returns `401 {"detail": "invalid debug secret"}`.

Available endpoints:

| Endpoint | Description |
| --- | --- |
| `GET /debug/ping` | `{"status":"ok"}` |
| `GET /debug/db` | Database dialect and ping success |
| `GET /debug/google` | Google token status: `has_token`, `expires_at`, `will_refresh` |
| `GET /debug/amo` | AmoCRM token status and configured base URL |

Examples:

```bash
curl -H "X-Debug-Secret: $DEBUG_SECRET" http://localhost:8000/debug/ping
curl -H "X-Debug-Secret: $DEBUG_SECRET" http://localhost:8000/debug/db
curl -H "X-Debug-Secret: $DEBUG_SECRET" http://localhost:8000/debug/google
curl -H "X-Debug-Secret: $DEBUG_SECRET" http://localhost:8000/debug/amo
```
