from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


AwarenessFreshness = Literal["current", "stale", "missing"]


class AwarenessStateCounts(BaseModel):
    total: int = Field(default=0, ge=0)
    current: int = Field(default=0, ge=0)
    stale: int = Field(default=0, ge=0)
    historical: int = Field(default=0, ge=0)
    tombstoned: int = Field(default=0, ge=0)


class AwarenessInventorySourceHealth(BaseModel):
    source: str
    freshness: AwarenessFreshness
    snapshot_id: str | None = None
    observed_at: datetime | None = None
    age_seconds: int | None = Field(default=None, ge=0)


class AwarenessGraphHealth(BaseModel):
    tenant_id: str
    cluster: str
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    stale_after_minutes: int = Field(ge=1)
    inventory_max_age_minutes: int = Field(ge=1)
    entities: AwarenessStateCounts
    relationships: AwarenessStateCounts
    observations_total: int = Field(default=0, ge=0)
    latest_observation_at: datetime | None = None
    inventory_sources: list[AwarenessInventorySourceHealth] = Field(
        default_factory=list
    )


def inventory_source_health(
    *,
    source: str,
    snapshot_id: str | None,
    observed_at: datetime | None,
    as_of: datetime,
    max_age_minutes: int,
) -> AwarenessInventorySourceHealth:
    if observed_at is None:
        return AwarenessInventorySourceHealth(
            source=source,
            freshness="missing",
        )

    age_seconds = max(
        0,
        int((as_of - observed_at).total_seconds()),
    )
    freshness: AwarenessFreshness = (
        "current"
        if age_seconds <= max_age_minutes * 60
        else "stale"
    )
    return AwarenessInventorySourceHealth(
        source=source,
        freshness=freshness,
        snapshot_id=snapshot_id,
        observed_at=observed_at,
        age_seconds=age_seconds,
    )
