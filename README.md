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

Set the `DEBUG_SECRET` environment variable to enable read-only diagnostic routes under `/debug`. Each request must include header `X-Debug-Secret` with the same value.

Available endpoints:

- `GET /debug/ping` → `{"status":"ok"}`
- `GET /debug/db` → database dialect and ping status
- `GET /debug/google` → whether a Google token exists in DB
- `GET /debug/amo` → whether an AmoCRM token exists and base URL configured

Example:

```bash
curl -H "X-Debug-Secret: $DEBUG_SECRET" http://localhost:8000/debug/ping
```
