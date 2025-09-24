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
| `GET /debug/amo` | AmoCRM configuration: `base_url`, `auth_mode`, `is_ready` |
| `GET /debug/ping-google` | Quick Google People API probe with latency and retry hints |

Example:

```bash
curl -H "X-Debug-Secret: $DEBUG_SECRET" http://localhost:8000/debug/db
```

`GET /debug/ping-google` performs a lightweight request against
`people/me`. The response contains `ok`, `latency_ms` and `retry_after`
fields. When the service is rate limited it returns HTTP 200 with
`{"ok": false, "error": "rate_limited", "retry_after": <seconds>}`.
A missing/expired token yields HTTP 401 with the usual
`{"detail": "Google auth required", "auth_url": "/auth/google/start"}`
payload.

## Sync API

### `/sync/contacts/dry-run`

Parameters:

* `limit` – maximum number of contacts to scan (default 50, hard capped to 20
  when `direction=both`; the response includes `limit_clamped: true` when a
  clamp happens).
* `direction` – `both` (default), `amo` or `google`.
* `since_days` – optional AmoCRM/Google filter by last update.
* `mode` – `fast` (default) fetches only the first Google People page; use
  `full` for exhaustive pagination when troubleshooting.

The response now contains diagnostic fields to aid performance tuning:
`duration_ms`, `google_requests`, `amo_requests`, `retries`, `rate_limit_hits`,
`pages_google`, `pages_amo`, as well as `limit_clamped` and `mode` echo.

### `/sync/contacts/apply`

Only `direction=to_google` is supported today. The response includes the same
diagnostic counters (`duration_ms`, `google_requests`, `amo_requests`,
`retries`, `rate_limit_hits`, `pages_*`) alongside the existing summary.

Supported filters:

- `amo_ids=ID1,ID2,...` – limit the sync to specific Amo contacts.
- `since_minutes` / `since_days` – behave the same way as in `dry-run` and can
  be combined with `amo_ids`. Filtering is applied before the final `limit` is
  enforced.

For safe manual testing prefer small batches and pauses between runs, e.g.:

```bash
http \
  :8000/sync/contacts/dry-run \
  limit==10 direction==both mode==fast

sleep 5

http \
  :8000/sync/contacts/apply \
  limit==5 since_days==14 direction==to_google confirm==1 \
  "X-Debug-Secret:$DEBUG_SECRET"
```

Run `dry-run` in `fast` mode first; switch to `mode=full` only when the quick
preview suggests mismatches that require deeper investigation.
