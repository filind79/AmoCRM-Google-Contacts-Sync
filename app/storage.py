from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    create_engine,
    select,
)
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

Base = declarative_base()
engine = None
SessionLocal = sessionmaker(autocommit=False, autoflush=False)


def get_engine():
    global engine
    if engine is None:
        engine = create_engine(settings.db_url, future=True)
        SessionLocal.configure(bind=engine)
    return engine


def get_session():
    # Важно: перед выдачей сессии убедиться, что SessionLocal привязан к engine
    get_engine()
    return SessionLocal()


def init_db() -> None:
    """
    Одноразовая инициализация схемы БД на старте сервиса.
    Создаёт таблицы, если их ещё нет (tokens, links).
    """
    eng = get_engine()
    Base.metadata.create_all(bind=eng)


class Link(Base):
    __tablename__ = "links"

    id = Column(Integer, primary_key=True)
    amo_contact_id = Column(String, unique=True, index=True, nullable=False)
    google_resource_name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Token(Base):
    __tablename__ = "tokens"

    id = Column(Integer, primary_key=True)
    system = Column(String, index=True, nullable=False)  # 'google' or 'amocrm'
    access_token = Column(String, nullable=False)
    refresh_token = Column(String, nullable=True)
    expiry = Column(DateTime, nullable=True)
    scopes = Column(String, nullable=True)
    account_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PendingSync(Base):
    __tablename__ = "pending_sync"

    id = Column(Integer, primary_key=True)
    amo_contact_id = Column(Integer, nullable=False, unique=True, index=True)
    attempts = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_error = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def get_token(session, system: str) -> Optional[Token]:
    stmt = select(Token).where(Token.system == system)
    return session.execute(stmt).scalars().first()


def save_token(session, system: str, access_token: str, refresh_token: str, expiry: Optional[datetime], scopes: str, account_id: Optional[str] = None) -> Token:
    token = get_token(session, system)
    if token:
        token.access_token = access_token
        token.refresh_token = refresh_token
        token.expiry = expiry
        token.scopes = scopes
        token.account_id = account_id
        token.updated_at = datetime.utcnow()
    else:
        token = Token(
            system=system,
            access_token=access_token,
            refresh_token=refresh_token,
            expiry=expiry,
            scopes=scopes,
            account_id=account_id,
        )
        session.add(token)
    session.commit()
    session.refresh(token)
    return token

def get_link(session, amo_contact_id: str) -> Optional[Link]:
    stmt = select(Link).where(Link.amo_contact_id == amo_contact_id)
    return session.execute(stmt).scalars().first()


def save_link(session, amo_contact_id: str, google_resource_name: str) -> Link:
    link = get_link(session, amo_contact_id)
    if link:
        link.google_resource_name = google_resource_name
        link.updated_at = datetime.utcnow()
    else:
        link = Link(
            amo_contact_id=amo_contact_id,
            google_resource_name=google_resource_name,
        )
        session.add(link)
    session.commit()
    session.refresh(link)
    return link


def remap_google_links(
    session, target_resource_name: str, source_resource_names: Iterable[str]
) -> None:
    resources = [
        name for name in source_resource_names if name and name != target_resource_name
    ]
    if not resources:
        return
    stmt = select(Link).where(Link.google_resource_name.in_(resources))
    links = session.execute(stmt).scalars().all()
    if not links:
        return
    now = datetime.utcnow()
    for link in links:
        link.google_resource_name = target_resource_name
        link.updated_at = now
    session.commit()


def get_pending_sync(session, amo_contact_id: int) -> Optional[PendingSync]:
    stmt = select(PendingSync).where(PendingSync.amo_contact_id == amo_contact_id)
    return session.execute(stmt).scalars().first()


def enqueue_pending_sync(session, amo_contact_id: int) -> PendingSync:
    record = get_pending_sync(session, amo_contact_id)
    now = datetime.utcnow()
    if record:
        record.attempts = 0
        record.next_attempt_at = now
        record.last_error = None
        record.updated_at = now
    else:
        record = PendingSync(
            amo_contact_id=amo_contact_id,
            next_attempt_at=now,
        )
        session.add(record)
    session.commit()
    session.refresh(record)
    return record


def fetch_due_pending_sync(session, limit: int) -> list[PendingSync]:
    stmt = (
        select(PendingSync)
        .where(PendingSync.next_attempt_at <= datetime.utcnow())
        .order_by(PendingSync.next_attempt_at, PendingSync.id)
        .limit(limit)
    )
    return session.execute(stmt).scalars().all()
