from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    func,
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
        engine = create_engine(
            settings.db_url,
            future=True,
            pool_pre_ping=True,
            pool_recycle=300,
        )
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


class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, nullable=False)
    value = Column(String, nullable=True)
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


def get_setting(session, key: str) -> Optional[str]:
    stmt = select(Setting).where(Setting.key == key)
    record = session.execute(stmt).scalars().first()
    return record.value if record else None


def set_settings(session, values: dict[str, Optional[str]]) -> None:
    if not values:
        return
    stmt = select(Setting).where(Setting.key.in_(values.keys()))
    existing = {setting.key: setting for setting in session.execute(stmt).scalars().all()}
    now = datetime.utcnow()
    for key, value in values.items():
        record = existing.get(key)
        if record:
            record.value = value
            record.updated_at = now
        else:
            session.add(Setting(key=key, value=value, created_at=now, updated_at=now))
    session.commit()


def set_setting(session, key: str, value: Optional[str]) -> None:
    set_settings(session, {key: value})

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


def list_pending_sync_by_contact_id(
    session, contact_id: int, limit: int = 50
) -> list[PendingSync]:
    stmt = (
        select(PendingSync)
        .where(PendingSync.amo_contact_id == contact_id)
        .order_by(PendingSync.updated_at.desc(), PendingSync.id.desc())
        .limit(limit)
    )
    return session.execute(stmt).scalars().all()


def list_recent_pending_sync(session, limit: int = 50) -> list[PendingSync]:
    stmt = (
        select(PendingSync)
        .order_by(PendingSync.updated_at.desc(), PendingSync.id.desc())
        .limit(limit)
    )
    return session.execute(stmt).scalars().all()


def get_pending_sync_stats(session) -> dict[str, int]:
    total_stmt = select(func.count(PendingSync.id))
    due_stmt = select(func.count(PendingSync.id)).where(PendingSync.next_attempt_at <= datetime.utcnow())
    total = int(session.execute(total_stmt).scalar_one() or 0)
    due = int(session.execute(due_stmt).scalar_one() or 0)
    return {"total": total, "due": due}


def get_pending_sync_health_stats(session) -> dict[str, object]:
    now = datetime.utcnow()
    pending_stmt = select(func.count(PendingSync.id)).where(PendingSync.last_error.is_(None))
    retry_stmt = select(func.count(PendingSync.id)).where(PendingSync.last_error.is_not(None))
    oldest_stmt = (
        select(PendingSync.created_at)
        .where(PendingSync.last_error.is_(None))
        .order_by(PendingSync.created_at.asc(), PendingSync.id.asc())
        .limit(1)
    )
    pending_count = int(session.execute(pending_stmt).scalar_one() or 0)
    retry_count = int(session.execute(retry_stmt).scalar_one() or 0)
    oldest_pending_created_at = session.execute(oldest_stmt).scalar_one_or_none()
    backlog_age_seconds = 0
    if pending_count > 0 and oldest_pending_created_at:
        backlog_age_seconds = max(0, int((now - oldest_pending_created_at).total_seconds()))
    return {
        "queue_pending_count": pending_count,
        "queue_retry_count": retry_count,
        "oldest_pending_created_at": oldest_pending_created_at,
        "backlog_age_seconds": backlog_age_seconds,
    }
