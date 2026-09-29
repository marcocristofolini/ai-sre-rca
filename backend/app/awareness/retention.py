from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AwarenessRetentionResult(BaseModel):
    tenant_id: str
    cluster: str
    dry_run: bool
    as_of: datetime
    observation_retention_days: int = Field(ge=1)
    tombstone_retention_days: int = Field(ge=1)
    observation_cutoff: datetime
    tombstone_cutoff: datetime

    observations_eligible: int = Field(default=0, ge=0)
    observations_deleted: int = Field(default=0, ge=0)
    latest_observations_preserved: int = Field(default=0, ge=0)
    inventory_receipts_preserved: int = Field(default=0, ge=0)

    tombstoned_relationships_eligible: int = Field(default=0, ge=0)
    tombstoned_relationships_deleted: int = Field(default=0, ge=0)
    tombstoned_entities_eligible: int = Field(default=0, ge=0)
    tombstoned_entities_deleted: int = Field(default=0, ge=0)
    tombstoned_entities_blocked: int = Field(default=0, ge=0)
