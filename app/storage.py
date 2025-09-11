from datetime import datetime
from typing import Optional

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
    return SessionLocal()


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
