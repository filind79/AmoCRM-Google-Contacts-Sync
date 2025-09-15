from typing import Any, Dict

import httpx
import logging
from datetime import datetime, timedelta

from fastapi import HTTPException

from app.storage import get_session, get_token
from app.utils import normalize_email, normalize_phone, unique
from app.config import settings


GOOGLE_API_BASE = "https://people.googleapis.com/v1"

logger = logging.getLogger(__name__)


async def get_access_token() -> str:
    session = get_session()
    try:
        token = get_token(session, "google")
        if not token:
            raise HTTPException(status_code=400, detail="google token missing")
        if token.expiry and token.expiry <= datetime.utcnow():
            if not token.refresh_token:
                raise HTTPException(status_code=401, detail="google token refresh failed")
            data = {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": token.refresh_token,
            }
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post("https://oauth2.googleapis.com/token", data=data)
                resp.raise_for_status()
                payload = resp.json()
            except Exception:
                raise HTTPException(status_code=401, detail="google token refresh failed")
            expires_in = payload.get("expires_in")
            token.access_token = payload.get("access_token")
            token.refresh_token = payload.get("refresh_token") or token.refresh_token
            token.expiry = (
                datetime.utcnow() + timedelta(seconds=expires_in)
                if expires_in is not None
                else None
            )
            session.commit()
            logger.info("Google token refreshed")
        return token.access_token
    finally:
        session.close()


async def upsert_contact_by_external_id(amo_contact_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
    token = await get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    external_id = {
        "value": str(amo_contact_id),
        "type": "AMOCRM",
    }
    url = f"{GOOGLE_API_BASE}/people:searchContacts"
    params = {"query": str(amo_contact_id), "readMask": "names,phoneNumbers,emailAddresses"}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        resource_name = None
        if results:
            resource_name = results[0]["person"]["resourceName"]

        body = {
            "names": [{"displayName": data["name"]}],
            "externalIds": [external_id],
        }
        phones = [normalize_phone(p) for p in data.get("phones", [])]
        phones = unique(phones)
        if phones:
            body["phoneNumbers"] = [{"value": p} for p in phones]
        emails = [normalize_email(e) for e in data.get("emails", [])]
        emails = unique(emails)
        if emails:
            body["emailAddresses"] = [{"value": e} for e in emails if e]

        if resource_name:
            update_url = f"{GOOGLE_API_BASE}/{resource_name}:updateContact"
            update_params = {"updatePersonFields": "names,phoneNumbers,emailAddresses"}
            resp = await client.patch(update_url, params=update_params, headers=headers, json=body)
            resp.raise_for_status()
            return resp.json()
        else:
            create_url = f"{GOOGLE_API_BASE}/people:createContact"
            resp = await client.post(create_url, headers=headers, json=body)
            resp.raise_for_status()
            return resp.json()
