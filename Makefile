run:
uvicorn app.main:app --reload

test:
pytest

lint:
ruff app tests

migrate:
alembic upgrade head
