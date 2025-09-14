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

## Diagnostics

Set a shared secret in `DEBUG_SECRET` (e.g. in Render service settings) to enable read-only diagnostic endpoints. These endpoints perform no writes and can be removed or disabled in production if desired.

Example cURL calls:

```bash
curl "http://localhost:8000/debug/google/ping?key=$DEBUG_SECRET"
curl "http://localhost:8000/debug/google/contacts?limit=3&key=$DEBUG_SECRET"
curl "http://localhost:8000/debug/amo/ping?key=$DEBUG_SECRET"
curl "http://localhost:8000/debug/db/token?key=$DEBUG_SECRET"
```
