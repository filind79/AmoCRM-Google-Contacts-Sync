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

## Docker / Render

```bash
docker-compose up --build
```

### Render
- Dockerfile уже выполняет `alembic upgrade head` и читает порт из `${PORT}`.
- Если используете долгосрочный токен Amo:
  - установите `AMO_LONG_LIVED_TOKEN` в Environment и не проходите OAuth Amo.

## OAuth

Visit `/auth/google/start` and `/auth/amocrm/start` to complete OAuth flows.

## Tests

```bash
pytest
```
