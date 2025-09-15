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

Set `DEBUG_SECRET` to enable read‑only diagnostic routes under `/debug`. The value
can be provided via environment variable or `.env` file, e.g.:

```bash
export DEBUG_SECRET=my-secret
```

Every request to these routes must include header `X-Debug-Secret` with the same
value. If `DEBUG_SECRET` is unset or the header is missing/incorrect the server
responds with `404` to hide the existence of these endpoints.

Available endpoints:

| Endpoint | Description |
| --- | --- |
| `GET /debug/db` | Database connectivity and number of stored tokens |
| `GET /debug/google` | Google token status: `has_token`, `expires_at`, `scopes` |
| `GET /debug/amo` | AmoCRM base URL and whether a token is stored |

Example:

```bash
curl -H "X-Debug-Secret: $DEBUG_SECRET" http://localhost:8000/debug/db
```
