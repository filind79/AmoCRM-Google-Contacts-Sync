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

## Admin interface

The admin UI is available at `/admin` and is protected with HTTP Basic Auth.
Set `ADMIN_BASIC_USER` and `ADMIN_BASIC_PASS` in environment (see `.env.example`).
It provides pages to manage users and jobs. Remove the credentials to disable the interface (requests to `/admin` will return 404).
