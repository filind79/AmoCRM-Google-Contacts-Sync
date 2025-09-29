from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.services.merge import MissingEtagError
from app.services.sync_engine import SyncEngine, SyncPlan, SyncResult


@dataclass(slots=True)
class ProcessResult:
    action: str
    resource_name: Optional[str]
    reason: Optional[List[str]] = None


class GoogleApplyService(SyncEngine):
    async def process_contact(self, contact: Dict[str, Any]) -> ProcessResult:
        plan = await self.plan(contact)
        result = await self.apply(plan)
        return _convert_result(result)

    async def merge_candidates(
        self,
        keys,
        *,
        amo_contact_id: Optional[int] = None,
        mapped_resource: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await super().merge_candidates(
            keys,
            amo_contact_id=amo_contact_id,
            mapped_resource=mapped_resource,
        )


def _convert_result(result: SyncResult) -> ProcessResult:
    reason: Optional[List[str]]
    if result.reason is None:
        reason = None
    elif isinstance(result.reason, list):
        reason = result.reason
    else:
        reason = [result.reason]
    return ProcessResult(result.action, result.resource_name, reason)


async def apply_contact(contact: Dict[str, Any]) -> ProcessResult:
    service = GoogleApplyService()
    try:
        plan = await service.plan(contact)
        result = await service.apply(plan)
        return _convert_result(result)
    finally:
        service.close()


__all__ = [
    "GoogleApplyService",
    "MissingEtagError",
    "ProcessResult",
    "SyncPlan",
    "SyncResult",
]
